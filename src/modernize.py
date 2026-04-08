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
import re

import httpx

from config import DEBUG, MODERNIZE_API, MODERNIZE_BATCH_SIZE, MODERNIZE_TIMEOUT, MODERNIZE_MAX_CONCURRENT

logger = logging.getLogger(__name__)

# Word-count tolerance: modernized text may have at most
# orig_words * TOLERANCE_RATIO + TOLERANCE_ABS extra words.
TOLERANCE_RATIO = 1.5
TOLERANCE_ABS = 2

# Lines matching this pattern have no real textual content to modernize.
_SKIP_RE = re.compile(r'^[\s\W\d]*$')


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

    # Filter out lines with no real textual content (whitespace, digits,
    # punctuation only) — sending them to the API wastes time and can
    # produce hallucinated output.
    sendable_idx = [i for i, t in enumerate(texts) if not _SKIP_RE.match(t)]
    if not sendable_idx:
        return None
    sendable_texts = [texts[i] for i in sendable_idx]

    modernized = asyncio.run(
        _modernize_all(sendable_texts, base_url, progress_callback)
    )
    if modernized is None:
        return None

    # Re-expand to full list, keeping originals for skipped lines.
    full = list(texts)
    for j, idx in enumerate(sendable_idx):
        full[idx] = modernized[j]
    return full


async def _modernize_all(texts, base_url, progress_callback=None):
    """Send all batches with limited concurrency, validate, retry divergent lines."""
    results = list(texts)  # pre-fill with originals as fallback
    batches = [
        (i, texts[i : i + MODERNIZE_BATCH_SIZE])
        for i in range(0, len(texts), MODERNIZE_BATCH_SIZE)
    ]
    total_batches = len(batches)
    completed = 0
    sem = asyncio.Semaphore(MODERNIZE_MAX_CONCURRENT)

    async def _tracked_batch(client, batch_texts):
        nonlocal completed
        async with sem:
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
        retry_sem = asyncio.Semaphore(MODERNIZE_MAX_CONCURRENT)

        async def _retry_one(client, orig):
            async with retry_sem:
                return await _send_batch(client, base_url, [orig], batch_size=1)

        async with httpx.AsyncClient(timeout=MODERNIZE_TIMEOUT) as client:
            retry_tasks = [
                _retry_one(client, orig)
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


def dehyphenate_lines(texts, zone_types=None):
    """
    Join words split by ¬ or - across lines of the same zone type.

    For each line ending with ¬ (or -), finds the next line with the
    same zone type and merges the word fragments. The full word appears
    on BOTH lines to give the modernization API maximum context:
    - Line N: "...le souverain"  (fragment replaced by full word)
    - Line M: "souverain Arbitre:..."  (full word prepended)

    Lines of different zone types (e.g. MainZone vs RunningTitleZone)
    are never merged, preventing cross-container corruption.

    Args:
        texts: List of line texts.
        zone_types: Optional list of zone type strings (same length as texts).
                    If None, joins with immediately next line (legacy behavior).

    Returns:
        list: Modified texts with hyphenated words rejoined.
    """
    joined = list(texts)

    for i in range(len(joined) - 1):
        line = joined[i]
        if not line:
            continue
        stripped = line.rstrip()
        if not stripped.endswith("¬") and not stripped.endswith("-"):
            continue

        # Find the word fragment before the hyphen
        before_hyphen = stripped[:-1]
        last_space = before_hyphen.rfind(" ")
        if last_space == -1:
            suffix = before_hyphen
            prefix_line = ""
        else:
            suffix = before_hyphen[last_space + 1:]
            prefix_line = before_hyphen[:last_space + 1]

        # Find the next line with the same zone type
        my_zone = zone_types[i] if zone_types else None
        target = None
        for j in range(i + 1, len(joined)):
            if zone_types is None:
                target = j
                break
            if zone_types[j] == my_zone:
                target = j
                break

        if target is None:
            continue

        next_line = joined[target]
        if not next_line or not next_line.strip():
            continue
        next_words = next_line.split(None, 1)
        next_first = next_words[0] if next_words else ""

        # Full word = suffix + first word of continuation line
        full_word = suffix + next_first

        # Both lines get the full word for maximum API context
        joined[i] = prefix_line + full_word
        joined[target] = full_word + (" " + next_words[1] if len(next_words) > 1 else "")

    return joined


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
            logger.debug("_send_batch error: %s: %r", type(e).__name__, e)
        return None
