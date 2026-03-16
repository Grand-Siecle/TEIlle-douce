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
)
from ..constants import NS_XML
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
    body = None
    for elem in root.iter():
        local = etree.QName(elem.tag).localname if isinstance(elem.tag, str) else elem.tag
        if local == "body":
            body = elem
            break

    if body is None:
        logger.warning("No <body> element found, skipping enrichment")
        return stats

    # Collect all containers in document order
    containers = []
    for elem in body.iter():
        local = etree.QName(elem.tag).localname if isinstance(elem.tag, str) else elem.tag
        if local in ENRICHMENT_CONTAINERS:
            containers.append(elem)

    stats["containers_found"] = len(containers)
    logger.debug("Found %d containers to enrich", len(containers))

    # Process each container sequentially
    all_sentences = []

    for i, container in enumerate(containers):
        if i > 0 and i % 100 == 0:
            logger.debug("Progress: %d/%d containers processed", i, len(containers))

        try:
            sentences = _process_container(container, stats)
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


def _process_container(container, stats):
    """
    Process a single container through the 6-phase pipeline.

    Returns:
        list[Sentence] or None: Sentences if successful, None if skipped.
    """
    # Get language
    lang = container.get(XML_LANG, "und")

    # Check if language is supported
    model = get_model(lang)
    if model is None:
        stats["containers_skipped"] += 1
        return None

    # Phase 1: Extract text
    raw_text, spans = extract_spans(container)
    clean_text = raw_text.strip()

    if len(clean_text) < ENRICHMENT_MIN_TEXT_LENGTH:
        stats["containers_skipped"] += 1
        return None

    # Phase 2: Dehyphenate
    dehyphenated_text, offset_map, hyphen_joins = dehyphenate(raw_text, spans)

    # Phase 3: NLP tagging (individual request)
    try:
        tokens = tag_text(dehyphenated_text.strip(), model)
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

    # Phase 4: Align tokens to XML positions
    aligned = align_tokens(tokens, spans, offset_map, hyphen_joins)

    # Phase 5: Segment into sentences
    sentences = segment_sentences(aligned)

    # Phase 6: Rebuild XML
    rebuild_container(container, sentences, spans)

    # Update stats
    stats["containers_enriched"] += 1
    stats["tokens_total"] += len(tokens)
    stats["sentences_total"] += len(sentences)

    return sentences
