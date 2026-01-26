# -----------------------------------------------------------
# XML output utilities
# Functions for writing formatted TEI XML files
# -----------------------------------------------------------
"""
XML output module.

This module provides utilities for writing TEI XML files to disk.
"""

from pathlib import Path

from lxml import etree


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

    etree.ElementTree(root).write(
        str(output_path),
        encoding="utf-8",
        xml_declaration=True,
        pretty_print=pretty_print,
    )
