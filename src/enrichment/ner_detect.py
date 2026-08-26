# -----------------------------------------------------------
# Phase 7: NER block extraction and inference.
# -----------------------------------------------------------
"""
NER detection pipeline (Phase 7).

Extracts text blocks from TEI containers, runs CamemBERT and/or GLiNER
inference, and returns typed NER spans.

The extraction handles three cases:
- French tokenized: <choice>/<orig>/<w> for CamemBERT, <reg> for GLiNER
- Non-French tokenized: <choice>/<orig>/<w> for GLiNER
- Raw text (no <choice>): plain text for GLiNER
"""

import logging
import re
from dataclasses import dataclass, field

from lxml import etree

try:
    import torch
    _HAS_TORCH = True
except ImportError:
    _HAS_TORCH = False

from ..constants import NS_TEI, NS_XML
from ..utils.xml import local_tag as _local
from .ner_filter import filter_spans, extract_title_from_tei

logger = logging.getLogger(__name__)

XML_LANG = f"{{{NS_XML}}}lang"
TEI_NS = f"{{{NS_TEI}}}"


# =============================================================================
# DATACLASSES
# =============================================================================


@dataclass
class NERBlock:
    """A text block extracted for NER inference."""

    lang: str  # "fra", "lat", "grc", ...
    text: str  # flat text for inference
    source: str  # "orig" | "reg" | "raw"
    container: etree._Element  # parent XML node (<ab>, <note>, etc.)
    # For source="orig": mapping (start_char, end_char) → <w> element
    char_to_w: list[tuple[int, int, etree._Element]] = field(default_factory=list)
    # For source="reg": mapping (start_char, end_char) → <reg> element
    char_to_reg: list[tuple[int, int, etree._Element]] = field(default_factory=list)


@dataclass
class NERSpan:
    """An entity detected by a NER model."""

    start_char: int
    end_char: int
    text: str
    label: str  # raw model label ("PER", "person name", etc.)
    entity_type: str  # normalized key ("person", "place", etc.)
    confidence: float
    model: str  # "camembert" | "gliner"


# =============================================================================
# LABEL MAPPING
# =============================================================================


def _build_label_map(entity_types_config):
    """Build reverse mapping from model labels to entity type keys."""
    label_map = {}
    for key, cfg in entity_types_config.items():
        if cfg.get("camembert_label"):
            label_map[cfg["camembert_label"]] = key
        if cfg.get("gliner_label"):
            label_map[cfg["gliner_label"]] = key
    return label_map


# =============================================================================
# BLOCK EXTRACTION
# =============================================================================


def _find_choices(container):
    """Find all <choice> elements that have <orig> and <reg type='modernized'>."""
    choices = []
    for elem in container.iter():
        if _local(elem.tag) == "choice":
            orig = None
            reg = None
            for child in elem:
                local = _local(child.tag)
                if local == "orig":
                    orig = child
                elif local == "reg" and child.get("type") == "modernized":
                    reg = child
            if orig is not None:
                choices.append((elem, orig, reg))
    return choices


def _extract_w_text(orig_elem):
    """
    Reconstruct flat text from <w> and <pc> elements inside <orig>.

    Returns:
        tuple: (text, char_to_w) where char_to_w maps char ranges to <w> elements.
    """
    parts = []
    char_to_w = []
    pos = 0

    for s_elem in orig_elem.iter():
        local = _local(s_elem.tag)
        if local == "w":
            token_text = s_elem.text or ""
            if not token_text:
                continue
            if pos > 0:
                parts.append(" ")
                pos += 1
            start = pos
            parts.append(token_text)
            pos += len(token_text)
            char_to_w.append((start, pos, s_elem))
        elif local == "pc":
            token_text = s_elem.text or ""
            if not token_text:
                continue
            # Punctuation: attach directly (no space before if join="left")
            join = s_elem.get("join", "")
            if join != "left" and pos > 0:
                parts.append(" ")
                pos += 1
            start = pos
            parts.append(token_text)
            pos += len(token_text)

    return "".join(parts), char_to_w


