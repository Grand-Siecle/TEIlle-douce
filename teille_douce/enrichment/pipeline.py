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
from dataclasses import dataclass, field

from lxml import etree

from teille_douce.config import (
    ENRICHMENT_CONTAINERS,
    ENRICHMENT_MIN_TEXT_LENGTH,
    ENRICHMENT_MAX_MISALIGNED_RATIO,
)
from ..constants import XML_ID, XML_LANG
from ..utils.xml import content_root, local_tag as _local
from .extractor import extract_spans
from .dehyphenation import dehyphenate
from ..teiheader import declare_pos_tagsets
from .client import tag_texts, get_model, check_server
from .aligner import align_tokens
from .segmenter import segment_sentences, chain_cross_container
from .reconstructor import rebuild_container

logger = logging.getLogger(__name__)



def new_stats():
    """The shape of what one document's enrichment reports.

    Defined once so that a caller — a test included — cannot build a
    partial one and have a counter raise the first time it is incremented.

    `containers_failed` used to be a single counter for four unrelated
    causes, incremented from four places. A guard refusing to anchor
    annotations to the wrong characters and a service refusing to answer
    are not the same fact, and blocks 2 and 3 of the report cannot be
    written honestly while they share a number. The total stays, so
    nothing that already reads it changes.
    """
    return {
        "containers_found": 0,
        "containers_enriched": 0,
        "containers_skipped": 0,
        "containers_failed": 0,      # the total, unchanged
        "containers_broken": 0,      # an exception here — needs a human
        "containers_refused": 0,     # the service answered no
        "containers_unanchored": 0,  # a guard refused it — block 2
        "containers_unsent": 0,      # the breaker never sent it
        "tokens_total": 0,
        "sentences_total": 0,
        "server_unavailable": False,
    }


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
    stats = new_stats()

    # Check PyHellen availability. The server is probed once at startup
    # AND here, per document: it can die mid-run, and that case used to
    # return all-zero stats that no console message keyed on — the
    # document ended up silently unannotated (audit 2.7).
    if not check_server():
        logger.warning("PyHellen server not available, skipping enrichment")
        stats["server_unavailable"] = True
        return stats

    # Front matter travels with the body: a title page is the most
    # metadata-dense page of a volume, it must not stay unannotated.
    body = content_root(root)
    if body is None:
        logger.warning("No <text> element found, skipping enrichment")
        return stats

    # Collect all containers in document order
    containers = [e for e in body.iter() if _local(e.tag) in ENRICHMENT_CONTAINERS]

    stats["containers_found"] = len(containers)
    logger.debug("Found %d containers to enrich", len(containers))

    # Pass 1 — sequential, cheap: extract text, dehyphenate, derive the
    # language blocks and build the HTTP requests of every container.
    jobs = []
    for i, container in enumerate(containers):
        try:
            jobs.append(_prepare_container(container, i, stats))
        except Exception as e:
            logger.error(f"Failed to prepare container {i}: {e}", exc_info=True)
            stats["containers_failed"] += 1
            stats["containers_broken"] += 1
            jobs.append(None)

    # Pass 2 — concurrent HTTP tagging over one keep-alive connection
    # (audit 3.3): the dominant cost of a full run was thousands of
    # sequential PyHellen round-trips per document.
    flat_requests = []
    owners = []  # parallel: (job, index of the request within the job)
    for job in jobs:
        if job is None:
            continue
        for k, (text, model, _base, _lang) in enumerate(job.requests):
            flat_requests.append((text, model))
            owners.append((job, k))

    if flat_requests:
        outcomes = tag_texts(flat_requests, progress_callback=progress_callback)
        for (job, k), outcome in zip(owners, outcomes):
            job.outcomes[k] = outcome

    # Pass 3 — sequential DOM mutation: align, segment, rebuild.
    all_sentences = []
    for job in jobs:
        if job is None:
            all_sentences.append([])
            continue
        try:
            sentences = _finish_container(job, stats)
            all_sentences.append(sentences if sentences else [])
        except Exception as e:
            logger.error(
                f"Failed to enrich container {job.index}: {e}", exc_info=True
            )
            stats["containers_failed"] += 1
            stats["containers_broken"] += 1
            all_sentences.append([])

    # Cross-container sentence chaining
    chain_cross_container(all_sentences)

    # The header can only name the tagsets once it knows which models
    # ran: declared with the skeleton, they would announce annotations a
    # run with enrichment disabled does not carry.
    declare_pos_tagsets(root, sorted(stats.get("languages_tagged", ())))

    logger.debug(
        "Enrichment complete: %d enriched, %d tokens, %d sentences",
        stats['containers_enriched'], stats['tokens_total'], stats['sentences_total'],
    )

    return stats


@dataclass
class _ContainerJob:
    """One container's state between the three enrichment passes."""
    container: object
    index: int
    spans: list
    offset_map: list
    hyphen_joins: object
    primary_lang: str
    # [(stripped_text, model, absolute_base_offset, effective_lang)]
    requests: list
    outcomes: list = field(default_factory=list)


