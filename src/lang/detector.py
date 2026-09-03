# -----------------------------------------------------------
# Language detection for TEIlle-douce pipeline
# -----------------------------------------------------------
"""
Language detection using lingua-language-detector with heuristic fallback.

Uses n-gram statistical models restricted to configured languages.
For short texts (< 8 words) with low confidence, falls back to
rule-based heuristics to avoid misclassification.
"""

import logging
import re
import unicodedata
from collections import Counter


# Character-level substitutions applied during cleaning (1→1 or 1→2).
# Extend this table to cover additional historical/OCR normalizations;
# the idx_map machinery handles length changes automatically.
_CHAR_SUBS = {
    "ſ": "s",     # long s
    "æ": "ae",    # ligatures
    "œ": "oe",
    "Æ": "Ae",
    "Œ": "Oe",
    "ß": "ss",
    "ë": "e",     # diacritics flattened for lingua
    "ï": "i",
    "ü": "u",
}

# Characters that are deleted entirely (typographic noise / OCR artifacts).
_DELETE_CHARS = frozenset("|¦")

# Vowels that trigger word-initial i/I → j/J (Ramist rule).
_J_VOWELS = frozenset("aeiouyàâéèêëîïôùûœAEIOUYÀÂÉÈÊËÎÏÔÙÛŒ")

from ..utils.hyphen import HYPHEN_CHARS, SOFT_HYPHEN, joins_words
from config import (
    SUPPORTED_LANGUAGES,
    LANG_CONFIDENCE_THRESHOLD,
    LANG_MIN_TEXT_LENGTH,
    LANG_DEFAULT,
)

logger = logging.getLogger(__name__)

# Lazy-loaded heuristics singleton
_heuristics = None

def _get_heuristics():
    global _heuristics
    if _heuristics is None:
        from .heuristics import get_heuristics
        _heuristics = get_heuristics()
    return _heuristics


# ISO 639-1 -> lingua Language enum name
_ISO1_TO_LINGUA_NAME = {
    "fr": "FRENCH",
    "la": "LATIN",
    "el": "GREEK",
    "de": "GERMAN",
    "nl": "DUTCH",
    "it": "ITALIAN",
    "en": "ENGLISH",
}