def _extract_orig_block(container, lang, choices):
    """
    Extract a block from <orig> elements (for CamemBERT on French,
    or for non-French tokenized blocks).

    Concatenates text from all <w> elements across all <choice> in the container.
    """
    all_text_parts = []
    all_char_to_w = []
    global_pos = 0

    for _choice, orig, _reg in choices:
        text, char_to_w = _extract_w_text(orig)
        if not text:
            continue
        if global_pos > 0:
            all_text_parts.append(" ")
            global_pos += 1
        offset = global_pos
        all_text_parts.append(text)
        for start, end, w_elem in char_to_w:
            all_char_to_w.append((start + offset, end + offset, w_elem))
        global_pos += len(text)

    full_text = "".join(all_text_parts)
    if not full_text.strip():
        return None

    return NERBlock(
        lang=lang,
        text=full_text,
        source="orig",
        container=container,
        char_to_w=all_char_to_w,
    )


def _extract_reg_block(container, lang, choices):
    """
    Extract a block from <reg type="modernized"> elements (for GLiNER on French).

    Concatenates text from all <reg> elements across all <choice> in the container.
    """
    all_text_parts = []
    all_char_to_reg = []
    global_pos = 0

    for _choice, _orig, reg in choices:
        if reg is None:
            continue
        text = reg.text or ""
        if not text.strip():
            continue
        if global_pos > 0:
            all_text_parts.append(" ")
            global_pos += 1
        start = global_pos
        all_text_parts.append(text)
        global_pos += len(text)
        all_char_to_reg.append((start, global_pos, reg))

    full_text = "".join(all_text_parts)
    if not full_text.strip():
        return None

    return NERBlock(
        lang=lang,
        text=full_text,
        source="reg",
        container=container,
        char_to_reg=all_char_to_reg,
    )


def _extract_raw_block(container, lang):
    """
    Extract a raw text block (no <choice> structure).

    Used for non-enriched/non-modernized containers.
    """
    text = etree.tostring(container, method="text", encoding="unicode") or ""
    text = text.strip()
    if not text:
        return None

    return NERBlock(
        lang=lang,
        text=text,
        source="raw",
        container=container,
    )


def extract_ner_blocks(root, containers_config):
    """
    Extract all NER blocks from the TEI body.

    For French containers with <choice>:
      - One "orig" block (for CamemBERT)
      - One "reg" block (for GLiNER)
    For non-French containers with <choice>:
      - One "orig" block (for GLiNER)
    For containers without <choice>:
      - One "raw" block (for GLiNER)

    Args:
        root: TEI root lxml Element.
        containers_config: Set of container tag names to scan (e.g. {"ab", "note", "fw"}).

    Returns:
        list[NERBlock]: Extracted blocks ready for inference.
    """
    body = None
    for elem in root.iter():
        if _local(elem.tag) == "body":
            body = elem
            break

    if body is None:
        logger.warning("No <body> element found, skipping NER extraction")
        return []

    blocks = []

    for elem in body.iter():
        local = _local(elem.tag)
        if local not in containers_config:
            continue

        lang = elem.get(XML_LANG, "und")
        if lang == "und":
            continue

        choices = _find_choices(elem)
        is_fra = lang == "fra"

        if choices:
            if is_fra:
                # French: CamemBERT on <orig>, GLiNER on <reg>
                orig_block = _extract_orig_block(elem, lang, choices)
                if orig_block:
                    blocks.append(orig_block)
                reg_block = _extract_reg_block(elem, lang, choices)
                if reg_block:
                    blocks.append(reg_block)
            else:
                # Non-French tokenized: GLiNER on <orig> text
                orig_block = _extract_orig_block(elem, lang, choices)
                if orig_block:
                    blocks.append(orig_block)
        else:
            # No enrichment/modernization: raw text
            raw_block = _extract_raw_block(elem, lang)
            if raw_block:
                blocks.append(raw_block)

    logger.info(
        "NER: extracted %d blocks (%d orig, %d reg, %d raw)",
        len(blocks),
        sum(1 for b in blocks if b.source == "orig"),
        sum(1 for b in blocks if b.source == "reg"),
        sum(1 for b in blocks if b.source == "raw"),
    )
    return blocks