def _prepare_container(container, container_index, stats):
    """
    Pass 1: everything before HTTP — extract, dehyphenate, language
    blocks, request building.

    Returns:
        _ContainerJob or None when the container is skipped (no model,
        too short, nothing to tag).
    """
    primary_lang = container.get(XML_LANG, "und")
    primary_model = get_model(primary_lang)
    if primary_model is None:
        stats["containers_skipped"] += 1
        return None

    raw_text, spans = extract_spans(container)
    if len(raw_text.strip()) < ENRICHMENT_MIN_TEXT_LENGTH:
        stats["containers_skipped"] += 1
        return None

    dehyphenated_text, offset_map, hyphen_joins = dehyphenate(raw_text, spans)
    blocks = _language_blocks_from_spans(
        spans, offset_map, len(dehyphenated_text), primary_lang
    )

    requests = []
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

        # The tagger strips its input; track the dropped leading
        # whitespace so token offsets can be rebased correctly.
        stripped = block_text.strip()
        leading_ws = len(block_text) - len(block_text.lstrip())
        requests.append((stripped, model, b_start + leading_ws, effective_lang))

    if not requests:
        stats["containers_skipped"] += 1
        return None

    job = _ContainerJob(
        container=container, index=container_index, spans=spans,
        offset_map=offset_map, hyphen_joins=hyphen_joins,
        primary_lang=primary_lang, requests=requests,
    )
    job.outcomes = [None] * len(requests)
    return job


def _finish_container(job, stats):
    """
    Pass 3: collect the container's tagging outcomes, then align,
    segment and rebuild — sequential DOM mutation.

    Returns:
        list[Sentence] or None when the container failed or was skipped.
    """
    container = job.container
    corresp = container.get("corresp") or "?"
    tokens = []
    misaligned = 0
    for (_text, _model, base, effective_lang), outcome in zip(job.requests, job.outcomes):
        if outcome is None or outcome[0] != "ok":
            reason = outcome[1] if outcome else "no outcome"
            logger.warning("Container %s: tagging failed (%s)", corresp, reason)
            stats["containers_failed"] += 1
            # The breaker refusing to send is not the service refusing to
            # answer: one is this pipeline protecting a server from hours
            # of sequential timeouts, the other is the server saying no.
            if outcome and str(outcome[0]) == "breaker":
                stats["containers_unsent"] += 1
            else:
                stats["containers_refused"] += 1
            return None
        _status, block_tokens, block_misaligned = outcome

        # Audit 2.12: a token the aligner could not find is anchored at
        # the cursor and drags every following token of ITS OWN block
        # with it — the cascade never crosses a block boundary, so the
        # gate is per block. Judging the container as a whole would let
        # a large clean block hide a short quotation whose annotations
        # are entirely misplaced.
        if block_misaligned and block_tokens:
            block_ratio = block_misaligned / len(block_tokens)
            if block_ratio > ENRICHMENT_MAX_MISALIGNED_RATIO:
                logger.warning(
                    "Container %s: %d/%d tokens of a %s block could not be "
                    "anchored (%.0f%% > %.0f%%) — left unenriched",
                    corresp, block_misaligned, len(block_tokens), effective_lang,
                    100 * block_ratio, 100 * ENRICHMENT_MAX_MISALIGNED_RATIO,
                )
                stats["containers_failed"] += 1
                # Block 2, not block 3: more than a fifth of a block could
                # not be anchored, so the guard refused to attach
                # annotations to the wrong characters. That is the guard
                # working, and its level drops from ERROR to WARNING.
                stats["containers_unanchored"] += 1
                return None

        misaligned += block_misaligned
        for tok in block_tokens:
            tok.char_start += base
            tok.char_end += base
            tok.origin_lang = effective_lang
        tokens.extend(block_tokens)

    if not tokens:
        stats["containers_skipped"] += 1
        return None

    if misaligned:
        logger.warning(
            "Container %s: %d/%d tokens anchored by cursor fallback",
            corresp, misaligned, len(tokens),
        )

    aligned = align_tokens(tokens, job.spans, job.offset_map, job.hyphen_joins)

    # The scope of the deterministic sentence ids (audit 2.8) pairs the
    # container's @corresp with its document-order index: @corresp alone
    # is NOT unique — one source zone can be reached through more than
    # one container (a figure's caption <ab> carries the zone of the
    # <figure> around it), and nothing forbids a document from repeating
    # a zone reference.
    scope_ref = container.get("corresp") or container.get(XML_ID) or ""
    sentences = segment_sentences(
        aligned, id_scope=f"{job.index}\x1f{scope_ref}"
    )

    rebuild_container(container, sentences, primary_lang=job.primary_lang)

    stats["containers_enriched"] += 1
    stats["tokens_total"] += len(tokens)
    stats["sentences_total"] += len(sentences)
    # Which tagsets the header will have to declare: the languages a
    # model actually tagged, not the ones the pipeline could tag.
    stats.setdefault("languages_tagged", set()).update(
        tok.origin_lang for tok in tokens if tok.origin_lang
    )

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
