# -----------------------------------------------------------
# Linguistic enrichment module for ALTO2TEI pipeline.
# Transforms body containers into tokenized TEI with NLP annotations.
# -----------------------------------------------------------
"""
Enrichment module.

Provides linguistic annotation (tokenization, POS tagging, lemmatization)
for TEI body containers via PyHellen NLP API.

Named-entity recognition (phases 7-9) lives behind the ner_pipeline
facade; its heavy dependencies are imported only when it runs.

Usage:
    from src.enrichment import enrich_body, run_ner
    enrich_body(root)
    run_ner(root, person_db, document_name)
"""

from .pipeline import enrich_body
from .ner_pipeline import run_ner, summarize

__all__ = ["enrich_body", "run_ner", "summarize"]
