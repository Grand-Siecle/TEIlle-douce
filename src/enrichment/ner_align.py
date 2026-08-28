# -----------------------------------------------------------
# Phase 8: NER span alignment, cross-model merge, and XML injection.
# -----------------------------------------------------------
"""
NER alignment and injection pipeline (Phase 8).

Aligns NER spans to XML nodes, merges results from CamemBERT and GLiNER,
resolves overlaps, and injects entity annotations.

Three alignment cases:
1. CamemBERT on <orig>: char offsets → <w> elements (wrap <w> in <orig>)
2. GLiNER on <reg>: char offsets → list of (<reg>, start, end) fragments
   • Single fragment  → mixed-content wrap inside <reg>
   • Multi fragments  → one wrapper per <reg>, linked via @xml:id+@next/@prev
3. GLiNER on raw text: char offsets → text node splitting

Dual anchoring: entities carrying both <w> anchors and <reg> fragments are
injected on BOTH layers (<orig> and <reg>), so annotations stay visible on
the diplomatic and the modernized views alike. Orig-only entities under a
<choice> get their <reg> fragments backfilled (backfill_reg_fragments)
before injection. Both copies receive the same @ref in Phase 9.
"""

import logging
import uuid
from dataclasses import dataclass, field

from lxml import etree

from ..constants import NS_TEI, XML_ID
from ..utils.xml import local_tag as _local
from .ner_filter import filter_aligned_by_pos

logger = logging.getLogger(__name__)

TEI_NS = f"{{{NS_TEI}}}"


# =============================================================================
# DATACLASSES
# =============================================================================


@dataclass
class AlignedEntity:
    """An entity aligned to specific XML nodes."""

    entity_type: str
    text: str
    confidence: float
    model: str  # "camembert" | "gliner" | "both"
    # For tokenized blocks: consecutive <w> elements to wrap
    w_elements: list = field(default_factory=list)
    # For raw text blocks: parent element + char offsets in its text content
    text_node: object = None
    text_start: int = None
    text_end: int = None
    # Anchors inside <reg> (mixed-content injection). List of fragments. One
    # fragment = single-line entity. Multiple fragments = entity split across
    # consecutive <reg> elements (multi-line), linked via @next/@prev at
    # injection time. Dual anchoring: when both reg_fragments and w_elements
    # are set, the entity is injected in <reg> AND in <orig>.
    reg_fragments: list = field(default_factory=list)  # list[(reg_elem, start, end)]


# =============================================================================
# SPAN → NODE ALIGNMENT
# =============================================================================


def _align_orig_spans(block, spans):
    """
    Align NER spans from <orig> text to <w> elements.

    Case 1 (CamemBERT) and Case 2b (non-French GLiNER on orig).
    Uses the char_to_w mapping built during extraction.
    """
    aligned = []

    for span in spans:
        covered_w = []
        for w_start, w_end, w_elem in block.char_to_w:
            # Check overlap between span and word
            if w_start < span.end_char and w_end > span.start_char:
                covered_w.append(w_elem)

        if not covered_w:
            logger.debug(
                "Span '%s' (%s) could not be aligned to any <w>",
                span.text,
                span.entity_type,
            )
            continue

        aligned.append(
            AlignedEntity(
                entity_type=span.entity_type,
                text=span.text,
                confidence=span.confidence,
                model=span.model,
                w_elements=covered_w,
            )
        )

    return aligned


