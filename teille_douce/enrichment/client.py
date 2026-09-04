# -----------------------------------------------------------
# PyHellen NLP API client for linguistic annotation.
# -----------------------------------------------------------
"""
PyHellen API client.

Sends text to PyHellen for tokenization, POS tagging, and lemmatization.
"""

import asyncio
import logging
from dataclasses import dataclass
from urllib.request import Request, urlopen
from urllib.error import URLError, HTTPError

import httpx

from teille_douce.config import (
    HEALTH_TIMEOUT,
    PYHELLEN_URL,
    PYHELLEN_TIMEOUT,
    PYHELLEN_MODELS,
    PYHELLEN_MAX_CONCURRENT,
    PYHELLEN_MAX_CONSECUTIVE_FAILURES,
)

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
        with urlopen(req, timeout=HEALTH_TIMEOUT) as resp:
            return resp.status == 200
    except (URLError, HTTPError, OSError):
        return False


def _process_response(data, text):
    """Parse a PyHellen response and anchor its tokens in *text*.

    Returns:
        tuple: (tokens, misaligned) — see _align_tokens.
    """
    raw_tokens = _parse_token_list(data.get("result", []))
    return _align_tokens(raw_tokens, text)


def tag_texts(requests, progress_callback=None, transport=None):
    """Synchronous entry point for concurrent tagging — see _tag_texts_async."""
    return asyncio.run(
        _tag_texts_async(requests, progress_callback=progress_callback,
                         transport=transport)
    )


async def _tag_texts_async(requests, progress_callback=None, transport=None):
    """
    Tag many texts concurrently over one keep-alive connection (audit 3.3).

    The old client opened one blocking urllib connection per container:
    thousands of sequential round-trips per document. This sends up to
    PYHELLEN_MAX_CONCURRENT requests at a time through a single
    httpx.AsyncClient — the same model modernize.py already uses.

    A circuit breaker (audit 2.7) opens after
    PYHELLEN_MAX_CONSECUTIVE_FAILURES consecutive failures: a frozen
    server must not turn into hours of sequential timeouts. Pending
    requests are then skipped and reported as such. One success resets
    the counter.

    Args:
        requests: list of (text, model) pairs.
        progress_callback: optional callable(done, total).
        transport: optional httpx transport (tests: MockTransport).

    Returns:
        list: one outcome per request, aligned with the input —
        ("ok", tokens, misaligned) | ("error", message) |
        ("breaker", message).
    """
    results = [None] * len(requests)
    sem = asyncio.Semaphore(PYHELLEN_MAX_CONCURRENT)
    state = {"consecutive": 0, "open": False, "done": 0}

    async def _one(client, i, text, model):
        async with sem:
            if state["open"]:
                results[i] = ("breaker", "circuit open — request not sent")
            else:
                try:
                    resp = await client.post(
                        f"{PYHELLEN_URL}/api/tag/{model}", json={"text": text}
                    )
                    resp.raise_for_status()
                    tokens, misaligned = _process_response(resp.json(), text)
                    results[i] = ("ok", tokens, misaligned)
                    state["consecutive"] = 0
                except Exception as e:
                    state["consecutive"] += 1
                    results[i] = ("error", f"{type(e).__name__}: {e}")
                    if (state["consecutive"] >= PYHELLEN_MAX_CONSECUTIVE_FAILURES
                            and not state["open"]):
                        state["open"] = True
                        logger.error(
                            "PyHellen circuit opened after %d consecutive "
                            "failures — remaining requests skipped",
                            state["consecutive"],
                        )
        state["done"] += 1
        if progress_callback:
            progress_callback(state["done"], len(requests))

    async with httpx.AsyncClient(timeout=PYHELLEN_TIMEOUT, transport=transport) as client:
        await asyncio.gather(
            *(_one(client, i, text, model)
              for i, (text, model) in enumerate(requests))
        )
    return results


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
    # Computed on the first miss only: a container whose tokens all match
    # exactly — the normal case — never needs it, and the win is not
    # paying for it twice on each of the ones that do.
    text_lower = None

    for t in raw_tokens:
        form = t["form"]
        # Try exact match first
        idx = text.find(form, cursor)

        # Fallback: case-insensitive. The lowered text is computed once
        # per container, not once per token (audit 3.11) — it is the same
        # string for every token of the block.
        if idx == -1:
            if text_lower is None:
                text_lower = text.lower()
            idx = text_lower.find(form.lower(), cursor)

        # Fallback: small window around cursor
        if idx == -1:
            window = min(cursor + len(form) + 20, len(text))
            idx = text_lower.find(form.lower(), max(0, cursor - 2), window)

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
