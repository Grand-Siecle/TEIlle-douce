# -----------------------------------------------------------
# Language detection module for TEIlle-douce pipeline
# -----------------------------------------------------------
"""
Language detection using lingua-language-detector with heuristic fallback.

Configuration (in config.py):
    - SUPPORTED_LANGUAGES: languages to detect (add more as needed)
    - LANG_CONFIDENCE_THRESHOLD: minimum confidence for detection
    - LANG_MIN_TEXT_LENGTH: minimum text length to attempt detection
    - LANG_DEFAULT: default when detection fails (see config.py)
"""

from .detector import LinguaDetector, get_detector
from .header import LangUsageBuilder, build_langusage
from .heuristics import LanguageHeuristics, get_heuristics

__all__ = [
    "LinguaDetector",
    "get_detector",
    "LanguageHeuristics",
    "get_heuristics",
    "LangUsageBuilder",
    "build_langusage",
]
