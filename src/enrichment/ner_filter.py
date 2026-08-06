# -----------------------------------------------------------
# NER post-detection filtering and entity-level deduplication.
# -----------------------------------------------------------
"""
NER noise filters.

Three filter layers, applied at different pipeline stages:

1. **Span-level** (after model inference, before alignment):
   Text-based checks — minimum length, digits, punctuation, title match,
   all-caps OCR garbage heuristic.

2. **POS-based** (after alignment, before overlap resolution):
   Uses the UPOS ``@pos`` attributes already on ``<w>`` elements from
   the linguistic enrichment phase.  Language-agnostic: no stopword lists
   required.  A person/place/org whose every word is a VERB, DET, PRON,
   ADJ, etc. is rejected.

3. **Entity-level** (after resolution/grouping):
   Fixes duplicated canonical names ("bonus bonus"), merges spelling
   variants via fuzzy matching, and prunes single-mention low-confidence
   entities.
"""

import logging
import re
import unicodedata
from collections import Counter
from difflib import SequenceMatcher

from ..utils.xml import local_tag as _local

logger = logging.getLogger(__name__)

# Entity types where strict name-plausibility filters apply
_NAMED_TYPES = frozenset({"person", "place", "organization"})

# ── Constants ────────────────────────────────────────────────────────

MIN_ENTITY_LENGTH = 2   # single characters are never valid entities
MIN_NAMED_LENGTH = 3    # PER/LOC/ORG need at least 3 chars

# ── POS-based filtering ─────────────────────────────────────────────
# Supports both UPOS tags and the extended PyHellen/LASLA tagsets
# used by this pipeline:
#   PyHellen:  NOMpro, NOMcom, VERcjg, VERinf, DETdef, PRE, PROper, ADJqua, CONsub …
#   LASLA (Latin single-letter): n=noun, v=verb, a=adj, d=adv, r=prep, c=conj, p=pron …
#   UPOS (fallback):  PROPN, NOUN, VERB, DET, ADP …

# Single-letter Latin tags that map to non-entity POS
_LATIN_NON_ENTITY = frozenset({"v", "a", "d", "r", "c", "p", "l", "i"})

# Prefixes of PyHellen tags that indicate non-entity words
_NON_ENTITY_PREFIXES = ("VER", "DET", "PRE", "PRO", "ADJ", "ADV", "CON", "INJ", "PON")

# UPOS fallback set
_NON_ENTITY_UPOS = frozenset({
    "VERB", "AUX", "ADJ", "ADV", "DET", "PRON", "ADP",
    "CCONJ", "SCONJ", "PART", "INTJ", "NUM",
})


def _is_non_entity_pos(pos):
    """True if the POS tag indicates a word that is never an entity."""
    if not pos or pos in ("UNK", "_", "-", "FOR"):
        return False  # unknown / foreign → don't filter
    if pos in _LATIN_NON_ENTITY:
        return True
    if pos.upper().startswith(_NON_ENTITY_PREFIXES):
        return True
    if pos in _NON_ENTITY_UPOS:
        return True
    return False


def _is_proper_noun(pos):
    """True if the POS tag indicates a proper noun."""
    return pos in ("NOMpro", "PROPN")


def _is_unknown_pos(pos):
    """True if POS is unknown or unset."""
    return not pos or pos in ("UNK", "_", "-", "FOR", "u")

# Latin/French common nouns that POS taggers sometimes mark PROPN
# because they are frequently capitalised in historical texts.
# Used as extra check for single-word PER entities only.
_FALSE_PROPN = frozenset({
    "deus", "doctor", "episcopus", "papa", "rex", "imperator",
    "dominus", "apostolus", "theologus", "sanctus", "beatus",
    "princeps", "comes", "dux", "pontifex",
})

# ── Regex patterns ──────────────────────────────────────────────────

_HAS_DIGIT = re.compile(r"\d")
_BAD_PUNCT = re.compile(r"[,;:!?/\\{}()\[\]<>@]")

