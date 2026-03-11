# -----------------------------------------------------------
# Async client for the VieuxParler text modernization API.
# -----------------------------------------------------------
"""
Text modernization module.

Translates historical French text to modern French using the
VieuxParler API (LSTM Fairseq/FreEM model). Uses httpx with
asyncio for concurrent batch requests.

Includes a validation step: lines where the API output diverges
too much from the original (word count mismatch) are retried
individually (batch_size=1). If the retry also diverges, the
original text is kept.
"""

import asyncio
import logging

import httpx

from config import DEBUG, MODERNIZE_API, MODERNIZE_BATCH_SIZE, MODERNIZE_TIMEOUT

logger = logging.getLogger(__name__)

# Word-count tolerance: modernized text may have at most
# orig_words * TOLERANCE_RATIO + TOLERANCE_ABS extra words.
TOLERANCE_RATIO = 1.5
TOLERANCE_ABS = 2


def _is_divergent(original, modernized):
    """
    Check if modernized text diverges too much from original.

    Returns True if the word count difference exceeds tolerance.
    """
    if not original or not modernized:
        return False
    orig_wc = len(original.split())
    mod_wc = len(modernized.split())
    if orig_wc == 0:
        return mod_wc > TOLERANCE_ABS
    return mod_wc > orig_wc * TOLERANCE_RATIO + TOLERANCE_ABS


def check_api(lang="fra"):
    """
    Check if the modernization API is reachable.

    Args:
        lang: TEI language ident to check.

    Returns:
        bool: True if the API /health endpoint responds OK.
    """
    base_url = MODERNIZE_API.get(lang)
    if not base_url:
        return False
    try:
        with httpx.Client(timeout=10) as client:
            r = client.get(f"{base_url}/health")
            return r.status_code == 200
    except httpx.HTTPError:
        return False


def modernize_texts(texts, lang="fra", progress_callback=None):
    """
    Modernize a list of text lines via the VieuxParler API.

    Splits *texts* into batches and sends them concurrently.
    Lines whose batch fails keep their original text.
    Lines where the API output diverges (word count mismatch)
    are retried individually.

    Args:
        texts: List of original text strings.
        lang: TEI language ident (used to pick the API URL).
        progress_callback: Optional callable(completed, total) for progress.

    Returns:
        list or None: Modernized texts (same length as *texts*),
                      or None if the language has no configured API
                      or every batch failed.
    """
    base_url = MODERNIZE_API.get(lang)
    if not base_url:
        return None

    return asyncio.run(_modernize_all(texts, base_url, progress_callback))


async def _modernize_all(texts, base_url, progress_callback=None):
    """Send all batches concurrently, validate, retry divergent lines."""
    results = list(texts)  # pre-fill with originals as fallback
    batches = [
        (i, texts[i : i + MODERNIZE_BATCH_SIZE])
        for i in range(0, len(texts), MODERNIZE_BATCH_SIZE)
    ]
    total_batches = len(batches)
    completed = 0

    async def _tracked_batch(client, batch_texts):
        nonlocal completed
        result = await _send_batch(client, base_url, batch_texts)
        completed += 1
        if progress_callback:
            progress_callback(completed, total_batches)
        return result

    async with httpx.AsyncClient(timeout=MODERNIZE_TIMEOUT) as client:
        tasks = [
            _tracked_batch(client, batch_texts)
            for _, batch_texts in batches
        ]
        responses = await asyncio.gather(*tasks, return_exceptions=True)

    # Phase 1: collect results and find divergent lines
    any_success = False
    divergent = []  # list of (global_index, original_text)

    for (start, batch_texts), response in zip(batches, responses):
        if isinstance(response, Exception) or response is None:
            if DEBUG:
                logger.debug(
                    "Modernize batch at index %d failed: %s", start, response
                )
            continue
        any_success = True
        for j, mod in enumerate(response):
            idx = start + j
            orig = texts[idx]
            if _is_divergent(orig, mod):
                if DEBUG:
                    logger.debug(
                        "Divergent line %d: orig(%d words)=%r → mod(%d words)=%r",
                        idx, len(orig.split()), orig[:80],
                        len(mod.split()), mod[:80],
                    )
                divergent.append((idx, orig))
            else:
                results[idx] = mod

    # Phase 2: retry divergent lines individually (batch_size=1)
    if divergent:
        if DEBUG:
            logger.debug(
                "Retrying %d divergent lines individually", len(divergent)
            )
        async with httpx.AsyncClient(timeout=MODERNIZE_TIMEOUT) as client:
            retry_tasks = [
                _send_batch(client, base_url, [orig], batch_size=1)
                for _, orig in divergent
            ]
            retry_responses = await asyncio.gather(
                *retry_tasks, return_exceptions=True
            )

        retried = 0
        still_bad = 0
        for (idx, orig), resp in zip(divergent, retry_responses):
            if isinstance(resp, Exception) or resp is None or not resp:
                if DEBUG:
                    logger.debug("Retry failed for line %d: %s", idx, resp)
                still_bad += 1
                continue
            mod = resp[0]
            if _is_divergent(orig, mod):
                if DEBUG:
                    logger.debug(
                        "Retry still divergent line %d: orig=%r → mod=%r",
                        idx, orig[:80], mod[:80],
                    )
                still_bad += 1
                # Keep original text (already in results)
            else:
                results[idx] = mod
                retried += 1

        if DEBUG:
            logger.debug(
                "Retry results: %d fixed, %d still divergent (kept original)",
                retried, still_bad,
            )

    return results if any_success else None


async def _send_batch(client, base_url, batch_texts, batch_size=None):
    """POST a single batch to /translate/batch."""
    if batch_size is None:
        batch_size = MODERNIZE_BATCH_SIZE
    try:
        r = await client.post(
            f"{base_url}/translate/batch",
            json={"texts": batch_texts, "batch_size": batch_size},
        )
        r.raise_for_status()
        data = r.json()
        return data.get("translations")
    except Exception as e:
        if DEBUG:
            logger.debug("_send_batch error: %s", e)
        return None
