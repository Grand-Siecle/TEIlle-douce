# -----------------------------------------------------------
# Language detection for ALTO2TEI pipeline
# -----------------------------------------------------------
"""
Language detection using lingua-language-detector with heuristic fallback.

Uses n-gram statistical models restricted to configured languages.
For short texts (< 8 words) with low confidence, falls back to
rule-based heuristics to avoid misclassification.
"""

import logging
import re
from collections import Counter
from dataclasses import dataclass

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


@dataclass
class LinguaSegment:
    """A segment of text with its detected language."""
    text: str
    lang: str
    start: int
    end: int


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
        """Normalize OCR/historical text for detection."""
        if not text:
            return ""

        # Rejoin hyphenated line breaks
        text = re.sub(r'(\w)[¬\-]\s*\n?\s*(\w)', r'\1\2', text)
        text = re.sub(r'¬', '', text)

        # Historical character normalization
        text = text.replace('ſ', 's')
        text = text.replace('æ', 'ae')
        text = text.replace('œ', 'oe')
        text = text.replace('Æ', 'Ae')
        text = text.replace('Œ', 'Oe')
        text = text.replace('ß', 'ss')
        text = text.replace('ë', 'e')
        text = text.replace('ï', 'i')
        text = text.replace('ü', 'u')

        # Ramist letter normalization (pre-17th c. typography):
        # u/v: 'uu' mid-word → 'uv' (pouuoir→pouvoir, trouuer→trouver)
        text = re.sub(r'(?<=\w)uu(?=\w)', 'uv', text)
        # i/j: word-initial 'i' before vowel → 'j' (ie→je, iamais→jamais)
        text = re.sub(r'\bi(?=[aeiouyàâéèêëîïôùûœ])', 'j', text)
        text = re.sub(r'\bI(?=[aeiouyàâéèêëîïôùûœAEIOUY])', 'J', text)

        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[|¦]', '', text)

        return text.strip()

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

    def detect_with_segments(self, text):
        """
        Detect primary language and foreign segments.

        Returns (primary_lang, list[LinguaSegment]) where segments
        are foreign-language portions of the text.
        """
        cleaned = self.clean_text(text)

        if len(cleaned) < self.min_text_length:
            self.stats[self.default_lang] += 1
            return (self.default_lang, [])

        primary_lang, _ = self._detect_single(cleaned)

        # Need enough words for multi-language detection
        self._ensure_detector()
        words = cleaned.split()
        if len(words) < self.MIN_SEGMENT_WORDS * 2:
            self.stats[primary_lang] += 1
            return (primary_lang, [])

        multi_results = self.detector.detect_multiple_languages_of(cleaned)

        if len(multi_results) <= 1:
            self.stats[primary_lang] += 1
            return (primary_lang, [])

        # Collect foreign segments, validated by heuristics
        heuristics = _get_heuristics()
        foreign_segments = []
        for r in multi_results:
            tei_lang = self._lang_to_tei(r.language)
            if tei_lang != primary_lang:
                if r.word_count >= self.MIN_SEGMENT_WORDS:
                    seg_text = cleaned[r.start_index:r.end_index]
                    # Validate with heuristics (two checks):
                    # 1) Reject if heuristics positively say primary lang (≥2).
                    # 2) Reject if heuristics find NO signal for the detected
                    #    foreign lang either — ambiguous segments are not tagged.
                    heur_lang, heur_score = heuristics.detect(seg_text)
                    if heur_lang == primary_lang and heur_score >= 2:
                        logger.debug(
                            "Rejected foreign segment '%s' — heuristics say %s",
                            seg_text[:50], primary_lang,
                        )
                        continue
                    # Check the detected foreign lang has some heuristic backing
                    foreign_heur_lang, foreign_heur_score = heuristics.detect_lang(
                        seg_text, tei_lang,
                    )
                    if foreign_heur_score == 0:
                        logger.debug(
                            "Rejected foreign segment '%s' — no %s signal",
                            seg_text[:50], tei_lang,
                        )
                        continue
                    foreign_segments.append(LinguaSegment(
                        text=seg_text,
                        lang=tei_lang,
                        start=r.start_index,
                        end=r.end_index,
                    ))

        self.stats[primary_lang] += 1
        for seg in foreign_segments:
            self.stats[seg.lang] += 1

        return (primary_lang, foreign_segments)

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

def detect_language(text):
    """Convenience function: detect language of text."""
    return get_detector().detect(text)
