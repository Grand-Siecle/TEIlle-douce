# -----------------------------------------------------------
# PyHellen NLP API client for linguistic annotation.
# -----------------------------------------------------------
"""
PyHellen API client.

Sends text to PyHellen for tokenization, POS tagging, and lemmatization.
"""

import json
import logging
from dataclasses import dataclass
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

from config import PYHELLEN_URL, PYHELLEN_TIMEOUT, PYHELLEN_MODELS

logger = logging.getLogger(__name__)


@dataclass
class NLPToken:
    """A single token annotated by PyHellen."""
    form: str
    lemma: str
    pos: str
    morph: str
    treated: str
    is_punctuation: bool
    char_start: int = 0
    char_end: int = 0
    origin_lang: str = ""  # TEI ident of the PyHellen model that tagged it


def get_model(lang_ident):
    """
    Get PyHellen model name for a TEI language ident.

    Args:
        lang_ident: TEI language identifier (e.g. "fra", "lat", "grc").

    Returns:
        str or None: Model name or None if unsupported.
    """
    return PYHELLEN_MODELS.get(lang_ident)


def check_server():
    """
    Check if PyHellen server is reachable.

    Returns:
        bool: True if server responds to health check.
    """
    try:
        req = Request(f"{PYHELLEN_URL}/api/languages", method="GET")
        with urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except (URLError, HTTPError, OSError):
        return False


def tag_text(text, model):
    """
    Send a single text to PyHellen for annotation.

    Args:
        text: Text string to annotate.
        model: PyHellen model name.

    Returns:
        list[NLPToken]: Annotated tokens with character offsets.

    Raises:
        ConnectionError: If server is unreachable.
        RuntimeError: If API returns an error.
    """
    url = f"{PYHELLEN_URL}/api/tag/{model}"
    payload = json.dumps({"text": text}).encode("utf-8")
    req = Request(url, data=payload, method="POST")
    req.add_header("Content-Type", "application/json")

    try:
        with urlopen(req, timeout=PYHELLEN_TIMEOUT) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except HTTPError as e:
        raise RuntimeError(f"PyHellen API error {e.code}: {e.read().decode()}")
    except (URLError, OSError) as e:
        raise ConnectionError(f"PyHellen unreachable: {e}")

    return _process_response(data, text)


def _process_response(data, text):
    """Parse a PyHellen response and anchor its tokens in *text*.

    Returns:
        tuple: (tokens, misaligned) — see _align_tokens.
    """
    raw_tokens = _parse_token_list(data.get("result", []))
    return _align_tokens(raw_tokens, text)


def _parse_token_list(token_list):
    """
    Parse a list of raw token dicts from PyHellen.

    Handles field name variations between models:
    - freem uses "POS" (uppercase)
    - lasla uses "pos" (lowercase)
    """
    tokens = []
    for t in token_list:
        form = t.get("form", "")
        pos = t.get("POS") or t.get("pos", "")
        lemma = t.get("lemma", "")
        morph = t.get("morph", "")
        treated = t.get("treated", form)

        is_punct = len(form) == 1 and not form.isalnum()

        tokens.append({
            "form": form,
            "lemma": lemma,
            "pos": pos,
            "morph": morph,
            "treated": treated,
            "is_punctuation": is_punct,
        })

    return tokens


def _align_tokens(raw_tokens, text):
    """
    Compute character offsets for each token in the source text.

    Scans through the text to find each token's form, assigning
    char_start and char_end. Falls back to case-insensitive matching
    if exact match fails.

    Returns:
        tuple: (tokens, misaligned) — misaligned counts the tokens that
        could not be found at all and were placed at the cursor
        (audit 2.12: this repli was silent and each occurrence degrades
        the alignment of every following token).
    """
    result = []
    cursor = 0
    misaligned = 0

    for t in raw_tokens:
        form = t["form"]
        # Try exact match first
        idx = text.find(form, cursor)

        # Fallback: case-insensitive
        if idx == -1:
            idx = text.lower().find(form.lower(), cursor)

        # Fallback: small window around cursor
        if idx == -1:
            window = min(cursor + len(form) + 20, len(text))
            idx = text.lower().find(form.lower(), max(0, cursor - 2), window)

        if idx == -1:
            # Last resort: place at cursor position — counted, because
            # the error then propagates to every following token.
            idx = cursor
            misaligned += 1

        char_end = idx + len(form)

        result.append(NLPToken(
            form=form,
            lemma=t["lemma"],
            pos=t["pos"],
            morph=t["morph"],
            treated=t["treated"],
            is_punctuation=t["is_punctuation"],
            char_start=idx,
            char_end=char_end,
        ))

        cursor = char_end

    return result, misaligned
