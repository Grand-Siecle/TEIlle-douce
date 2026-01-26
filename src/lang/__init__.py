# -----------------------------------------------------------
# Language detection module for ALTO2TEI pipeline
# -----------------------------------------------------------
"""
Language detection module using FastText.

This module provides language detection capabilities for text elements
in TEI documents. It uses FastText's pre-trained language identification
model (lid.176.bin) to detect languages and update TEI headers.

Main components:
    - LanguageDetector: Core detection class with sliding window support
    - LangUsageBuilder: TEI header `<langUsage>` builder
    - build_langusage: Convenience function for updating headers

Example usage:
    >>> from src.lang import LanguageDetector, build_langusage
    >>>
    >>> # Detect language
    >>> detector = LanguageDetector()
    >>> lang = detector.detect("Bonjour le monde")
    >>> print(lang)  # "fra"
    >>>
    >>> # Update TEI header
    >>> stats = detector.get_stats()
    >>> build_langusage(tei_root, stats)

Configuration (in config.py):
    - SUPPORTED_LANGUAGES: Dict of supported language codes
    - LANG_CONFIDENCE_THRESHOLD: Minimum detection confidence
    - LANG_MIN_TEXT_LENGTH: Minimum text length for detection
    - LANG_DEFAULT: Default language code (e.g., "und")
    - LANG_FALLBACK: Fallback language for unsupported detections
"""

from .detector import LanguageDetector, LangSegment, detect_language, get_detector
from .header import LangUsageBuilder, build_langusage
from .heuristics import LanguageHeuristics, get_heuristics

__all__ = [
    # Detection
    "LanguageDetector",
    "LangSegment",
    "detect_language",
    "get_detector",
    # Heuristics
    "LanguageHeuristics",
    "get_heuristics",
    # Header building
    "LangUsageBuilder",
    "build_langusage",
]
