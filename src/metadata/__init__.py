# -----------------------------------------------------------
# Metadata handling module
# Loads and processes document metadata from various sources
# -----------------------------------------------------------
"""
Metadata module for TEIlle-douce pipeline.

This module provides classes and functions for loading document metadata
from CSV files, handling IIIF URL mappings, and person metadata.

Usage:
    from src.metadata import load_metadata, IIIFMapping, PersonDatabase

    # Load metadata from CSV
    df, row = load_metadata(csv_path, doc_name)

    # Use IIIF mapping
    mapping = IIIFMapping()
    mapping.load_from_csv(csv_path)
    url = mapping.get_url(filename)

    # Use person database
    person_db = PersonDatabase(person_csv_path)
    author_data = person_db.enrich_author_data("PERS0001")
"""

from .csv_book import load_metadata, find_metadata_row, build_metadata_dict, override_teiheader_from_csv, select_manifest
from .csv_person import PersonDatabase, load_person_database, get_person_database
from .iiif import IIIFMapping

__all__ = [
    "load_metadata",
    "find_metadata_row",
    "build_metadata_dict",
    "override_teiheader_from_csv",
    "select_manifest",
    "IIIFMapping",
    "PersonDatabase",
    "load_person_database",
    "get_person_database",
]
