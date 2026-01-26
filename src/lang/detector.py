# -----------------------------------------------------------
# FastText language detection for ALTO2TEI pipeline
# -----------------------------------------------------------
"""
Language detection using FastText with sliding window approach.

This module provides a LanguageDetector class that uses FastText's
pre-trained language identification model (lid.176.bin) to detect
the language of text content, with support for mixed-language detection.
"""

import re
import warnings
from pathlib import Path
from collections import Counter
from dataclasses import dataclass

from config import (
    SUPPORTED_LANGUAGES,
    LANG_CONFIDENCE_THRESHOLD,
    LANG_MIN_TEXT_LENGTH,
    LANG_DEFAULT,
    FASTTEXT_MODEL_PATH,
)

# Import fallback with default to None if not defined
try:
    from config import LANG_FALLBACK
except ImportError:
    LANG_FALLBACK = None

# Import heuristics (lazy to avoid circular import)
_heuristics = None

def _get_heuristics():
    """Lazy load heuristics module."""
    global _heuristics
    if _heuristics is None:
        from .heuristics import get_heuristics
        _heuristics = get_heuristics()
    return _heuristics

# Suppress FastText warnings
warnings.filterwarnings("ignore", category=UserWarning)

# Global detector instance (lazy initialization)
_detector_instance = None


@dataclass
class LangSegment:
    """A segment of text with its detected language."""
    text: str
    lang: str
    start: int  # character offset in original text
    end: int