# =============================================================================
# NER INFERENCE
# =============================================================================


def _run_camembert(blocks, model, label_map, threshold, batch_size=32):
    """Run CamemBERT NER on a list of blocks using batch inference."""
    results = [[] for _ in blocks]
    texts = [b.text for b in blocks]

    for batch_start in range(0, len(texts), batch_size):
        batch_texts = texts[batch_start : batch_start + batch_size]
        try:
            batch_preds = model(batch_texts)
        except Exception as e:
            logger.warning("CamemBERT batch failed at offset %d: %s", batch_start, e)
            continue

        for j, preds in enumerate(batch_preds):
            idx = batch_start + j
            block = blocks[idx]
            spans = []
            for pred in preds:
                label = pred.get("entity_group", pred.get("entity", ""))
                score = pred.get("score", 0.0)
                if score < threshold:
                    continue
                entity_type = label_map.get(label)
                if entity_type is None:
                    continue
                spans.append(
                    NERSpan(
                        start_char=pred["start"],
                        end_char=pred["end"],
                        text=block.text[pred["start"] : pred["end"]],
                        label=label,
                        entity_type=entity_type,
                        confidence=score,
                        model="camembert",
                    )
                )
            results[idx] = spans

    return results


def _chunk_text(text, max_words, overlap_words):
    """
    Yield (chunk_text, char_offset) for a long text using a sliding word window.

    GLiNER truncates inputs longer than ~384 subword tokens. We slide a window
    of `max_words` words across the text with `overlap_words` overlap so an
    entity straddling a chunk boundary is still captured in the next window.
    Offsets are kept so predicted spans can be remapped to the original text.
    """
    word_positions = [(m.start(), m.end()) for m in re.finditer(r"\S+", text)]
    if len(word_positions) <= max_words:
        yield text, 0
        return

    step = max(1, max_words - overlap_words)
    n = len(word_positions)
    i = 0
    while i < n:
        end_idx = min(i + max_words, n)
        start_char = word_positions[i][0]
        end_char = word_positions[end_idx - 1][1]
        yield text[start_char:end_char], start_char
        if end_idx >= n:
            break
        i += step


def _run_gliner(
    blocks,
    model,
    labels,
    label_map,
    threshold,
    batch_size=16,
    max_words=280,
    overlap_words=30,
):
    """Run GLiNER NER on a list of blocks using batch inference.

    Long blocks are sliced into overlapping word windows to stay under
    GLiNER's 384-token limit; predicted spans are remapped to block offsets
    and duplicates from overlap regions are merged.
    """
    results = [[] for _ in blocks]

    chunk_block_idx = []
    chunk_offsets = []
    chunk_texts = []
    for bi, block in enumerate(blocks):
        for chunk_text, offset in _chunk_text(block.text, max_words, overlap_words):
            chunk_block_idx.append(bi)
            chunk_offsets.append(offset)
            chunk_texts.append(chunk_text)

    cuda_available = _HAS_TORCH and torch.cuda.is_available()
    empty_cache_every = 50  # batches

    for batch_start in range(0, len(chunk_texts), batch_size):
        batch_texts = chunk_texts[batch_start : batch_start + batch_size]
        try:
            batch_preds = model.inference(
                batch_texts, labels, threshold=threshold
            )
        except Exception as e:
            logger.warning("GLiNER batch failed at offset %d: %s", batch_start, e)
            if cuda_available:
                # Drop any cached blocks from the failed allocation so the
                # next batch starts from a clean allocator state.
                torch.cuda.empty_cache()
            continue

        batch_idx = batch_start // batch_size
        if cuda_available and batch_idx > 0 and batch_idx % empty_cache_every == 0:
            torch.cuda.empty_cache()

        for j, preds in enumerate(batch_preds):
            global_idx = batch_start + j
            bi = chunk_block_idx[global_idx]
            offset = chunk_offsets[global_idx]
            block = blocks[bi]
            for pred in preds:
                label = pred.get("label", "")
                entity_type = label_map.get(label)
                if entity_type is None:
                    continue
                start = pred["start"] + offset
                end = pred["end"] + offset
                results[bi].append(
                    NERSpan(
                        start_char=start,
                        end_char=end,
                        text=block.text[start:end],
                        label=label,
                        entity_type=entity_type,
                        confidence=pred.get("score", 0.0),
                        model="gliner",
                    )
                )

    for bi, spans in enumerate(results):
        if not spans:
            continue
        deduped = {}
        for s in spans:
            key = (s.start_char, s.end_char, s.entity_type)
            existing = deduped.get(key)
            if existing is None or s.confidence > existing.confidence:
                deduped[key] = s
        results[bi] = sorted(deduped.values(), key=lambda s: s.start_char)

    return results


