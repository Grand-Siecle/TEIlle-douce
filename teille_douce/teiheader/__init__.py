# -----------------------------------------------------------
# TEI Header construction module
# Builds the <teiHeader> element with metadata and taxonomy
# -----------------------------------------------------------
"""
TEI Header module for TEIlle-douce pipeline.

This module provides functions to build TEI headers from metadata.

Usage:
    from teille_douce.teiheader import build_header

    root, zones, lines = build_header(metadata, document, root, page_count, config, versions, filepaths)
"""

from .builder import build_header, declare_pos_tagsets, update_extent

__all__ = ["build_header", "declare_pos_tagsets", "update_extent"]