def _align_reg_spans(block, spans):
    """
    Align NER spans from <reg> text to (a) the <reg> elements they cover for
    in-place mixed-content injection and (b) the <w> elements in the sibling
    <orig> for POS filtering and canonical-name extraction.

    Case 2 (GLiNER on French <reg>). Returns one AlignedEntity per span;
    multi-<reg> spans yield a single entity with multiple reg_fragments,
    linked at injection time via @next/@prev.
    """
    aligned = []

    for span in spans:
        # Find which <reg> element(s) this span falls in
        covered_regs = []
        for reg_start, reg_end, reg_elem in block.char_to_reg:
            if reg_start < span.end_char and reg_end > span.start_char:
                # Compute local offsets within this <reg>
                local_start = max(0, span.start_char - reg_start)
                local_end = min(reg_end - reg_start, span.end_char - reg_start)
                covered_regs.append((reg_elem, local_start, local_end))

        if not covered_regs:
            continue

        # For each covered <reg>, find corresponding <w> in sibling <orig>
        all_w = []
        for reg_elem, local_start, local_end in covered_regs:
            choice = reg_elem.getparent()
            if choice is None:
                continue

            # Find <orig> sibling
            orig = None
            for child in choice:
                if _local(child.tag) == "orig":
                    orig = child
                    break
            if orig is None:
                continue

            # Get the span text within this <reg>
            reg_text = reg_elem.text or ""

            # Collect all <w> from <orig> (across <s> elements)
            w_elems = [e for e in orig.iter() if _local(e.tag) == "w"]

            # Tokenize <reg> text to find positional correspondence
            reg_words = reg_text.split()
            if not reg_words or not w_elems:
                continue

            # Find which word indices in <reg> the span covers
            char_pos = 0
            span_word_indices = []
            for wi, word in enumerate(reg_words):
                word_start = reg_text.find(word, char_pos)
                word_end = word_start + len(word)
                if word_start < local_end and word_end > local_start:
                    span_word_indices.append(wi)
                char_pos = word_end

            # Map word indices to <w> elements (positional)
            for wi in span_word_indices:
                if wi < len(w_elems):
                    all_w.append(w_elems[wi])
                else:
                    # Fallback: try matching by @norm or @lemma
                    if wi < len(reg_words):
                        target = reg_words[wi].lower()
                        for w in w_elems:
                            norm = (w.get("norm") or w.get("lemma") or "").lower()
                            if norm == target:
                                all_w.append(w)
                                break

        if not all_w:
            logger.debug(
                "Span '%s' (%s) from <reg> could not be mapped to <w>",
                span.text,
                span.entity_type,
            )
            continue

        # Build reg_fragments list: one per covered <reg>. Sorted by document
        # order via covered_regs ordering (which matches block.char_to_reg).
        # Multi-fragment entities get linked via @next/@prev at injection time.
        reg_fragments = list(covered_regs)

        aligned.append(
            AlignedEntity(
                entity_type=span.entity_type,
                text=span.text,
                confidence=span.confidence,
                model=span.model,
                w_elements=all_w,
                reg_fragments=reg_fragments,
            )
        )

    return aligned


def _align_raw_spans(block, spans):
    """
    Align NER spans from raw text to the container element.

    Case 3: text node splitting required during injection.
    """
    aligned = []
    for span in spans:
        aligned.append(
            AlignedEntity(
                entity_type=span.entity_type,
                text=span.text,
                confidence=span.confidence,
                model=span.model,
                text_node=block.container,
                text_start=span.start_char,
                text_end=span.end_char,
            )
        )
    return aligned


def align_spans_to_nodes(block, spans):
    """
    Align NER spans to XML nodes based on block source type.

    Args:
        block: NERBlock with source info and char mappings.
        spans: List of NERSpan detected on this block.

    Returns:
        list[AlignedEntity]: Entities aligned to XML nodes.
    """
    if not spans:
        return []

    if block.source == "orig":
        return _align_orig_spans(block, spans)
    elif block.source == "reg":
        return _align_reg_spans(block, spans)
    elif block.source == "raw":
        return _align_raw_spans(block, spans)
    else:
        logger.warning("Unknown block source: %s", block.source)
        return []


# =============================================================================
# CROSS-MODEL MERGE (FRENCH)
# =============================================================================


def _w_key(w_elem):
    """Unique identity for a <w> element (by id() in memory)."""
    return id(w_elem)


