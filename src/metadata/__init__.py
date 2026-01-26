# -----------------------------------------------------------
# Metadata handling module
# Loads and processes document metadata from various sources
# -----------------------------------------------------------
"""
Metadata module for ALTO2TEI pipeline.

This module provides classes and functions for loading document metadata
from CSV files and handling IIIF URL mappings.

Usage:
    from src.metadata import load_metadata, IIIFMapping

    # Load metadata from CSV
    df, row = load_metadata(csv_path, doc_name)

    # Use IIIF mapping
    mapping = IIIFMapping()
    mapping.load_from_csv(csv_path)
    url = mapping.get_url(filename)
"""

from .csv import load_metadata, find_metadata_row, build_metadata_dict, override_teiheader_from_csv
from .iiif import IIIFMapping

__all__ = ["load_metadata", "find_metadata_row", "build_metadata_dict", "override_teiheader_from_csv", "IIIFMapping"]
