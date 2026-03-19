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
    SHORT_TEXT_WORDS = 8

    def __init__(
        self,
        supported_langs=None,
        confidence_threshold=None,
        min_text_length=None,
        default_lang=None,
    ):
        self.supported_langs = supported_langs or SUPPORTED_LANGUAGES
        self.confidence_threshold = confidence_threshold or LANG_CONFIDENCE_THRESHOLD
        self.min_text_length = min_text_length or LANG_MIN_TEXT_LENGTH
        self.default_lang = default_lang or LANG_DEFAULT
        self.detector = None
        self.stats = Counter()

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

        text = re.sub(r'\s+', ' ', text)
        text = re.sub(r'[|¦]', '', text)

        return text.strip()

    def _lang_to_tei(self, lingua_lang):
        """Convert a lingua Language enum to TEI ident code."""
        if lingua_lang is None:
            return self.default_lang
        return self._lingua_name_to_tei.get(lingua_lang.name, self.default_lang)

    def _detect_single(self, text):
        """
        Detect language for a single text segment.

        Returns (language_code, confidence). Falls back to heuristics
        for short texts with low lingua confidence.
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

        if confidence >= self.confidence_threshold:
            return (detected, confidence)

        # Short text + low confidence: try heuristics
        if len(text.split()) < self.SHORT_TEXT_WORDS:
            supported_idents = {info["ident"] for info in self.supported_langs.values()}
            heur_lang, heur_score = _get_heuristics().detect(text)
            if heur_lang and heur_lang in supported_idents and heur_score >= 2:
                return (heur_lang, confidence)

        return (detected, confidence)

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

        # Find majority language by word count
        lang_word_counts = Counter()
        for r in multi_results:
            lang_word_counts[self._lang_to_tei(r.language)] += r.word_count
        majority_lang = lang_word_counts.most_common(1)[0][0]

        # Collect foreign segments
        foreign_segments = []
        for r in multi_results:
            tei_lang = self._lang_to_tei(r.language)
            if tei_lang != majority_lang and tei_lang != self.default_lang:
                if r.word_count >= self.MIN_SEGMENT_WORDS:
                    foreign_segments.append(LinguaSegment(
                        text=cleaned[r.start_index:r.end_index],
                        lang=tei_lang,
                        start=r.start_index,
                        end=r.end_index,
                    ))

        self.stats[majority_lang] += 1
        for seg in foreign_segments:
            self.stats[seg.lang] += 1

        return (majority_lang, foreign_segments)

    def get_stats(self):
        """Return language usage counts, sorted by frequency."""
        if not self.stats:
            return {}
        return dict(self.stats.most_common())

    def reset_stats(self):
        self.stats.clear()


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