def merge_model_results(camembert_aligned, gliner_aligned):
    """
    Merge CamemBERT and GLiNER results for the same French container.

    Two entities are considered the same mention when their <w> sets have a
    Jaccard ratio strictly above 0.5 -- not when the sets are identical.

    Rules:
    - Overlapping + same type → merge, boost confidence, model="both"
    - Overlapping + different types → keep both, on purpose (see below)
    - Below the ratio → both are kept too

    In the last two cases the arbitration is deliberately left to the two steps
    that follow in align_and_inject(): filter_aligned_by_pos() then
    resolve_overlaps(). That order matters. Several POS rules are
    type-specific -- a "person" needs a proper noun, for instance -- so the
    more confident of two competing annotations can be rejected while the other
    survives. Keeping both here is what lets the surviving one through:
    dropping the loser early would lose the mention entirely.

    resolve_overlaps() then keeps at most one annotation per <w>, by
    confidence, regardless of type. A span can therefore never carry two types
    at once in the output; doing so would require standoff annotation, since
    two XML elements cannot overlap on the same words (audit #1.9).

    Args:
        camembert_aligned: AlignedEntities from CamemBERT on <orig>.
        gliner_aligned: AlignedEntities from GLiNER on <reg>.

    Returns:
        list[AlignedEntity]: Merged entities.
    """
    if not camembert_aligned and not gliner_aligned:
        return []
    if not camembert_aligned:
        return list(gliner_aligned)
    if not gliner_aligned:
        return list(camembert_aligned)

    merged = []
    used_gliner = set()

    for cam_ent in camembert_aligned:
        cam_w_set = frozenset(_w_key(w) for w in cam_ent.w_elements)

        best_match = None
        best_overlap = 0

        for gi, gli_ent in enumerate(gliner_aligned):
            if gi in used_gliner:
                continue
            if not gli_ent.w_elements:
                continue

            gli_w_set = frozenset(_w_key(w) for w in gli_ent.w_elements)
            overlap = len(cam_w_set & gli_w_set)

            if overlap > best_overlap:
                best_overlap = overlap
                best_match = gi

        if best_match is not None:
            gli_ent = gliner_aligned[best_match]
            gli_w_set = frozenset(_w_key(w) for w in gli_ent.w_elements)
            union_size = len(cam_w_set | gli_w_set)
            overlap_ratio = best_overlap / union_size if union_size > 0 else 0

            if overlap_ratio > 0.5 and cam_ent.entity_type == gli_ent.entity_type:
                # Same entity, same type → merge with confidence boost.
                # Keep both anchor sets (CamemBERT <w> + GLiNER <reg>) for
                # dual injection.
                used_gliner.add(best_match)
                merged.append(
                    AlignedEntity(
                        entity_type=cam_ent.entity_type,
                        text=cam_ent.text,
                        confidence=min(1.0, max(cam_ent.confidence, gli_ent.confidence) + 0.1),
                        model="both",
                        w_elements=cam_ent.w_elements,
                        reg_fragments=list(gli_ent.reg_fragments),
                    )
                )
            elif overlap_ratio > 0.5 and cam_ent.entity_type != gli_ent.entity_type:
                # Same words, different types → keep both, deliberately.
                #
                # Only one of them can reach the output: resolve_overlaps()
                # keeps at most one annotation per <w>. But filter_aligned_by_pos()
                # runs BEFORE it, and several of its rules are type-specific
                # (a "person" must carry a proper noun, etc.). The more
                # confident annotation can therefore be rejected while the
                # other one passes. Keeping both here is what lets that other
                # one survive -- e.g. "Apollon" tagged NOMcom: the "person"
                # reading is rejected by the POS rules, and only because the
                # "artwork" reading was kept alongside does the mention survive
                # at all. Dropping the loser here loses such mentions entirely.
                #
                # Verified empirically; do not "simplify" this branch.
                used_gliner.add(best_match)
                merged.append(cam_ent)
                merged.append(
                    AlignedEntity(
                        entity_type=gli_ent.entity_type,
                        text=cam_ent.text,
                        confidence=gli_ent.confidence,
                        model=gli_ent.model,
                        w_elements=cam_ent.w_elements,  # use CamemBERT's w mapping
                        reg_fragments=list(gli_ent.reg_fragments),
                    )
                )
            else:
                # Low overlap → probably two distinct mentions sharing a word.
                # CamemBERT's is kept here; the GLiNER one is NOT marked as
                # used, so it is re-added by the unmatched loop below. Both
                # therefore reach resolve_overlaps(), which arbitrates by
                # confidence. (The former comment claimed only CamemBERT's
                # survived, which was never true.)
                merged.append(cam_ent)
        else:
            merged.append(cam_ent)

    # Add unmatched GLiNER entities
    for gi, gli_ent in enumerate(gliner_aligned):
        if gi not in used_gliner:
            merged.append(gli_ent)

    return merged