# Greek Unicode range (basic + extended blocks)
_IS_GREEK = re.compile(
    r"^[\u0370-\u03FF\u1F00-\u1FFF\u0300-\u036F\s,;·᾽ʼ]+$"
)
# Common Greek verb endings
_GREEK_VERB_ENDINGS = re.compile(
    r"(ω|μι|μαι|ομαι|εται|ονται|νται|ειν|αι|ναι|σθαι"
    r"|ούμαι|είν|ούν|ών|ῶν|ῆς|ές|ός|ῶ|εῖν|οῦν|υμι|ννυμι|νυμι)$"
)


# ── Normalisation helpers ────────────────────────────────────────────

def _normalize(text):
    """Lowercase, strip accents, collapse spaces."""
    text = text.lower().strip()
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    return " ".join(stripped.split())


# ── Title extraction ─────────────────────────────────────────────────

def extract_title_from_tei(root):
    """
    Extract the main document title from ``<titleStmt>`` in the TEI header.

    Returns:
        str: title text, or empty string.
    """
    for elem in root.iter():
        if _local(elem.tag) == "titleStmt":
            for child in elem:
                if _local(child.tag) == "title":
                    return "".join(child.itertext()).strip()
    return ""


# =====================================================================
# 1. SPAN-LEVEL FILTERS  (Phase 7 → before alignment)
# =====================================================================

def filter_spans(spans, title=None):
    """
    Filter NER spans to remove obvious noise.

    Args:
        spans: list of NERSpan.
        title: optional document title for title-matching filter.

    Returns:
        list of NERSpan: filtered spans.
    """
    if not spans:
        return []

    norm_title = _normalize(title) if title else None
    title_words = set(norm_title.split()) if norm_title else set()

    filtered = []
    reasons = Counter()

    for span in spans:
        reason = _check_span(span, norm_title, title_words)
        if reason:
            reasons[reason] += 1
            logger.debug(
                "NER span filter: '%s' (%s, %.2f, %s) → %s",
                span.text, span.entity_type, span.confidence, span.model, reason,
            )
        else:
            filtered.append(span)

    if reasons:
        total = sum(reasons.values())
        detail = ", ".join(f"{r}: {c}" for r, c in reasons.most_common())
        logger.info("NER span filter: rejected %d/%d (%s)", total, total + len(filtered), detail)

    return filtered


def _check_span(span, norm_title, title_words):
    """Return a rejection reason string, or None to keep the span."""
    text = span.text.strip()

    # ── 1. Minimum length ───────────────────────────────────────────
    if len(text) < MIN_ENTITY_LENGTH:
        return "too_short"

    # ── 2. Punctuation inside entity ────────────────────────────────
    if _BAD_PUNCT.search(text):
        return "bad_punct"

    norm = _normalize(text)
    words = norm.split()
    is_named = span.entity_type in _NAMED_TYPES

    # ── 3. Minimum 3 chars for all entity types ────────────────────
    if len(norm) < MIN_NAMED_LENGTH:
        return "too_short_named"

    # ── 4. Digits mixed with letters ────────────────────────────────
    if _HAS_DIGIT.search(text):
        return "has_digits"

    # ── 5. Greek verb / short Greek fragment ─────────────────────────
    if span.entity_type == "person" and _IS_GREEK.match(text):
        if len(words) == 1 and _GREEK_VERB_ENDINGS.search(text):
            return "greek_verb"
        if len(norm) <= 4:
            return "greek_short"

    # ── 6. All-caps single word ≥ 7 chars, low confidence ───────────
    # Targets OCR garbage from corrupted running titles/headers.
    if len(words) == 1 and len(text) >= 7:
        if text.isupper() and span.confidence < 0.70:
            return "allcaps_low_conf"

    # ── 7. Entity text matches the document title ───────────────────
    if is_named and norm_title and len(norm) >= 4:
        if norm == norm_title:
            return "title_exact"
        # Single word that is one of the content words of the title
        if len(words) == 1 and len(norm) >= 5 and norm in title_words:
            return "title_word"

    return None


# =====================================================================
# 2. POS-BASED FILTERS  (Phase 8 → after alignment, before overlap)
# =====================================================================

