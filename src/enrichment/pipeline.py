# -----------------------------------------------------------
# Enrichment pipeline orchestrator.
# -----------------------------------------------------------
"""
Enrichment pipeline (main orchestrator).

Coordinates the 6 phases of linguistic enrichment:
1. Extract text from containers
2. Dehyphenate split words
3. Tag text with PyHellen NLP
4. Align tokens to XML positions
5. Segment into sentences
6. Rebuild XML with annotations
"""

import logging

from lxml import etree

from config import (
    ENRICHMENT_CONTAINERS,
    ENRICHMENT_MIN_TEXT_LENGTH,
    ENRICHMENT_MAX_MISALIGNED_RATIO,
)
from ..constants import NS_XML, XML_ID
from ..utils.xml import local_tag as _local
from .extractor import extract_spans
from .dehyphenation import dehyphenate
from .client import tag_text, get_model, check_server
from .aligner import align_tokens
from .segmenter import segment_sentences, chain_cross_container
from .reconstructor import rebuild_container

logger = logging.getLogger(__name__)

XML_LANG = f"{{{NS_XML}}}lang"


def enrich_body(root, progress_callback=None):
    """
    Enrich all body containers with linguistic annotations.

    Finds all container elements (<ab>, <note>, <fw>) in the TEI body,
    processes each through the 6-phase pipeline, and rebuilds them
    with <w>, <pc>, <s>, and <lb/> elements.

    Args:
        root: The TEI root lxml Element.
        progress_callback: Optional callable(current, total) for progress updates.

    Returns:
        dict: Statistics about the enrichment process.
    """
    stats = {
        "containers_found": 0,
        "containers_enriched": 0,
        "containers_skipped": 0,
        "containers_failed": 0,
        "tokens_total": 0,
        "sentences_total": 0,
    }

    # Check PyHellen availability
    if not check_server():
        logger.warning("PyHellen server not available, skipping enrichment")
        return stats

    # Find the body element
    body = next((e for e in root.iter() if _local(e.tag) == "body"), None)
    if body is None:
        logger.warning("No <body> element found, skipping enrichment")
        return stats

    # Collect all containers in document order
    containers = [e for e in body.iter() if _local(e.tag) in ENRICHMENT_CONTAINERS]

    stats["containers_found"] = len(containers)
    logger.debug("Found %d containers to enrich", len(containers))

    # Process each container sequentially
    all_sentences = []

    for i, container in enumerate(containers):
        if i > 0 and i % 100 == 0:
            logger.debug("Progress: %d/%d containers processed", i, len(containers))

        try:
            sentences = _process_container(container, stats, container_index=i)
            all_sentences.append(sentences if sentences else [])
        except Exception as e:
            logger.error(f"Failed to enrich container {i}: {e}", exc_info=True)
            stats["containers_failed"] += 1
            all_sentences.append([])

        if progress_callback:
            progress_callback(i + 1, len(containers))

    # Cross-container sentence chaining
    chain_cross_container(all_sentences)

    logger.debug(
        "Enrichment complete: %d enriched, %d tokens, %d sentences",
        stats['containers_enriched'], stats['tokens_total'], stats['sentences_total'],
    )

    return stats