class LanguageDetector:
    """
    Language detector using FastText with sliding window support.

    Uses the lid.176.bin model to identify languages. Supports:
    - Text cleaning (hyphenation removal, normalization)
    - Sliding window detection for mixed-language texts
    - Foreign segment detection

    Attributes:
        model: FastText language identification model.
        supported_langs (dict): Mapping of supported language codes.
        confidence_threshold (float): Minimum confidence for detection.
        min_text_length (int): Minimum text length to attempt detection.
        default_lang (str): Default language code when detection fails.
        stats (Counter): Statistics of detected languages.
    """

    # FastText model URL
    MODEL_URL = "https://dl.fbaipublicfiles.com/fasttext/supervised-models/lid.176.bin"
    MODEL_FILENAME = "lid.176.bin"

    # Window parameters for sliding detection
    WINDOW_SIZE = 50  # words per window
    WINDOW_STEP = 25  # step size (overlap = WINDOW_SIZE - WINDOW_STEP)
    MIN_SEGMENT_WORDS = 3  # minimum words for a foreign segment

    def __init__(
        self,
        supported_langs=None,
        confidence_threshold=None,
        min_text_length=None,
        default_lang=None,
        model_path=None,
    ):
        """
        Initialize the language detector.

        Args:
            supported_langs: Dict of supported language codes (from config if None).
            confidence_threshold: Min confidence threshold (from config if None).
            min_text_length: Min text length for detection (from config if None).
            default_lang: Default language when detection fails (from config if None).
            model_path: Path to FastText model (auto-downloads if None).
        """
        self.supported_langs = supported_langs or SUPPORTED_LANGUAGES
        self.confidence_threshold = confidence_threshold or LANG_CONFIDENCE_THRESHOLD
        self.min_text_length = min_text_length or LANG_MIN_TEXT_LENGTH
        self.default_lang = default_lang or LANG_DEFAULT
        self.model_path = model_path or FASTTEXT_MODEL_PATH
        self.model = None
        self.stats = Counter()

        # Build reverse mapping: fasttext code -> TEI ident
        self._lang_map = self._build_lang_map()

    def _build_lang_map(self):
        """Build mapping from FastText language codes to TEI idents."""
        lang_map = {}

        # FastText uses ISO 639-1 codes (2-letter) for most languages
        ft_to_tei = {
            "fr": "fra",
            "en": "eng",
            "de": "deu",
            "it": "ita",
            "nl": "nld",
            "la": "lat",
            "el": "grc",  # Modern Greek, map to Ancient Greek
        }

        for code, info in self.supported_langs.items():
            if code in ft_to_tei:
                lang_map[code] = info["ident"]
            else:
                lang_map[code] = info["ident"]

        return lang_map

    def _ensure_model(self):
        """Ensure the FastText model is loaded."""
        if self.model is not None:
            return

        try:
            import fasttext
            fasttext.FastText.eprint = lambda x: None
        except ImportError:
            raise ImportError(
                "fasttext is required for language detection. "
                "Install it with: pip install fasttext"
            )

        model_path = self._get_model_path()

        if not model_path.exists():
            self._download_model(model_path)

        self.model = fasttext.load_model(str(model_path))

        # Workaround for numpy 2.0 compatibility
        self._original_predict = self.model.predict
        self.model.predict = self._predict_wrapper

    def _predict_wrapper(self, text, k=1, threshold=0.0, on_unicode_error="strict"):
        """Wrapper for predict that handles numpy 2.0 compatibility."""
        result = self.model.f.predict(text, k, threshold, on_unicode_error)

        if len(result) == 0:
            return ((), [])

        labels = []
        probs = []
        for prob, label in result:
            labels.append(label)
            probs.append(prob)

        return (tuple(labels), probs)

    def _get_model_path(self):
        """Get the path to the FastText model file."""
        if self.model_path:
            return Path(self.model_path)

        cache_dir = Path.home() / ".fasttext"
        cache_dir.mkdir(parents=True, exist_ok=True)
        return cache_dir / self.MODEL_FILENAME

    def _download_model(self, model_path):
        """Download the FastText language identification model."""
        import urllib.request

        print(f"Downloading FastText model to {model_path}...")
        print("This may take a few minutes (file is ~126MB)")

        try:
            urllib.request.urlretrieve(self.MODEL_URL, model_path)
            print("Download complete.")
        except Exception as e:
            raise RuntimeError(
                f"Failed to download FastText model: {e}\n"
                f"You can manually download from {self.MODEL_URL} "
                f"and place it at {model_path}"
            )

    def clean_text(self, text):
        """
        Clean text for language detection.

        Removes/normalizes:
        - Line-break hyphenation (¬, - at end of words)
        - Historical characters (ſ -> s, etc.)
        - Multiple spaces
        - Special OCR artifacts

        Args:
            text (str): Raw text to clean.

        Returns:
            str: Cleaned text.
        """
        if not text:
            return ""

        # Remove hyphenation marks (¬ or - followed by newline/space and continuation)
        # Pattern: word ending with ¬ or - followed by space/newline and next word part
        text = re.sub(r'(\w)[¬\-]\s*\n?\s*(\w)', r'\1\2', text)

        # Remove standalone ¬
        text = re.sub(r'¬', '', text)

        # Normalize historical/OCR characters
        # Long s -> s
        text = text.replace('ſ', 's')
        # Ligatures
        text = text.replace('æ', 'ae')
        text = text.replace('œ', 'oe')
        text = text.replace('Æ', 'Ae')
        text = text.replace('Œ', 'Oe')
        # Old forms
        text = text.replace('ß', 'ss')
        # Accented variations sometimes used in old prints
        text = text.replace('ë', 'e')
        text = text.replace('ï', 'i')
        text = text.replace('ü', 'u')

        # Normalize whitespace
        text = re.sub(r'\s+', ' ', text)

        # Remove common OCR artifacts
        text = re.sub(r'[|¦]', '', text)  # vertical bars

        return text.strip()

    def _detect_single(self, text):
        """
        Detect language for a single text segment.

        Uses FastText as primary detector, with heuristic rules as fallback
        for unsupported languages or low confidence detections.

        Args:
            text (str): Text to analyze (should be pre-cleaned).

        Returns:
            tuple: (language_code, confidence)
        """
        if len(text) < self.min_text_length:
            return (self.default_lang, 0.0)

        # Get supported language idents for checking
        supported_idents = {info["ident"] for info in self.supported_langs.values()}

        self._ensure_model()

        try:
            predictions = self.model.predict(text, k=1)
            label = predictions[0][0]
            confidence = float(predictions[1][0])

            lang_code = label.replace("__label__", "")

            # Map FastText code to TEI ident
            if lang_code in self._lang_map:
                detected = self._lang_map[lang_code]
            elif lang_code in supported_idents:
                detected = lang_code
            else:
                detected = None  # Unsupported language

            # Case 1: Supported language with good confidence - return it
            if detected and confidence >= self.confidence_threshold:
                return (detected, confidence)

            # Case 2: Try heuristics for better detection
            heuristics = _get_heuristics()
            heur_lang, heur_score = heuristics.detect(text)

            # If heuristics found a supported language with good score
            if heur_lang and heur_lang in supported_idents and heur_score >= 2:
                return (heur_lang, confidence)

            # Case 3: FastText detected supported but low confidence
            if detected and confidence < self.confidence_threshold:
                # If heuristics agree or have a signal, use FastText result
                if heur_lang == detected or heur_score >= 1:
                    return (detected, confidence)
                # Otherwise return default
                return (self.default_lang, confidence)

            # Case 4: Unsupported language - use fallback
            fallback = LANG_FALLBACK if LANG_FALLBACK else self.default_lang
            return (fallback, confidence)

        except Exception:
            return (self.default_lang, 0.0)

    def detect(self, text):
        """
        Detect the primary language of the text.

        Args:
            text (str): Text to analyze.

        Returns:
            str: TEI language ident code.
        """
        cleaned = self.clean_text(text)
        lang, conf = self._detect_single(cleaned)
        self.stats[lang] += 1
        return lang

    def detect_with_segments(self, text):
        """
        Detect languages with sliding window, returning segments.

        This method detects the primary language and identifies
        foreign language segments within the text.

        Args:
            text (str): Text to analyze.

        Returns:
            tuple: (primary_lang, list of LangSegment for foreign parts)
        """
        cleaned = self.clean_text(text)

        if len(cleaned) < self.min_text_length:
            self.stats[self.default_lang] += 1
            return (self.default_lang, [])

        # Split into words with positions
        words = []
        for match in re.finditer(r'\S+', cleaned):
            words.append((match.group(), match.start(), match.end()))

        if len(words) < self.MIN_SEGMENT_WORDS:
            lang, _ = self._detect_single(cleaned)
            self.stats[lang] += 1
            return (lang, [])

        # Detect language for whole text first (primary language)
        primary_lang, primary_conf = self._detect_single(cleaned)

        # If text is short or low confidence, don't try segmentation
        if len(words) < self.WINDOW_SIZE or primary_conf < self.confidence_threshold:
            self.stats[primary_lang] += 1
            return (primary_lang, [])

        # Sliding window detection
        window_langs = []
        for i in range(0, len(words), self.WINDOW_STEP):
            window_words = words[i:i + self.WINDOW_SIZE]
            if len(window_words) < self.MIN_SEGMENT_WORDS:
                continue

            window_text = ' '.join(w[0] for w in window_words)
            lang, conf = self._detect_single(window_text)

            if conf >= self.confidence_threshold:
                start_pos = window_words[0][1]
                end_pos = window_words[-1][2]
                window_langs.append((lang, start_pos, end_pos, conf))

        # Find majority language from windows
        if window_langs:
            lang_counts = Counter(wl[0] for wl in window_langs)
            majority_lang = lang_counts.most_common(1)[0][0]
        else:
            majority_lang = primary_lang

        # Find foreign segments (different from majority)
        foreign_segments = []
        current_foreign = None

        for lang, start, end, conf in window_langs:
            if lang != majority_lang and lang != self.default_lang:
                if current_foreign is None:
                    current_foreign = {
                        'lang': lang,
                        'start': start,
                        'end': end
                    }
                elif current_foreign['lang'] == lang:
                    # Extend current segment
                    current_foreign['end'] = end
                else:
                    # Different foreign language, save current and start new
                    foreign_segments.append(LangSegment(
                        text=cleaned[current_foreign['start']:current_foreign['end']],
                        lang=current_foreign['lang'],
                        start=current_foreign['start'],
                        end=current_foreign['end']
                    ))
                    current_foreign = {
                        'lang': lang,
                        'start': start,
                        'end': end
                    }
            else:
                # Back to majority language, save any current foreign segment
                if current_foreign is not None:
                    foreign_segments.append(LangSegment(
                        text=cleaned[current_foreign['start']:current_foreign['end']],
                        lang=current_foreign['lang'],
                        start=current_foreign['start'],
                        end=current_foreign['end']
                    ))
                    current_foreign = None

        # Don't forget last segment
        if current_foreign is not None:
            foreign_segments.append(LangSegment(
                text=cleaned[current_foreign['start']:current_foreign['end']],
                lang=current_foreign['lang'],
                start=current_foreign['start'],
                end=current_foreign['end']
            ))

        # Filter out very short foreign segments
        foreign_segments = [
            seg for seg in foreign_segments
            if len(seg.text.split()) >= self.MIN_SEGMENT_WORDS
        ]

        self.stats[majority_lang] += 1
        for seg in foreign_segments:
            self.stats[seg.lang] += 1

        return (majority_lang, foreign_segments)

    def get_stats(self):
        """
        Get language usage statistics.

        Returns:
            dict: Dictionary with language codes as keys and element counts.
        """
        if not self.stats:
            return {}

        return dict(self.stats.most_common())

    def reset_stats(self):
        """Reset language statistics."""
        self.stats.clear()


def get_detector():
    """
    Get or create the global language detector instance.

    Returns:
        LanguageDetector: The global detector instance.
    """
    global _detector_instance
    if _detector_instance is None:
        _detector_instance = LanguageDetector()
    return _detector_instance


def detect_language(text):
    """
    Convenience function to detect language of text.

    Args:
        text (str): Text to analyze.

    Returns:
        str: TEI language ident code.
    """
    return get_detector().detect(text)