def filter_aligned_by_pos(entities):
    """
    Filter aligned entities using UPOS ``@pos`` tags on ``<w>`` elements.

    Language-agnostic: works for French, Latin, Greek, German, etc.
    without any stopword list.

    Rules (applied in order):

    1. PER/LOC/ORG where *all* words carry non-entity POS → reject.
    2. PER: must have at least one PROPN (or one word with unknown POS).
       Catches "le philosophe" (DET+NOUN), "philosophe indifférent"
       (NOUN+ADJ), "docteur subtil" (NOUN+ADJ), etc.
    3. PER single-word: check against false-PROPN Latin nouns.
    4. LOC/ORG single-word: must be PROPN or NOUN (not ADJ, not unknown
       that looks like garbage).

    Args:
        entities: list of AlignedEntity.

    Returns:
        list of AlignedEntity: kept entities.
    """
    if not entities:
        return []

    kept = []
    rejected = 0

    for ent in entities:
        if not ent.w_elements:
            # Raw-text entity — no POS info, keep (other filters handle it)
            kept.append(ent)
            continue

        pos_tags = [w.get("pos", "") for w in ent.w_elements]
        lemmas = [w.get("lemma", "") or "" for w in ent.w_elements]

        # ── Rule 0: All words flagged as foreign language → reject ─
        # Foreign-language detector marks tokens with sentinel lemmas
        # like "@latin", "@italian", etc. These are noise, not entities.
        if lemmas and all(lem.startswith("@") for lem in lemmas):
            logger.debug(
                "POS filter: '%s' (%s) → all-foreign lemmas %s",
                ent.text, ent.entity_type, lemmas,
            )
            rejected += 1
            continue

        # ── Rule 1: All words carry a non-entity POS ───────────────
        # Applies to EVERY entity type.  A DET/PRE/VER/CON/PRO is
        # never an entity of any kind.
        evaluable = [p for p in pos_tags if not _is_unknown_pos(p)]
        if evaluable and all(_is_non_entity_pos(p) for p in evaluable):
            logger.debug(
                "POS filter: '%s' (%s) → all non-entity POS %s",
                ent.text, ent.entity_type, evaluable,
            )
            rejected += 1
            continue

        # ── Rule 2: PER must contain at least one NOMpro ────────────
        # A person name always contains a proper noun.  Descriptive
        # phrases like "le philosophe" (DETdef + NOMcom),
        # "philosophe indifférent" (NOMcom + ADJqua) have no NOMpro.
        if ent.entity_type == "person":
            has_propn = any(_is_proper_noun(p) for p in pos_tags)
            has_unknown = any(_is_unknown_pos(p) for p in pos_tags)
            if not has_propn and not has_unknown:
                logger.debug(
                    "POS filter: PER '%s' → no NOMpro in %s",
                    ent.text, pos_tags,
                )
                rejected += 1
                continue

        # ── Rule 3: PER single-word false-PROPN ─────────────────────
        if ent.entity_type == "person" and len(ent.w_elements) == 1:
            lemma = (ent.w_elements[0].get("lemma") or "").lower()
            if lemma in _FALSE_PROPN:
                logger.debug(
                    "POS filter: PER '%s' → false PROPN (lemma=%s)",
                    ent.text, lemma,
                )
                rejected += 1
                continue

        # ── Rule 4: Single-word ADJ → reject for all types ─────────
        # "ancien" (ADJqua), "théologique", "morales", "tout", "autre"
        if len(ent.w_elements) == 1:
            pos = pos_tags[0]
            pos_upper = pos.upper() if pos else ""
            if _is_non_entity_pos(pos) and (
                pos_upper.startswith("ADJ") or pos == "a"
            ):
                logger.debug(
                    "POS filter: %s '%s' → single-word ADJ (%s)",
                    ent.entity_type, ent.text, pos,
                )
                rejected += 1
                continue

        kept.append(ent)

    if rejected:
        logger.info("NER POS filter: rejected %d/%d entities", rejected, rejected + len(kept))

    return kept


# =====================================================================
# 3. ENTITY-LEVEL FILTERS  (Phase 9 → after grouping)
# =====================================================================