class LinguaDetector:
    """
    Language detector using lingua-language-detector.

    Only loads the languages listed in config.SUPPORTED_LANGUAGES.
    For short/ambiguous texts, falls back to rule-based heuristics.
    """

    MIN_SEGMENT_WORDS = 3

    def __init__(
        self,
        supported_langs=None,
        confidence_threshold=None,
        min_text_length=None,
        default_lang=None,
    ):
        self.supported_langs = supported_langs if supported_langs is not None else SUPPORTED_LANGUAGES
        self.confidence_threshold = confidence_threshold if confidence_threshold is not None else LANG_CONFIDENCE_THRESHOLD
        self.min_text_length = min_text_length if min_text_length is not None else LANG_MIN_TEXT_LENGTH
        self.default_lang = default_lang if default_lang is not None else LANG_DEFAULT
        self.detector = None
        self.stats = Counter()
        self._document_prior = None  # dominant language from first pass

        # lingua enum name -> TEI ident (e.g. "FRENCH" -> "fra")
        self._lingua_name_to_tei = {}
        for code, info in self.supported_langs.items():
            lingua_name = _ISO1_TO_LINGUA_NAME.get(code)
            if lingua_name:
                self._lingua_name_to_tei[lingua_name] = info["ident"]

    def _ensure_detector(self):
        """Build the lingua detector lazily on first use."""
        if self.detector is not None:
            return

        from lingua import Language, LanguageDetectorBuilder

        languages = []
        for code in self.supported_langs:
            lingua_name = _ISO1_TO_LINGUA_NAME.get(code)
            if lingua_name and hasattr(Language, lingua_name):
                languages.append(getattr(Language, lingua_name))

        if not languages:
            raise ValueError(
                "No lingua Language objects could be built from SUPPORTED_LANGUAGES. "
                f"Available mappings: {list(_ISO1_TO_LINGUA_NAME.keys())}"
            )

        self.detector = (
            LanguageDetectorBuilder
            .from_languages(*languages)
            .with_preloaded_language_models()
            .with_minimum_relative_distance(0.25)
            .build()
        )
        logger.info(
            "Lingua detector initialized with languages: %s",
            [lang.name for lang in languages],
        )

    def clean_text(self, text):
        """Normalize OCR/historical text for detection (string only)."""
        cleaned, _ = self._clean_with_map(text)
        return cleaned

    def _clean_with_map(self, text):
        """
        Normalize text *and* return a per-character origin map.

        Runs a single char-by-char pass applying every normalization
        ``clean_text`` performs (historical character subs, Ramist u/v
        and i/j, hyphen rejoin at line breaks, whitespace collapse,
        pipe deletion), while emitting an index table ``idx_map`` such
        that ``idx_map[i]`` is the offset in the *original* text of the
        i-th character of the cleaned output.

        This map lets callers translate cleaned-text offsets back to
        the original without the word-count heuristic: a segment
        ``[c_start, c_end)`` in cleaned maps to
        ``(idx_map[c_start], idx_map[c_end - 1] + 1)`` in the input.
        Deleted characters (hyphens, collapsed whitespace, pipe
        separators) have no entry in ``idx_map``, giving a natural
        "snap inside" semantics: the segment boundary lands on the
        nearest kept character.

        One-to-many substitutions (``æ`` → ``ae``) produce two entries
        pointing back at the same input offset.

        Args:
            text: Raw input text.

        Returns:
            tuple[str, list[int]]: cleaned text and its idx_map.
        """
        if not text:
            return "", []

        # Normalize to NFC so combining sequences become precomposed
        # where possible. This is conservative in length for our
        # corpus (17th-c. printed characters are already precomposed)
        # and keeps the idx_map in code-point units throughout.
        src = unicodedata.normalize("NFC", text)

        out = []
        idx_map = []
        n = len(src)
        i = 0
        last_emitted_was_space = True  # True → strip leading whitespace
        word_just_started = True        # True → next alpha char is word-initial

        while i < n:
            c = src[i]

            # 1→0  Pipe / broken-bar separators (OCR noise)
            if c in _DELETE_CHARS:
                i += 1
                continue

            # 1→0  Line-break hyphen: <word>¬[\s]*<word> or <word>-[\s]*<word>
            # Consume the hyphen and the whitespace that follows, if the
            # previous kept char was a letter and the next non-space char
            # is also a letter. Falls through to normal handling otherwise.
            if c in HYPHEN_CHARS:
                prev_kept = out[-1] if out else ""
                # The whitespace scan only matters when a letter precedes
                # the mark: a dash used as punctuation or a rule would
                # otherwise walk its whole following run for nothing, on
                # every character of every page.
                if prev_kept and prev_kept.isalpha():
                    j = i + 1
                    while j < n and src[j].isspace():
                        j += 1
                    if joins_words(prev_kept, src[j:j + 1]):
                        i = j
                        word_just_started = False
                        continue
                # Not a word-splitting hyphen; keep "-", which can be a
                # genuine compound, and drop the soft hyphen, which is
                # never part of a word (src/utils/hyphen.py).
                if c == SOFT_HYPHEN:
                    i += 1
                    continue

            # Whitespace collapse: keep one space if the previous emitted
            # char wasn't already a space; drop further runs of whitespace.
            if c.isspace():
                if not last_emitted_was_space:
                    out.append(" ")
                    idx_map.append(i)
                    last_emitted_was_space = True
                word_just_started = True
                i += 1
                continue

            # Character-level substitutions (1→1 or 1→2)
            mapped = _CHAR_SUBS.get(c)
            if mapped is not None:
                for sub_ch in mapped:
                    out.append(sub_ch)
                    idx_map.append(i)
                last_emitted_was_space = False
                word_just_started = False
                i += 1
                continue

            # Ramist: mid-word 'uu' → 'uv'. We check AFTER confirming the
            # previous kept char is alpha (inside a word) AND the char
            # after the pair is also alpha.
            if c == "u" and out and out[-1].isalpha():
                if i + 1 < n and src[i + 1] == "u":
                    if i + 2 < n and src[i + 2].isalpha():
                        out.append("u"); idx_map.append(i)
                        out.append("v"); idx_map.append(i + 1)
                        last_emitted_was_space = False
                        word_just_started = False
                        i += 2
                        continue

            # Ramist: word-initial i/I before a vowel → j/J.
            if word_just_started and c in ("i", "I"):
                if i + 1 < n and src[i + 1] in _J_VOWELS:
                    out.append("j" if c == "i" else "J")
                    idx_map.append(i)
                    last_emitted_was_space = False
                    word_just_started = False
                    i += 1
                    continue

            # Default: identity copy.
            out.append(c)
            idx_map.append(i)
            last_emitted_was_space = False
            if c.isalpha() or c.isdigit():
                word_just_started = False
            else:
                word_just_started = True
            i += 1

        # Trim trailing whitespace.
        while out and out[-1] == " ":
            out.pop()
            idx_map.pop()

        return "".join(out), idx_map

    def _lang_to_tei(self, lingua_lang):
        """Convert a lingua Language enum to TEI ident code."""
        if lingua_lang is None:
            return self.default_lang
        return self._lingua_name_to_tei.get(lingua_lang.name, self.default_lang)

    def compute_document_prior(self, texts):
        """
        First pass: detect the dominant language from all document texts.

        Concatenates a sample of texts and runs lingua on the combined text
        to establish the document-level dominant language.

        Args:
            texts: Iterable of text strings from all containers.
        """
        # Sample up to 5000 chars for efficiency
        sample = []
        total = 0
        for t in texts:
            if t and t.strip():
                cleaned = self.clean_text(t)
                sample.append(cleaned)
                total += len(cleaned)
                if total > 5000:
                    break

        combined = " ".join(sample)
        if len(combined) < self.min_text_length:
            return

        self._ensure_detector()
        confs = self.detector.compute_language_confidence_values(combined)
        if confs:
            prior = self._lang_to_tei(confs[0].language)
            prior_conf = confs[0].value
            if prior_conf >= self.confidence_threshold:
                self._document_prior = prior
                logger.info(
                    "Document prior: %s (confidence=%.3f)", prior, prior_conf
                )

    def _detect_single(self, text):
        """
        Detect language for a single text segment.

        Strategy:
        1. Get lingua confidence values
        2. If lingua picks a non-prior language, always check heuristics
        3. Heuristics can override lingua when they disagree
        4. For ambiguous cases (low confidence), fall back to prior/default
        """
        if len(text) < self.min_text_length:
            return (self.default_lang, 0.0)

        self._ensure_detector()

        confs = self.detector.compute_language_confidence_values(text)
        if not confs:
            return (self.default_lang, 0.0)

        best = confs[0]
        detected = self._lang_to_tei(best.language)
        confidence = best.value
        prior = self._document_prior or self.default_lang

        # Lingua agrees with prior — but still verify non-default languages
        # with heuristics (the prior itself may have been wrong)
        if detected == prior and detected == self.default_lang:
            return (detected, confidence)
        if detected == prior and detected != self.default_lang:
            # Prior is non-default (e.g. lat): check heuristics can confirm
            heuristics = _get_heuristics()
            heur_lang, heur_score = heuristics.detect(text)
            if heur_lang == self.default_lang and heur_score >= 2:
                logger.debug(
                    "Heuristics override prior+lingua: %s(%.3f) -> %s (heur=%.1f) | %s",
                    detected, confidence, self.default_lang, heur_score, text[:60],
                )
                return (self.default_lang, confidence)
            return (detected, confidence)

        # Lingua disagrees with prior — consult heuristics
        supported_idents = {info["ident"] for info in self.supported_langs.values()}
        heuristics = _get_heuristics()
        heur_lang, heur_score = heuristics.detect(text)

        # Also check if heuristics have ANY signal for the detected language
        detected_heur_score = 0
        if detected in heuristics.rules:
            rules = heuristics.rules[detected]
            detected_heur_score = (
                heuristics._count_char_matches(text, rules["chars"]) * heuristics.char_weight
                + heuristics._count_word_matches(text, rules["words"])
                + heuristics._count_pattern_matches(text, rules["patterns"]) * 1.5
            )

        # With a document prior, lower the bar: even a weak signal for
        # the prior language overrides lingua, especially if heuristics
        # found no evidence for the detected language
        min_heur = 2
        if self._document_prior:
            min_heur = 1  # weaker signal is enough with a prior

        # Heuristics say it's the prior language — override lingua
        if heur_lang == prior and heur_score >= min_heur:
            logger.debug(
                "Heuristics override: lingua=%s(%.3f) -> %s (heur=%.1f) | %s",
                detected, confidence, prior, heur_score, text[:60],
            )
            return (prior, confidence)

        # Prior is set, heuristics found no evidence for detected language,
        # and lingua confidence is not overwhelming — prefer prior
        if self._document_prior and detected_heur_score < 1 and confidence < 0.9:
            logger.debug(
                "Prior fallback: lingua=%s(%.3f) no heuristic support -> %s | %s",
                detected, confidence, prior, text[:60],
            )
            return (prior, confidence)

        # Heuristics say it's a different supported language — use that
        if heur_lang and heur_lang in supported_idents and heur_score >= min_heur:
            if heur_lang != detected:
                logger.debug(
                    "Heuristics redirect: lingua=%s -> heur=%s (score=%.1f) | %s",
                    detected, heur_lang, heur_score, text[:60],
                )
            return (heur_lang, confidence)

        # No heuristic opinion — trust lingua if confident enough
        if confidence >= self.confidence_threshold:
            return (detected, confidence)

        # Low confidence, no heuristic match — fall back to prior
        return (prior, 0.0)

    def detect(self, text):
        """Detect the primary language. Returns TEI ident code."""
        cleaned = self.clean_text(text)
        lang, _ = self._detect_single(cleaned)
        self.stats[lang] += 1
        return lang

    def detect_primary_and_segments(self, text):
        """
        Primary language + foreign segments in one cleaning pass.

        detect() then detect_foreign_segments() each re-cleaned the same
        text (audit 3.6); this entry point cleans once and reuses the
        result for both detections.

        NB — audit 3.1 proposed skipping the multi-language scan when the
        primary confidence is >= ~0.9. Measured on the corpus sample
        (2 545 containers, 240 with foreign segments): the MEDIAN primary
        confidence of the multilingual containers is 1.000, so any
        confidence threshold discards most real foreign segments (205/240
        lost even at a 1.0 gate). For a corpus whose multilingualism is a
        research object, the scan must always run; the gate was rejected.

        Returns:
            tuple: (primary_lang, segments) — the same values the
            detect() / detect_foreign_segments() pair produces.
        """
        cleaned, idx_map = self._clean_with_map(text)
        if len(cleaned) < self.min_text_length:
            primary = self.default_lang
        else:
            self._ensure_detector()
            primary, _confidence = self._detect_single(cleaned)
        self.stats[primary] += 1

        return primary, self._foreign_segments_cleaned(cleaned, idx_map, primary)

    def detect_foreign_segments(self, text, primary_lang=None):
        """
        Detect foreign-language segments with offsets in the input text.

        Cleans *text* once, keeping a per-character origin map, runs
        lingua's multi-language detector on the cleaned string, then
        translates each accepted segment's cleaned-offset bounds back
        to the original via the map — no word-count heuristic, so the
        alignment stays correct even when the cleaning rejoins
        hyphenated words, expands ligatures, or deletes separators.

        Segments whose language is the primary one are discarded, as
        are segments shorter than ``MIN_SEGMENT_WORDS`` or rejected by
        rule-based heuristics (see ``_foreign_segments_cleaned``).

        Args:
            text: Input text (as extracted from the TEI container).
            primary_lang: TEI ident of the container's primary language;
                if None, detected from the text itself.

        Returns:
            list[tuple[int, int, str]]: ``(start, end, tei_lang)`` with
            offsets in the *input* text, ready to slice ``.tail`` data.
        """
        cleaned, idx_map = self._clean_with_map(text)
        if primary_lang is None and len(cleaned) >= self.min_text_length:
            self._ensure_detector()
            primary_lang, _ = self._detect_single(cleaned)
        return self._foreign_segments_cleaned(cleaned, idx_map, primary_lang)

    def _foreign_segments_cleaned(self, cleaned, idx_map, primary_lang):
        """Core of detect_foreign_segments, operating on already-cleaned
        text (audit 3.6: one cleaning pass shared with primary detection
        via detect_primary_and_segments)."""
        if len(cleaned) < self.min_text_length:
            return []

        self._ensure_detector()

        # Count words cheaply without tokenizing (contiguous non-space runs).
        word_count = sum(1 for _ in re.finditer(r"\S+", cleaned))
        if word_count < self.MIN_SEGMENT_WORDS * 2:
            return []

        multi_results = self.detector.detect_multiple_languages_of(cleaned)
        if len(multi_results) <= 1:
            return []

        heuristics = _get_heuristics()
        segments = []
        cleaned_len = len(cleaned)

        for r in multi_results:
            tei_lang = self._lang_to_tei(r.language)
            if tei_lang == primary_lang:
                continue
            if r.word_count < self.MIN_SEGMENT_WORDS:
                continue

            seg_text = cleaned[r.start_index:r.end_index]

            heur_lang, heur_score = heuristics.detect(seg_text)
            if heur_lang == primary_lang and heur_score >= 2:
                logger.debug(
                    "Rejected foreign segment '%s' — heuristics say %s",
                    seg_text[:50], primary_lang,
                )
                continue
            _, foreign_heur_score = heuristics.detect_lang(seg_text, tei_lang)
            if foreign_heur_score == 0:
                logger.debug(
                    "Rejected foreign segment '%s' — no %s signal",
                    seg_text[:50], tei_lang,
                )
                continue

            # Map cleaned offsets back to the input via idx_map.
            # Clamp on the rare chance lingua returns out-of-bounds.
            c_start = max(0, min(r.start_index, cleaned_len))
            c_end = max(c_start, min(r.end_index, cleaned_len))
            if c_start >= cleaned_len or c_end <= c_start:
                continue
            in_start = idx_map[c_start]
            in_end = idx_map[c_end - 1] + 1
            segments.append((in_start, in_end, tei_lang))

        for _, _, lang in segments:
            self.stats[lang] += 1

        return segments

    def get_stats(self):
        """Return language usage counts, sorted by frequency."""
        if not self.stats:
            return {}
        return dict(self.stats.most_common())

    def reset_stats(self):
        self.stats.clear()
        self._document_prior = None


# Global singleton
_instance = None

def get_detector():
    """Get or create the global LinguaDetector instance."""
    global _instance
    if _instance is None:
        _instance = LinguaDetector()
    return _instance
