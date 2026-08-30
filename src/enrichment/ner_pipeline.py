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

from config import (
    NER_CERT_THRESHOLDS,
    NER_CONFIDENCE_THRESHOLD,
    NER_CONTAINERS,
    NER_ENTITY_TYPES,
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


def run_ner(root, person_db, document_name, models=None):
    """
    Run phases 7-9 of the NER pipeline on one document.

    Args:
        root: TEI root element (already enriched and modernized).
        person_db: PersonDatabase for local linking, or None.
        document_name: Document name — entity CSVs are written to
            ``NER_OUTPUT_DIR/<document_name>/`` (audit 2.4).
        models: Optional preloaded NERModels; defaults to the run-level
            cache.

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

    # Phase 7: extract blocks + inference
    blocks = extract_ner_blocks(root, NER_CONTAINERS)
    spans = detect_entities(
        blocks, models, NER_ENTITY_TYPES, NER_MODELS,
        NER_CONFIDENCE_THRESHOLD, root=root,
    )

    # Phase 8: align + merge + inject
    aligned = align_and_inject(
        blocks, spans, NER_ENTITY_TYPES, NER_CERT_THRESHOLDS
    )

    # Phase 9: resolve + CSV + header + @ref
    return resolve_entities(
        root, aligned, NER_ENTITY_TYPES, person_db, NER_OUTPUT_DIR, document_name
    )


def summarize(resolved):
    """
    One-line summary of a document's entities, or None when there are none.

    Example: "12 entities (7 person, 3 place, 2 artwork), 48 mentions".
    """
    if not resolved:
        return None

    by_type = {}
    for ent in resolved:
        by_type[ent.entity_type] = by_type.get(ent.entity_type, 0) + 1
    total_mentions = sum(len(e.mentions) for e in resolved)
    type_str = ", ".join(
        f"{count} {etype}"
        for etype, count in sorted(by_type.items(), key=lambda kv: -kv[1])
    )
    return f"{len(resolved)} entities ({type_str}), {total_mentions} mentions"
