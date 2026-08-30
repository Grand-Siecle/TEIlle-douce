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
import unicodedata
from collections import namedtuple
from difflib import SequenceMatcher

import httpx

from config import (
    DEBUG,
    MODERNIZE_API,
    MODERNIZE_BATCH_SIZE,
    MODERNIZE_CERT_THRESHOLDS,
    MODERNIZE_MAX_CONCURRENT,
    MODERNIZE_TIMEOUT,
)

logger = logging.getLogger(__name__)

# Word-count tolerance: modernized text may have at most
# orig_words * TOLERANCE_RATIO + TOLERANCE_ABS extra words.
TOLERANCE_RATIO = 1.5
TOLERANCE_ABS = 2

# Minimum character-level similarity (after normalization) between
# original and modernized text.  Below this threshold the API output
# is considered hallucinated.  Legitimate old-French → modern-French
# changes (cognoiſtre → connaître) stay above ~0.73 after normalization.
SIMILARITY_MIN = 0.8

# Lines matching this pattern have no real textual content to modernize.
_SKIP_RE = re.compile(r'^[\s\W\d]*$')

# Minimum number of alphabetic characters for a line to be worth modernizing.
_MIN_ALPHA = 3


def _normalize_for_comparison(text):
    """Normalize text for similarity comparison: long-s, accents, case."""
    text = text.replace("ſ", "s").replace("¬", "").lower()
    nfkd = unicodedata.normalize("NFKD", text)
    return "".join(c for c in nfkd if unicodedata.category(c) != "Mn")


def similarity(original, modernized):
    """
    Character-level similarity of a modernized line to its original.

    Already computed inside _is_divergent to reject hallucinations; also
    the honest basis for the reading's @cert (audit 1.12), so it lives
    in one place.
    """
    if not original or not modernized:
        return 0.0
    return SequenceMatcher(
        None, _normalize_for_comparison(original), _normalize_for_comparison(modernized)
    ).ratio()