def _process_container(container, stats, container_index=0):
    """
    Process a single container through the 6-phase pipeline.

    If the container contains <foreign> elements (from language
    detection), each language block is tagged with the matching
    PyHellen model; otherwise the container is tagged with the
    primary-language model as before.

    Returns:
        list[Sentence] or None: Sentences if successful, None if skipped.
    """
    primary_lang = container.get(XML_LANG, "und")
    primary_model = get_model(primary_lang)
    if primary_model is None:
        stats["containers_skipped"] += 1
        return None

    # Phase 1: Extract text (spans carry the per-fragment language).
    raw_text, spans = extract_spans(container)
    clean_text = raw_text.strip()

    if len(clean_text) < ENRICHMENT_MIN_TEXT_LENGTH:
        stats["containers_skipped"] += 1
        return None

    # Phase 2: Dehyphenate.
    dehyphenated_text, offset_map, hyphen_joins = dehyphenate(raw_text, spans)

    # Phase 2.5: Derive language blocks on dehyphenated_text from spans.
    blocks = _language_blocks_from_spans(
        spans, offset_map, len(dehyphenated_text), primary_lang
    )

    # Phase 3: NLP tagging — one PyHellen call per block, with the
    # appropriate model. Token offsets are rebased to the dehyph text.
    try:
        tokens, misaligned = _tag_blocks(
            blocks, dehyphenated_text, primary_model, primary_lang
        )
    except ConnectionError as e:
        logger.warning(f"PyHellen connection error: {e}")
        stats["containers_failed"] += 1
        return None
    except RuntimeError as e:
        logger.warning(f"PyHellen API error: {e}")
        stats["containers_failed"] += 1
        return None

    if not tokens:
        stats["containers_skipped"] += 1
        return None

    # Audit 2.12: unfindable tokens are anchored at the cursor and drag
    # every following token with them. A few are tolerable OCR noise;
    # past the threshold the annotations would attach to the wrong
    # characters — better an unenriched container than a wrong one.
    if misaligned:
        ratio = misaligned / len(tokens)
        if ratio > ENRICHMENT_MAX_MISALIGNED_RATIO:
            logger.error(
                "Container %s: %d/%d tokens could not be anchored "
                "(%.0f%% > %.0f%%) — left unenriched",
                container.get("corresp") or "?", misaligned, len(tokens),
                100 * ratio, 100 * ENRICHMENT_MAX_MISALIGNED_RATIO,
            )
            stats["containers_failed"] += 1
            return None
        logger.warning(
            "Container %s: %d/%d tokens anchored by cursor fallback",
            container.get("corresp") or "?", misaligned, len(tokens),
        )

    # Phase 4: Align tokens to XML positions.
    aligned = align_tokens(tokens, spans, offset_map, hyphen_joins)

    # Phase 5: Segment into sentences. The scope of the deterministic
    # sentence ids (audit 2.8) pairs the container's @corresp with its
    # document-order index: @corresp alone is NOT unique (consecutive
    # <fw> lines of one zone each get their own container with the same
    # @corresp), and identical scopes would mean duplicate sentence ids.
    scope_ref = container.get("corresp") or container.get(XML_ID) or ""
    sentences = segment_sentences(
        aligned, id_scope=f"{container_index}\x1f{scope_ref}"
    )

    # Phase 6: Rebuild XML.
    rebuild_container(container, sentences, spans, primary_lang=primary_lang)

    stats["containers_enriched"] += 1
    stats["tokens_total"] += len(tokens)
    stats["sentences_total"] += len(sentences)

    return sentences


def _language_blocks_from_spans(spans, offset_map, dehyph_len, primary_lang):
    """
    Compute contiguous (start, end, lang) blocks over dehyphenated_text.

    Each dehyph character inherits the lang of the span that covers
    the raw position ``offset_map[i]``. A ``None`` span-lang is treated
    as the primary language.

    Returns:
        list[tuple[int, int, str]]: Blocks in dehyph text order.
    """
    if dehyph_len == 0 or not spans:
        return [(0, dehyph_len, primary_lang)] if dehyph_len else []

    # raw position -> lang (None → primary)
    raw_to_lang = {}
    for span in spans:
        effective = span.lang if span.lang else primary_lang
        for pos in range(span.offset_start, span.offset_end):
            raw_to_lang[pos] = effective

    blocks = []
    block_start = 0
    block_lang = raw_to_lang.get(offset_map[0], primary_lang) if offset_map else primary_lang
    for i in range(1, dehyph_len):
        lang_here = raw_to_lang.get(offset_map[i], primary_lang)
        if lang_here != block_lang:
            blocks.append((block_start, i, block_lang))
            block_start = i
            block_lang = lang_here
    blocks.append((block_start, dehyph_len, block_lang))
    return blocks


def _tag_blocks(blocks, dehyphenated_text, primary_model, primary_lang):
    """
    Tag each language block with the matching PyHellen model.

    Token offsets returned by PyHellen are block-local; this function
    rebases them to absolute positions in ``dehyphenated_text`` and
    stamps ``origin_lang`` on each token.

    Falls back to the primary model (and keeps the primary lang label)
    when no PyHellen model is configured for a detected foreign
    language — emitting a warning so the mismatch between
    SUPPORTED_LANGUAGES and PYHELLEN_MODELS is visible.
    """
    all_tokens = []
    misaligned = 0
    for b_start, b_end, b_lang in blocks:
        block_text = dehyphenated_text[b_start:b_end]
        if not block_text.strip():
            continue

        if b_lang == primary_lang:
            model, effective_lang = primary_model, primary_lang
        else:
            model = get_model(b_lang)
            if model is None:
                logger.warning(
                    "No PyHellen model configured for detected language "
                    "'%s' — tagging block with primary model '%s'. Align "
                    "SUPPORTED_LANGUAGES and PYHELLEN_MODELS to fix.",
                    b_lang, primary_lang,
                )
                model, effective_lang = primary_model, primary_lang
            else:
                effective_lang = b_lang

        # tag_text strips its input; track how many leading-whitespace
        # chars were dropped so we can rebase token offsets correctly.
        stripped_block = block_text.strip()
        leading_ws = len(block_text) - len(block_text.lstrip())
        block_tokens, block_misaligned = tag_text(stripped_block, model)
        misaligned += block_misaligned

        base = b_start + leading_ws
        for t in block_tokens:
            t.char_start += base
            t.char_end += base
            t.origin_lang = effective_lang
        all_tokens.extend(block_tokens)

    return all_tokens, misaligned
