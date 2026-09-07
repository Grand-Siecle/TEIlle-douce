# -----------------------------------------------------------
# IIIF URL mapping module
# Manages mappings between ALTO files and IIIF image URLs
# -----------------------------------------------------------
"""
IIIF mapping module.

This module provides the IIIFMapping class for managing mappings between
ALTO filenames and IIIF image URLs loaded from CSV files.
"""

import logging
from pathlib import Path
from typing import Dict, Optional

import pandas as pd

from teille_douce.config import (
    IIIF_CSV_PATTERNS,
    IIIF_CSV_MAX_SIZE,
    IIIF_CSV_SAMPLE_ROWS,
    IIIF_CSV_MIN_MATCH_RATE,
)

logger = logging.getLogger(__name__)


class IIIFMapping:
    """
    Manages mappings between ALTO files and IIIF image URLs.

    Loads mappings from CSV files and provides URL lookup by filename.

    Expected CSV format (no header):
        Column 0: IIIF URL
        Column 1: Source identifier
        Column 2: ALTO filename

    Attributes:
        mapping (dict): Dictionary mapping filenames to IIIF URLs.
    """

    def __init__(self):
        """Initialize an empty mapping."""
        self.mapping: Dict[str, str] = {}

    def load_from_csv(self, csv_path: Path) -> bool:
        """
        Load a mapping from a CSV file.

        Args:
            csv_path (Path): Path to the mapping CSV file.

        Returns:
            bool: True if loading succeeded, False otherwise.
        """
        if not csv_path.exists():
            logger.warning("CSV not found: %s", csv_path)
            return False

        try:
            # dtype=str: an all-digit ALTO-name column ("0001") would be
            # inferred as int64 and str(row[2]) would yield "1", missing
            # every real file stem (audit 5.3, same class).
            df = pd.read_csv(csv_path, header=None, dtype=str, keep_default_na=False)

            if df.shape[1] < 3:
                logger.warning("Invalid CSV (< 3 columns): %s", csv_path.name)
                return False

            count = 0
            for _, row in df.iterrows():
                url = str(row[0]).strip()
                alto_name = str(row[2]).strip()

                if not url or url == "nan":
                    continue

                # Store with and without .xml extension
                base_name = alto_name.replace(".xml", "")
                self.mapping[base_name] = url
                self.mapping[f"{base_name}.xml"] = url
                count += 1

            return True

        except Exception as e:
            logger.error("Reading CSV %s: %s", csv_path.name, e)
            return False

    def get_url(self, filename: str) -> Optional[str]:
        """
        Get the IIIF URL for a filename or xml:id.

        Args:
            filename (str): ALTO filename or xml:id (e.g., "f0-plat-sup", "f1").

        Returns:
            str or None: IIIF URL or None if not found.
        """
        # Try exact match first
        url = self.mapping.get(filename)
        if url:
            return url

        # Try without .xml extension
        base_name = filename.replace(".xml", "")
        return self.mapping.get(base_name)

    def has_mapping(self) -> bool:
        """
        Check if a mapping has been loaded.

        Returns:
            bool: True if mapping contains entries.
        """
        return len(self.mapping) > 0

    def count(self) -> int:
        """
        Get the number of mapped files.

        Returns:
            int: Number of unique files mapped (divided by 2 since we store with/without .xml).
        """
        return len(self.mapping) // 2

    @staticmethod
    def detect_csv(doc_dir: Path, alto_files: list) -> Optional[Path]:
        """
        Auto-detect a mapping CSV in a document directory.

        Searches for CSV files with names containing 'iiif', 'mapping', or 'manifest',
        and validates them by checking if the third column contains ALTO filenames.

        Args:
            doc_dir (Path): Directory containing the document files.
            alto_files (list): List of ALTO file paths for validation.

        Returns:
            Path or None: Path to the detected CSV or None if not found.
        """
        candidates = []
        for pattern in IIIF_CSV_PATTERNS:
            candidates.extend(doc_dir.glob(pattern))

        if not candidates:
            return None

        # Get ALTO filenames for validation
        alto_names = {p.name for p in alto_files}
        alto_names.update(p.stem for p in alto_files)

        # Sort by relevance (prefer 'iiif' in name)
        candidates.sort(key=lambda p: (0 if "iiif" in p.name.lower() else 1, p.name.lower()))

        # Validate candidates
        for csv_path in candidates:
            # A regular file, first of all. A FIFO named like a mapping
            # reports zero bytes, so it passed the size cap below and
            # `read_csv` then waited for a writer that never came: the
            # whole command hung with nothing on screen, `run` and
            # `check` alike. A directory or a broken symlink raised
            # instead. `is_file` follows the symlink and answers no to
            # all three.
            if not csv_path.is_file():
                continue
            # Skip very large files
            try:
                too_big = csv_path.stat().st_size > IIIF_CSV_MAX_SIZE
            except OSError:
                continue
            if too_big:
                continue

            try:
                df = pd.read_csv(
                    csv_path, header=None, nrows=IIIF_CSV_SAMPLE_ROWS,
                    dtype=str, keep_default_na=False,
                )

                if df.shape[1] < 3 or df.empty:
                    continue

                # Check if column 2 contains ALTO filenames
                col2_values = df.iloc[:, 2].astype(str).str.strip()
                matches = sum(
                    1
                    for val in col2_values
                    if val in alto_names or val.replace(".xml", "") in alto_names
                )

                # Require minimum match rate
                if matches > 0 and matches / len(col2_values) > IIIF_CSV_MIN_MATCH_RATE:
                    return csv_path

            except Exception:
                continue

        return None
