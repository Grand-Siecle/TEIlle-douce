# -----------------------------------------------------------
# Async client for the VieuxParler text modernization API.
# -----------------------------------------------------------
"""
Text modernization module.

Translates historical French text to modern French using the
VieuxParler API (LSTM Fairseq/FreEM model). Uses httpx with
asyncio for concurrent batch requests.
"""

import asyncio

import httpx

from config import MODERNIZE_API, MODERNIZE_BATCH_SIZE, MODERNIZE_TIMEOUT


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
    """Send all batches concurrently and reassemble results."""
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

    any_success = False
    for (start, _), response in zip(batches, responses):
        if isinstance(response, Exception) or response is None:
            continue
        for j, t in enumerate(response):
            results[start + j] = t
        any_success = True

    return results if any_success else None


async def _send_batch(client, base_url, batch_texts):
    """POST a single batch to /translate/batch."""
    try:
        r = await client.post(
            f"{base_url}/translate/batch",
            json={"texts": batch_texts, "batch_size": MODERNIZE_BATCH_SIZE},
        )
        r.raise_for_status()
        data = r.json()
        return data.get("translations")
    except Exception:
        return None