def detect_entities(blocks, models, entity_types_config, models_config, threshold, root=None):
    """
    Run NER inference on extracted blocks.

    Dispatches blocks to the appropriate model(s) based on language and source.

    Args:
        blocks: List of NERBlock from extract_ner_blocks().
        models: NERModels instance (lazy-loading).
        entity_types_config: NER_ENTITY_TYPES from config.
        models_config: NER_MODELS from config.
        threshold: Minimum confidence score.

    Returns:
        list[list[NERSpan]]: One list of spans per input block.
    """
    if not blocks:
        return []

    label_map = _build_label_map(entity_types_config)

    # Build GLiNER labels list
    gliner_labels = [
        cfg["gliner_label"]
        for cfg in entity_types_config.values()
        if cfg.get("gliner_label")
    ]

    # CamemBERT labels (for filtering — model produces its own labels)
    camembert_langs = set(models_config["camembert"].get("languages") or [])

    # Separate blocks by model target, skipping blocks too short to
    # contain a meaningful entity (saves inference time).
    MIN_BLOCK_LENGTH = 10  # characters

    camembert_blocks = []  # (index, block)
    gliner_blocks = []  # (index, block)
    skipped = 0

    for i, block in enumerate(blocks):
        if len(block.text.strip()) < MIN_BLOCK_LENGTH:
            skipped += 1
            continue
        if block.source == "orig" and block.lang in camembert_langs:
            camembert_blocks.append((i, block))
        else:
            gliner_blocks.append((i, block))

    if skipped:
        logger.info("NER: skipped %d blocks shorter than %d chars", skipped, MIN_BLOCK_LENGTH)

    # Initialize results
    all_results = [[] for _ in blocks]

    # Run CamemBERT
    if camembert_blocks:
        logger.info("Running CamemBERT on %d French blocks", len(camembert_blocks))
        cam_blocks = [b for _, b in camembert_blocks]
        cam_batch = models_config["camembert"].get("batch_size", 32)
        cam_results = _run_camembert(cam_blocks, models.camembert, label_map, threshold, cam_batch)
        for (idx, _), spans in zip(camembert_blocks, cam_results):
            all_results[idx] = spans

    # Run GLiNER
    if gliner_blocks:
        logger.info("Running GLiNER on %d blocks", len(gliner_blocks))
        gli_blocks = [b for _, b in gliner_blocks]
        gli_cfg = models_config["gliner"]
        gli_batch = gli_cfg.get("batch_size", 16)
        gli_max_words = gli_cfg.get("max_words", 280)
        gli_overlap = gli_cfg.get("overlap_words", 30)
        gli_results = _run_gliner(
            gli_blocks,
            models.gliner,
            gliner_labels,
            label_map,
            threshold,
            gli_batch,
            gli_max_words,
            gli_overlap,
        )
        for (idx, _), spans in zip(gliner_blocks, gli_results):
            all_results[idx] = spans

    total_spans = sum(len(s) for s in all_results)
    logger.info("NER: detected %d raw spans across %d blocks", total_spans, len(blocks))

    # ── Post-detection filtering ────────────────────────────────────
    title = extract_title_from_tei(root) if root is not None else None
    for i, spans in enumerate(all_results):
        if spans:
            all_results[i] = filter_spans(spans, title=title)

    filtered_total = sum(len(s) for s in all_results)
    if filtered_total < total_spans:
        logger.info("NER: %d spans after filtering (-%d)", filtered_total, total_spans - filtered_total)

    return all_results
