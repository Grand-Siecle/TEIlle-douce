# -----------------------------------------------------------
# NER orchestration (phases 7-9).
# -----------------------------------------------------------
"""
Named-entity pipeline facade.

Chains the three NER phases on one document:
7. extract blocks + model inference   (ner_detect)
8. align, merge across models, inject (ner_align)
9. resolve, write CSVs, header, @ref  (ner_resolve)

This orchestration used to live inline in ``main.py`` (audit 4.3),
unlike the enrichment and modernization phases which go through the
``TEI`` facade. Heavy imports (torch, transformers, gliner) stay inside
``run_ner`` so a run with NER disabled never pays for them.
"""

import logging
from collections import Counter

from .entity_schema import NER_ENTITY_TYPES
from config import (
    NER_CERT_THRESHOLDS,
    NER_CONFIDENCE_THRESHOLD,
    NER_CONTAINERS,
    NER_MODELS,
    NER_OUTPUT_DIR,
)

logger = logging.getLogger(__name__)

# Models are expensive to load and identical for every document of a run.
_models = None


def get_models():
    """Load the NER models once per run (shared across documents)."""
    global _models
    if _models is None:
        from .ner_models import NERModels

        _models = NERModels(NER_MODELS)
    return _models


def reset_models():
    """Drop the cached models (tests, or to reclaim GPU memory)."""
    global _models
    _models = None


def run_ner(root, person_db, document_name, models=None,
            entity_types=None, ner_models=None, containers=None,
            confidence_threshold=None, cert_thresholds=None, output_dir=None):
    """
    Run phases 7-9 of the NER pipeline on one document.

    Args:
        root: TEI root element (already enriched and modernized).
        person_db: PersonDatabase for local linking, or None.
        document_name: Document name — entity CSVs are written to
            ``NER_OUTPUT_DIR/<document_name>/`` (audit 2.4).
        models: Optional preloaded NERModels; defaults to the run-level
            cache.
        entity_types, ner_models, containers, confidence_threshold,
        cert_thresholds, output_dir: Optional overrides of the matching
            config values — the module reads config only for its
            defaults, so a caller (or a test) can steer the phases
            without patching config globals.

    Returns:
        list[ResolvedEntity]: the document's resolved entities.

    Raises:
        ImportError: NER dependencies not installed (requirements-ner).
        Exception: any failure of the underlying phases — the caller
            decides whether one document's NER failure is fatal.
    """
    from .ner_detect import extract_ner_blocks, detect_entities
    from .ner_align import align_and_inject
    from .ner_resolve import resolve_entities

    if models is None:
        models = get_models()
    entity_types = NER_ENTITY_TYPES if entity_types is None else entity_types
    ner_models = NER_MODELS if ner_models is None else ner_models
    containers = NER_CONTAINERS if containers is None else containers
    if confidence_threshold is None:
        confidence_threshold = NER_CONFIDENCE_THRESHOLD
    cert_thresholds = NER_CERT_THRESHOLDS if cert_thresholds is None else cert_thresholds
    output_dir = NER_OUTPUT_DIR if output_dir is None else output_dir

    # Phase 7: extract blocks + inference
    blocks = extract_ner_blocks(root, containers)
    spans = detect_entities(
        blocks, models, entity_types, ner_models,
        confidence_threshold, root=root,
    )

    # Phase 8: align + merge + inject
    aligned = align_and_inject(blocks, spans, entity_types, cert_thresholds)

    # Phase 9: resolve + CSV + header + @ref
    return resolve_entities(
        root, aligned, entity_types, person_db, output_dir, document_name,
        cert_thresholds=cert_thresholds,
    )


def summarize(resolved):
    """
    One-line summary of a document's entities, or None when there are none.

    Example: "12 entities (7 person, 3 place, 2 artwork), 48 mentions".
    """
    if not resolved:
        return None

    by_type = Counter(ent.entity_type for ent in resolved)
    total_mentions = sum(len(e.mentions) for e in resolved)
    type_str = ", ".join(f"{count} {etype}" for etype, count in by_type.most_common())
    return f"{len(resolved)} entities ({type_str}), {total_mentions} mentions"