def _is_divergent(original, modernized):
    """
    Check if modernized text diverges too much from original.

    Returns True if:
    - the word count difference exceeds tolerance, OR
    - the character-level similarity (after normalization) is too low.
    """
    if not original or not modernized:
        return False
    orig_wc = len(original.split())
    mod_wc = len(modernized.split())
    if orig_wc == 0:
        return mod_wc > TOLERANCE_ABS
    if mod_wc > orig_wc * TOLERANCE_RATIO + TOLERANCE_ABS:
        return True
    # Character-level similarity check (catches hallucinations with
    # similar word count, e.g. Greek OCR artifacts → invented French).
    if similarity(original, modernized) < SIMILARITY_MIN:
        return True
    return False


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
    sendable_idx = [
        i for i, t in enumerate(texts)
        if not _SKIP_RE.match(t) and sum(c.isalpha() for c in t) >= _MIN_ALPHA
    ]
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

    for (start, _batch_texts), response in zip(batches, responses):
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

    For each line ending with ¬ (or -), finds the next same-zone line and
    merges the word fragment(s). A word can be split across MORE than two
    lines (e.g. "ex¬" / "tra¬" / "ordinai¬" / "rement grand" — a page break
    landing mid-word can do this). To handle that, the algorithm walks the
    chain forward: as long as the candidate line is itself just a single
    hyphenated fragment (one token, ending in ¬/-), it is absorbed into the
    accumulating fragment and the walk continues to the next same-zone
    line; the chain stops at the first line that actually carries more than
    one fragment (i.e. the word's ending plus following text, or simply
    isn't a lone fragment).

    The full reconstructed word is written onto line i, onto every
    intermediate chain line, and onto the terminal line — redundant on
    purpose, so the modernization API sees a complete word on every line
    touched by the split:
    - Line N:   "...le souv¬"        -> "...le souverain"
    - Line N+1: "erain Arbitre:..."  -> "souverain Arbitre:..."
    (and, for a 3+-line split, every line in between also gets the full
    word on its own).

    That repetition is INPUT ONLY. It must not reach the output, or any
    extraction of the modernized text yields doubled words (audit 1.12);
    the returned `carried` set names the lines whose first word belongs
    to an earlier line, so the caller can drop it after modernization.

    Lines of different zone types (e.g. MainZone vs RunningTitleZone)
    are never merged, preventing cross-container corruption.

    Args:
        texts: List of line texts.
        zone_types: Optional list of zone type strings (same length as texts).
                    If None, joins with immediately next line (legacy behavior).

    Returns:
        tuple: (joined_texts, carried) — the rejoined lines, and the
        indices of those that open with a word carried over from an
        earlier line.
    """
    joined = list(texts)
    # Lines that START with a word carried over from an earlier line
    # (see the docstring): the caller needs them to undo the repetition.
    carried = set()

    def next_same_zone(idx, zone):
        for j in range(idx + 1, len(joined)):
            if zone_types is None:
                return j
            if zone_types[j] == zone:
                return j
        return None

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

        my_zone = zone_types[i] if zone_types else None
        j = next_same_zone(i, my_zone)
        if j is None:
            continue

        # Walk the hyphen chain forward, absorbing every line that is
        # itself nothing but a single dangling fragment, until we reach
        # the line that actually terminates the word.
        fragment = suffix
        chain = []
        while True:
            cand = joined[j]
            if not cand or not cand.strip():
                j = None
                break
            cand_words = cand.split()
            cand_stripped = cand.rstrip()
            is_single_fragment = (
                len(cand_words) == 1
                and (cand_stripped.endswith("¬") or cand_stripped.endswith("-"))
            )
            if not is_single_fragment:
                break
            fragment += cand_words[0][:-1]
            chain.append(j)
            nxt = next_same_zone(j, my_zone)
            if nxt is None:
                j = None
                break
            j = nxt

        if j is None:
            # Chain never reached a resolving line; leave as-is.
            # strip_residual_hyphens() is the final backstop for any ¬
            # that survives to a modernized <reg>.
            continue

        next_line = joined[j]
        next_words = next_line.split(None, 1)
        next_first = next_words[0] if next_words else ""

        full_word = fragment + next_first

        joined[i] = prefix_line + full_word
        for c in chain:
            joined[c] = full_word
            carried.add(c)
        joined[j] = full_word + (" " + next_words[1] if len(next_words) > 1 else "")
        carried.add(j)

    return joined, carried


# A modernized line and how far the API moved it. `cert` is a TEI
# certainty value; None when there is no basis to judge.
Reading = namedtuple("Reading", "text cert")


def drop_carried_words(texts, carried):
    """
    Remove, from each carried line, the word that belongs to an earlier one.

    Counterpart of dehyphenate_lines' deliberate repetition: the word
    stays on the line where it STARTS, and the lines that merely
    continue it drop their leading token (a line that was nothing but a
    fragment thus ends up empty, which is what it contributes of its
    own). Lives next to dehyphenate_lines because the two share one
    invariant — change how the chain is written and this must follow.

    Caveat: the API is allowed to retokenize (see TOLERANCE_RATIO), so
    "one leading token" is a best effort. It only ever applies to lines
    the API actually changed, which bounds the damage to a line that was
    being rewritten anyway.
    """
    out = list(texts)
    for idx in carried:
        if idx >= len(out) or not out[idx]:
            continue
        parts = out[idx].split(None, 1)
        out[idx] = parts[1] if len(parts) > 1 else ""
    return out


def grade_readings(sent, returned, carried):
    """
    Turn the API's answers into readings, or None where it changed nothing.

    Both decisions — "is this a modernization at all?" and "how far did
    it stray?" — are taken between what was SENT and what came BACK.
    Comparing against the raw diplomatic line instead would count
    dehyphenation itself as an editorial modernization and grade a
    mechanical word-rejoin as a heavy rewrite (both observed).

    Args:
        sent: the dehyphenated lines handed to the API.
        returned: its answers, aligned.
        carried: indices whose first word belongs to an earlier line.

    Returns:
        list[Reading | None]: one per line, None where nothing changed.
    """
    texts = drop_carried_words(returned, carried)
    readings = []
    for i, (before, after) in enumerate(zip(sent, returned)):
        if not after or after == before:
            readings.append(None)
            continue
        score = similarity(before, after)
        readings.append(Reading(text=texts[i], cert=_cert_for(score)))
    return readings


def _cert_for(score):
    """TEI certainty value for a similarity score."""
    for label, floor in sorted(MODERNIZE_CERT_THRESHOLDS.items(), key=lambda kv: -kv[1]):
        if score >= floor:
            return label
    # No configured floor matched: say so rather than leave the reading
    # half-attributed (teidata.certainty has a value for exactly this).
    return "unknown"


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
