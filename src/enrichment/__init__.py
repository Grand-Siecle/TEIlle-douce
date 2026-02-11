# -----------------------------------------------------------
# Linguistic enrichment module for ALTO2TEI pipeline.
# Transforms body containers into tokenized TEI with NLP annotations.
# -----------------------------------------------------------
"""
Enrichment module.

Provides linguistic annotation (tokenization, POS tagging, lemmatization)
for TEI body containers via PyHellen NLP API.

Usage:
    from src.enrichment import enrich_body
    enrich_body(root)
"""

from .pipeline import enrich_body

__all__ = ["enrich_body"]