# =============================================================================
# OVERLAP RESOLUTION
# =============================================================================


def resolve_overlaps(entities):
    """
    Resolve overlapping entities using greedy selection by confidence.

    For tokenized entities: two entities overlap if they share any <w>.
    For raw text entities: two entities overlap if their char ranges intersect.

    Args:
        entities: List of AlignedEntity.

    Returns:
        list[AlignedEntity]: Non-overlapping entities.
    """
    if not entities:
        return []

    # Sort by confidence descending
    sorted_ents = sorted(entities, key=lambda e: e.confidence, reverse=True)
    accepted = []
    used_w = set()  # set of _w_key for claimed <w> elements
    used_ranges = []  # (text_node_id, start, end) for raw text

    for ent in sorted_ents:
        if ent.w_elements:
            ent_w_keys = {_w_key(w) for w in ent.w_elements}
            if ent_w_keys & used_w:
                logger.debug(
                    "Overlap rejected: '%s' (%s, %.2f)",
                    ent.text,
                    ent.entity_type,
                    ent.confidence,
                )
                continue
            used_w |= ent_w_keys
            accepted.append(ent)
        elif ent.text_node is not None:
            node_id = id(ent.text_node)
            overlaps = False
            for rid, rs, re_ in used_ranges:
                if rid == node_id and ent.text_start < re_ and ent.text_end > rs:
                    overlaps = True
                    break
            if overlaps:
                logger.debug(
                    "Overlap rejected: '%s' (%s, %.2f)",
                    ent.text,
                    ent.entity_type,
                    ent.confidence,
                )
                continue
            used_ranges.append((node_id, ent.text_start, ent.text_end))
            accepted.append(ent)

    return accepted


# =============================================================================
# XML INJECTION
# =============================================================================


def _confidence_to_cert(confidence, thresholds):
    """Map confidence score to TEI @cert value."""
    if confidence >= thresholds["high"]:
        return "high"
    elif confidence >= thresholds["mid"]:
        return "mid"
    return "low"


def _make_entity_element(entity_type, cert, entity_types_config):
    """Create a TEI entity element (e.g., <persName>, <rs type="event">)."""
    cfg = entity_types_config.get(entity_type, {})
    tag = cfg.get("tei_element", "rs")
    attrs = {"resp": "#ner-auto", "cert": cert}

    # Extra attributes for <rs type="..."> elements
    extra = cfg.get("tei_element_attrs", {})
    attrs.update(extra)

    # Use TEI namespace for parsed trees
    full_tag = f"{TEI_NS}{tag}"
    elem = etree.Element(full_tag, **attrs)
    return elem


def _inject_tokenized_entities(entities, cert_thresholds, entity_types_config):
    """
    Wrap <w> elements with entity tags inside <orig>.

    Groups consecutive <w> from the same parent into a single wrapper.
    """
    for ent in entities:
        if not ent.w_elements:
            continue

        cert = _confidence_to_cert(ent.confidence, cert_thresholds)

        # Group consecutive <w> by parent to handle multi-token entities.
        # Adjacency is required: moving non-adjacent siblings into one
        # wrapper would reorder intervening nodes (<pc>, <lb/>) in the
        # diplomatic text.
        groups = []
        current_group = [ent.w_elements[0]]

        for w in ent.w_elements[1:]:
            prev = current_group[-1]
            parent = w.getparent()
            if parent is not None and parent is prev.getparent():
                siblings = list(parent)
                try:
                    adjacent = siblings.index(prev) + 1 == siblings.index(w)
                except ValueError:
                    adjacent = False
            else:
                adjacent = False
            if adjacent:
                current_group.append(w)
            else:
                groups.append(current_group)
                current_group = [w]
        groups.append(current_group)

        for group in groups:
            parent = group[0].getparent()
            if parent is None:
                continue

            # Create entity wrapper element
            wrapper = _make_entity_element(ent.entity_type, cert, entity_types_config)

            # Insert wrapper before the first <w> in the group
            first_w = group[0]
            try:
                idx = list(parent).index(first_w)
            except ValueError:
                logger.debug("Skipping entity injection — <w> no longer in expected parent")
                continue
            parent.insert(idx, wrapper)

            # Move all <w> in the group into the wrapper
            for w in group:
                if w.getparent() is parent:
                    parent.remove(w)
                elif w.getparent() is not None:
                    w.getparent().remove(w)
                wrapper.append(w)


