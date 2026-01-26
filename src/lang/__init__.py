# -----------------------------------------------------------
# Language detection module for ALTO2TEI pipeline
# -----------------------------------------------------------
"""
Language detection module using FastText.

This module provides language detection capabilities for text elements
in TEI documents. It uses FastText's pre-trained language identification model.

Usage:
    from src.lang import LanguageDetector, update_langusage

    detector = LanguageDetector()
    lang = detector.detect("Bonjour le monde")
    # Returns: "fra"
"""

from .detector import LanguageDetector, detect_language, get_detector, LangSegment
from .header import update_langusage

__all__ = [
    "LanguageDetector",
    "LangSegment",
    "detect_language",
    "get_detector",
    "update_langusage",
]
