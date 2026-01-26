# -----------------------------------------------------------
# Body construction module
# Builds the <body> element from sourceDoc text data
# -----------------------------------------------------------
"""
Body module for ALTO2TEI pipeline.

This module provides functions to build TEI body elements from extracted text.

Usage:
    from src.body import build_body, Text

    text = Text(root)
    build_body(root, text.data)
"""

from .builder import build_body
from .text import Text

__all__ = ["build_body", "Text"]