def _inject_reg_entities(entities, cert_thresholds, entity_types_config):
    """
    Inject entity tags directly inside <reg> elements (mixed content).

    Makes annotations visible on the modernized text; with dual anchoring the
    same entities are also wrapped around <w> in <orig> by
    _inject_tokenized_entities.

    Single-<reg> entities are wrapped in mixed content. Multi-<reg> entities
    are split into one wrapper per <reg> and linked via @xml:id + @next/@prev
    so the fragments can be reassembled.
    """
    # Step 1: per-entity fragment xml:ids (assign upfront so injection can
    # cross-reference even though wrappers are written in <reg> order).
    ent_fragment_ids = {}  # id(ent) → list[str] (one xml:id per fragment)
    for ent in entities:
        if not ent.reg_fragments:
            continue
        n = len(ent.reg_fragments)
        if n == 1:
            ent_fragment_ids[id(ent)] = [None]  # no xml:id needed
        else:
            base = uuid.uuid4().hex[:8]
            ent_fragment_ids[id(ent)] = [f"ent-{base}-{i}" for i in range(n)]

    # Step 2: group injection tasks by <reg>. Each task carries the entity,
    # the fragment offsets in that <reg>, and the fragment's xml:id+links.
    by_reg = {}  # id(reg_elem) → (reg_elem, [task, ...])
    for ent in entities:
        if not ent.reg_fragments:
            continue
        frag_ids = ent_fragment_ids[id(ent)]
        for frag_idx, (reg_elem, fstart, fend) in enumerate(ent.reg_fragments):
            xml_id = frag_ids[frag_idx]
            prev_id = frag_ids[frag_idx - 1] if frag_idx > 0 else None
            next_id = frag_ids[frag_idx + 1] if frag_idx + 1 < len(frag_ids) else None
            task = {
                "ent": ent,
                "start": fstart,
                "end": fend,
                "xml_id": xml_id,
                "prev_id": prev_id,
                "next_id": next_id,
            }
            key = id(reg_elem)
            by_reg.setdefault(key, (reg_elem, []))
            by_reg[key][1].append(task)

    # Step 3: for each <reg>, splice in wrappers in document order.
    for _, (reg_elem, tasks) in by_reg.items():
        original_text = reg_elem.text or ""
        if not original_text:
            continue

        forward = sorted(tasks, key=lambda t: (t["start"], t["end"]))

        # Defensive overlap drop (overlap resolver runs in <w>-space, which
        # may not catch coincidental same-<reg>-offset clashes).
        non_overlap = []
        last_end = -1
        for task in forward:
            if task["start"] is None or task["end"] is None:
                continue
            if task["start"] < last_end:
                logger.debug(
                    "Skipping overlapping reg entity '%s' at %d-%d",
                    task["ent"].text, task["start"], task["end"],
                )
                continue
            non_overlap.append(task)
            last_end = task["end"]

        if not non_overlap:
            continue

        reg_elem.text = None
        pos = 0
        prev_elem = None

        for task in non_overlap:
            ent = task["ent"]
            cert = _confidence_to_cert(ent.confidence, cert_thresholds)

            before = original_text[pos:task["start"]]
            if prev_elem is None:
                reg_elem.text = (reg_elem.text or "") + before
            else:
                prev_elem.tail = (prev_elem.tail or "") + before

            wrapper = _make_entity_element(ent.entity_type, cert, entity_types_config)
            wrapper.text = original_text[task["start"]:task["end"]]
            wrapper.tail = ""
            if task["xml_id"]:
                wrapper.set(XML_ID, task["xml_id"])
            if task["prev_id"]:
                wrapper.set("prev", f"#{task['prev_id']}")
            if task["next_id"]:
                wrapper.set("next", f"#{task['next_id']}")
            reg_elem.append(wrapper)
            prev_elem = wrapper
            pos = task["end"]

        remaining = original_text[pos:]
        if prev_elem is None:
            reg_elem.text = (reg_elem.text or "") + remaining
        else:
            prev_elem.tail = (prev_elem.tail or "") + remaining


