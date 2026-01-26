# -----------------------------------------------------------
# SourceDoc construction module
# Builds the <sourceDoc> element from ALTO XML files
# -----------------------------------------------------------
"""
SourceDoc module for ALTO2TEI pipeline.

This module provides functions to build TEI sourceDoc elements from ALTO files.
Uses parallel processing for performance on large documents.

Usage:
    from src.sourcedoc import build_sourcedoc

    build_sourcedoc(doc_name, root, filepaths, tags, zones, lines, config, progress, task, iiif_mapping)
"""

from .builder import build_sourcedoc, extract_labels

__all__ = ["build_sourcedoc", "extract_labels"]
