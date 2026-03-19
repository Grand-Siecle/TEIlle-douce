# -----------------------------------------------------------
# Shared utilities module
# File handling and XML output utilities
# -----------------------------------------------------------
"""
Utilities module for ALTO2TEI pipeline.

This module provides shared utilities for file handling and XML output.

Usage:
    from src.utils import Files, write_xml

    # Order ALTO files by page number
    ordered = Files(doc_name, file_list).order_files()

    # Write XML to file
    write_xml(root, output_path)
"""

from .files import Files
from .xml import write_xml, local_tag

__all__ = ["Files", "write_xml", "local_tag"]