def fix_canonical_names(entities):
    """
    Fix duplicated-word canonical names like ``"bonus bonus"`` → ``"bonus"``.

    Happens when consecutive ``<w>`` elements share the same ``@lemma``
    and the NER model spans both.

    Args:
        entities: list of ResolvedEntity (modified in place).
    """
    fixed = 0
    for ent in entities:
        words = ent.canonical_name.split()
        if len(words) < 2:
            continue

        # All words identical: "bonus bonus bonus" → "bonus"
        if len(set(w.lower() for w in words)) == 1:
            ent.canonical_name = words[0]
            fixed += 1
            continue

        # Repeated pattern: "abc def abc def" → "abc def"
        n = len(words)
        for chunk in range(1, n // 2 + 1):
            if n % chunk != 0:
                continue
            block = words[:chunk]
            if all(words[i:i + chunk] == block for i in range(0, n, chunk)):
                ent.canonical_name = " ".join(block)
                fixed += 1
                break

    if fixed:
        logger.info("NER filter: fixed %d duplicated canonical names", fixed)


def filter_resolved_entities(entities, min_confidence_single=0.55):
    """
    Prune single-mention entities with low confidence.

    Entities mentioned several times are kept regardless (repeated
    detection is itself a signal).

    Args:
        entities: list of ResolvedEntity.
        min_confidence_single: confidence floor for 1-mention entities.

    Returns:
        list of ResolvedEntity.
    """
    kept = []
    pruned = 0

    for ent in entities:
        if len(ent.mentions) == 1:
            best = max((m.confidence for m in ent.mentions), default=0)
            if best < min_confidence_single:
                logger.debug(
                    "Pruned '%s' (%s, conf=%.2f, 1 mention)",
                    ent.canonical_name, ent.entity_type, best,
                )
                pruned += 1
                continue
        kept.append(ent)

    if pruned:
        logger.info("NER filter: pruned %d low-confidence single-mention entities", pruned)
    return kept


# =====================================================================
# FUZZY DEDUPLICATION  (Phase 9 → after grouping)
# =====================================================================

def fuzzy_merge_entities(entities, similarity_threshold=0.78, min_name_length=4):
    """
    Merge resolved entities whose canonical names are spelling variants.

    Uses ``SequenceMatcher`` to catch OCR/orthographic variants like
    *Tertulus / tertules / Tertullies*, *Theologus / Theclogius*.

    Only merges entities of the same type.

    Args:
        entities: list of ResolvedEntity.
        similarity_threshold: min SequenceMatcher ratio to merge.
        min_name_length: skip fuzzy matching on shorter names.

    Returns:
        list of ResolvedEntity.
    """
    if len(entities) <= 1:
        return entities

    by_type = {}
    for ent in entities:
        by_type.setdefault(ent.entity_type, []).append(ent)

    result = []
    total_merges = 0

    for _etype, group in by_type.items():
        merged = _fuzzy_merge_group(group, similarity_threshold, min_name_length)
        total_merges += len(group) - len(merged)
        result.extend(merged)

    if total_merges:
        logger.info("NER fuzzy dedup: merged %d spelling-variant groups", total_merges)

    return result


def _fuzzy_merge_group(entities, threshold, min_length):
    """Union-Find merge of similar entities within a same-type group."""
    if len(entities) <= 1:
        return entities

    norms = [_normalize(e.canonical_name) for e in entities]

    # ── Union-Find ──────────────────────────────────────────────────
    parent = list(range(len(entities)))

    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    def union(a, b):
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[ra] = rb

    for i in range(len(entities)):
        if len(norms[i]) < min_length:
            continue
        for j in range(i + 1, len(entities)):
            if len(norms[j]) < min_length:
                continue
            if find(i) == find(j):
                continue
            if SequenceMatcher(None, norms[i], norms[j]).ratio() >= threshold:
                union(i, j)

    # ── Collect groups ──────────────────────────────────────────────
    groups = {}
    for i in range(len(entities)):
        groups.setdefault(find(i), []).append(i)

    result = []
    for indices in groups.values():
        if len(indices) == 1:
            result.append(entities[indices[0]])
            continue

        # Representative = most mentions
        best_idx = max(indices, key=lambda i: len(entities[i].mentions))
        best = entities[best_idx]

        for idx in indices:
            if idx == best_idx:
                continue
            other = entities[idx]
            best.mentions.extend(other.mentions)
            if not best.local_match and other.local_match:
                best.local_match = other.local_match
            logger.debug("Fuzzy merged '%s' into '%s'", other.canonical_name, best.canonical_name)

        result.append(best)

    return result
