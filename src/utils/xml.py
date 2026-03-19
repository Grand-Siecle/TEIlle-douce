# -----------------------------------------------------------
# XML output utilities
# Functions for writing formatted TEI XML files
# -----------------------------------------------------------
"""
XML output module.

This module provides utilities for writing TEI XML files to disk.
"""

import logging
from pathlib import Path

from lxml import etree

logger = logging.getLogger(__name__)


def local_tag(tag):
    """Strip namespace from an lxml tag name.

    Example: "{http://www.tei-c.org/ns/1.0}body" → "body"
    """
    if isinstance(tag, str) and "}" in tag:
        return tag.split("}", 1)[1]
    return tag


def write_xml(root, output_path, pretty_print=True):
    """
    Write an XML tree to a file.

    Args:
        root (etree.Element): XML root element to write.
        output_path (Path or str): Output file path.
        pretty_print (bool): If True, format with indentation. Default True.

    Returns:
        None
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    try:
        etree.ElementTree(root).write(
            str(output_path),
            encoding="utf-8",
            xml_declaration=True,
            pretty_print=pretty_print,
        )
    except Exception as e:
        logger.error("Failed to write XML to %s: %s", output_path, e)
        raise
