# -----------------------------------------------------------
# Body construction module
# Builds the <body> element from sourceDoc text data
# -----------------------------------------------------------
"""
Body module for TEIlle-douce pipeline.

This module provides functions to build TEI body elements from extracted text.

Usage:
    from teille_douce.body import build_body, Text

    text = Text(root)
    build_body(root, text.data)
"""

from .builder import (build_body, apply_modernization,
                      apply_modernization_enriched, count_containers)
from .note_links import link_notes_to_lines
from .text import Text

__all__ = [
    "build_body",
    "apply_modernization",
    "apply_modernization_enriched",
    "count_containers",
    "link_notes_to_lines",
    "Text",
]
