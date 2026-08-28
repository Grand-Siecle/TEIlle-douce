# -----------------------------------------------------------
# XML output utilities
# Functions for writing formatted TEI XML files
# -----------------------------------------------------------
"""
XML output module.

This module provides utilities for writing TEI XML files to disk.
"""

import logging
import os
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


def xml_id_safe(value):
    """Return ``value`` coerced to a valid XML NCName for use as ``xml:id``.

    NCNames must start with a letter or underscore — never a digit. ALTO
    pages named purely numerically (e.g. "1.xml") would otherwise yield
    invalid xml:id values like "1".
    """
    s = str(value)
    if s and s[0].isdigit():
        return f"f{s}"
    return s


def write_xml(root, output_path, pretty_print=True):
    """
    Write an XML tree to a file, atomically.

    The tree is serialized to a temporary sibling file then moved into
    place with os.replace: an interrupted run can never leave a truncated
    file at the final path — which matters doubly since --skip-existing
    treats the file's existence as "already converted".

    Args:
        root (etree.Element): XML root element to write.
        output_path (Path or str): Output file path.
        pretty_print (bool): If True, format with indentation. Default True.

    Returns:
        None
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = output_path.with_name(output_path.name + ".part")

    try:
        etree.ElementTree(root).write(
            str(tmp_path),
            encoding="utf-8",
            xml_declaration=True,
            pretty_print=pretty_print,
        )
        os.replace(tmp_path, output_path)
    except Exception as e:
        logger.error("Failed to write XML to %s: %s", output_path, e)
        tmp_path.unlink(missing_ok=True)
        raise