def _inject_raw_text_entities(entities, cert_thresholds, entity_types_config):
    """
    Inject entity tags into raw text by splitting text nodes.

    Processes entities in reverse order (by position) to preserve offsets.
    """
    # Group by container, sort by position descending
    by_container = {}
    for ent in entities:
        if ent.text_node is None:
            continue
        key = id(ent.text_node)
        by_container.setdefault(key, (ent.text_node, []))
        by_container[key][1].append(ent)

    for _, (container, ents) in by_container.items():
        # Get the full text content
        full_text = etree.tostring(container, method="text", encoding="unicode") or ""

        # Sort entities by start offset for forward reconstruction
        forward_ents = sorted(ents, key=lambda e: e.text_start)

        # Remove existing text children (preserve non-text children)
        existing_children = list(container)

        # If container has child elements, this is complex — skip for safety
        if existing_children:
            logger.debug(
                "Raw text injection skipped for container with child elements"
            )
            continue

        container.text = None

        pos = 0
        prev_elem = None

        for ent in forward_ents:
            cert = _confidence_to_cert(ent.confidence, cert_thresholds)

            # Text before entity
            before = full_text[pos : ent.text_start]
            if prev_elem is None:
                container.text = (container.text or "") + before
            else:
                prev_elem.tail = (prev_elem.tail or "") + before

            # Entity element
            elem = _make_entity_element(ent.entity_type, cert, entity_types_config)
            elem.text = full_text[ent.text_start : ent.text_end]
            elem.tail = ""
            container.append(elem)
            prev_elem = elem
            pos = ent.text_end

        # Remaining text after last entity
        remaining = full_text[pos:]
        if prev_elem is None:
            container.text = (container.text or "") + remaining
        else:
            prev_elem.tail = (prev_elem.tail or "") + remaining


def _find_orig_and_reg(w):
    """Climb from a <w> to its <orig> ancestor and the sibling <reg>."""
    node = w
    orig = None
    while node is not None:
        if _local(node.tag) == "orig":
            orig = node
            break
        node = node.getparent()
    if orig is None:
        return None, None
    choice = orig.getparent()
    if choice is None or _local(choice.tag) != "choice":
        return orig, None
    for child in choice:
        if _local(child.tag) == "reg":
            return orig, child
    return orig, None


def backfill_reg_fragments(entities):
    """
    Compute <reg> fragments for entities that only carry <w> anchors
    (CamemBERT-only detections), so they can be dual-injected.

    Inverse of the positional mapping in _align_reg_spans: the i-th <w> of an
    <orig> corresponds to the i-th word of the sibling <reg> text.  Entities
    whose <w> are not under a <choice>/<reg> (Latin/Greek, unmodernized lines)
    are left unchanged and stay orig-only.
    """
    filled = 0
    for ent in entities:
        if ent.reg_fragments or not ent.w_elements:
            continue

        # Group the entity's <w> by <orig> ancestor (an entity can span
        # several <choice> lines); keep document order.
        by_orig = {}
        order = []
        for w in ent.w_elements:
            orig, reg = _find_orig_and_reg(w)
            if orig is None or reg is None:
                continue
            key = id(orig)
            if key not in by_orig:
                by_orig[key] = (orig, reg, [])
                order.append(key)
            by_orig[key][2].append(w)

        fragments = []
        for key in order:
            orig, reg, ws = by_orig[key]
            reg_text = reg.text or ""
            reg_words = reg_text.split()
            if not reg_words:
                continue
            w_elems = [e for e in orig.iter() if _local(e.tag) == "w"]
            indices = [w_elems.index(w) for w in ws if w in w_elems]
            indices = [i for i in indices if i < len(reg_words)]
            if not indices:
                continue

            # Char offsets of each word in reg_text (mirrors _align_reg_spans)
            offsets = []
            pos = 0
            for word in reg_words:
                start = reg_text.find(word, pos)
                offsets.append((start, start + len(word)))
                pos = start + len(word)

            fragments.append((reg, offsets[min(indices)][0], offsets[max(indices)][1]))

        if fragments:
            ent.reg_fragments = fragments
            filled += 1

    if filled:
        logger.info("NER: backfilled <reg> fragments for %d orig-only entities", filled)
    return filled


def inject_entities(aligned_entities, cert_thresholds, entity_types_config):
    """
    Inject all aligned entities into the TEI XML.

    Dual anchoring: an entity carrying both <w> anchors and <reg> fragments is
    injected on BOTH layers — wrapped around <w> in <orig> (diplomatic view)
    and as mixed content inside <reg> (modernized view). Both copies receive
    the same @ref in Phase 9 (add_refs_to_body).

    Reg-fragment entities → mixed-content wrap inside <reg> (multi-fragment
    entities linked via @xml:id + @next/@prev).
    Tokenized entities → wrap <w> in <orig>.
    Raw text entities → split text nodes.

    Args:
        aligned_entities: List of AlignedEntity (overlaps already resolved).
        cert_thresholds: NER_CERT_THRESHOLDS from config.
        entity_types_config: NER_ENTITY_TYPES from config.
    """
    reg_inj = [e for e in aligned_entities if e.reg_fragments]
    tokenized = [e for e in aligned_entities if e.w_elements]
    raw = [
        e for e in aligned_entities
        if not e.reg_fragments and e.text_node is not None and not e.w_elements
    ]

    if reg_inj:
        _inject_reg_entities(reg_inj, cert_thresholds, entity_types_config)
        logger.info("NER: injected %d <reg> entity annotations", len(reg_inj))

    if tokenized:
        _inject_tokenized_entities(tokenized, cert_thresholds, entity_types_config)
        logger.info("NER: injected %d tokenized entity annotations", len(tokenized))

    if raw:
        _inject_raw_text_entities(raw, cert_thresholds, entity_types_config)
        logger.info("NER: injected %d raw text entity annotations", len(raw))


# =============================================================================
# PUBLIC API — PHASE 8 ORCHESTRATOR
# =============================================================================


def align_and_inject(blocks, all_spans, entity_types_config, cert_thresholds):
    """
    Phase 8 orchestrator: align spans, merge models, resolve overlaps, inject.

    Args:
        blocks: List of NERBlock from Phase 7.
        all_spans: List of list of NERSpan from Phase 7 (parallel to blocks).
        entity_types_config: NER_ENTITY_TYPES from config.
        cert_thresholds: NER_CERT_THRESHOLDS from config.

    Returns:
        list[AlignedEntity]: All injected entities (for Phase 9 resolution).
    """
    if not blocks or not all_spans:
        return []

    # Step 1: Align spans to nodes per block
    container_aligned = {}  # container_id → {source → [AlignedEntity]}

    for block, spans in zip(blocks, all_spans):
        aligned = align_spans_to_nodes(block, spans)
        cid = id(block.container)
        container_aligned.setdefault(cid, {})
        container_aligned[cid][block.source] = aligned

    # Step 2: Merge cross-model results for French containers
    final_aligned = []
    seen_containers = set()

    for block in blocks:
        cid = id(block.container)
        if cid in seen_containers:
            continue
        seen_containers.add(cid)

        sources = container_aligned.get(cid, {})
        orig_aligned = sources.get("orig", [])
        reg_aligned = sources.get("reg", [])
        raw_aligned = sources.get("raw", [])

        if orig_aligned and reg_aligned:
            # French: merge CamemBERT (orig) + GLiNER (reg)
            merged = merge_model_results(orig_aligned, reg_aligned)
            final_aligned.extend(merged)
        else:
            final_aligned.extend(orig_aligned)
            final_aligned.extend(reg_aligned)
            final_aligned.extend(raw_aligned)

    # Step 3: POS-based filtering (uses @pos from linguistic enrichment)
    pos_filtered = filter_aligned_by_pos(final_aligned)

    # Step 4: Resolve overlaps
    resolved = resolve_overlaps(pos_filtered)
    logger.info(
        "NER: %d after merge, %d after POS filter, %d after overlap resolution",
        len(final_aligned),
        len(pos_filtered),
        len(resolved),
    )

    # Step 5: Backfill <reg> anchors for orig-only entities (dual anchoring)
    backfill_reg_fragments(resolved)

    # Step 6: Inject into XML
    inject_entities(resolved, cert_thresholds, entity_types_config)

    return resolved
