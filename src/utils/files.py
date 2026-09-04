# -----------------------------------------------------------
# Code by: Kelly Christensen
# Organizes and orders file paths in a document directory.
# -----------------------------------------------------------
"""
File ordering module.

This module provides the Files class for ordering ALTO files by page number.
"""

import logging
import re
from collections import namedtuple

from .xml import xml_id_safe

logger = logging.getLogger(__name__)


# Named tuple for file data
File = namedtuple("File", ["num", "filepath"])

# Sort key given to a file whose name holds no digit: it is ordered last.
# Named because it is not a page number and must never be reported as one --
# every such file receives this same value, so they all collide by
# construction.
NO_PAGE_NUMBER = 999999


class Files:
    """
    Orders ALTO files by page number extracted from filenames.

    Recognizes various filename patterns:
    - f12.xml, f12-np.xml
    - page_001.xml
    - 001.xml

    Files without detectable page numbers are placed at the end.

    Attributes:
        doc (str): Document name.
        fl (list): List of file paths.
    """

    def __init__(self, document_name, file_list):
        """
        Initialize the Files orderer.

        Args:
            document_name (str): Name of the document.
            file_list (list): List of Path objects for ALTO files.
        """
        self.doc = document_name
        self.fl = file_list

    def order_files(self):
        """
        Sort ALTO files by page number.

        Extracts numeric values from filenames and sorts by that number.
        Files without numbers are placed at the end with a high number.

        Returns:
            list: List of File namedtuples sorted by page number.

        Raises:
            ValueError: If no valid ALTO files are found.
        """
        numbered = []
        others = []

        for filepath in self.fl:
            # Extract first number from filename
            match = re.search(r"(\d+)", filepath.stem)
            if match:
                try:
                    num = int(match.group(1))
                    numbered.append(File(num, filepath))
                except ValueError:
                    others.append(File(NO_PAGE_NUMBER, filepath))
            else:
                others.append(File(NO_PAGE_NUMBER, filepath))
                logger.warning("File ignored (no page number detected): %s", filepath.name)

        if not numbered and not others:
            raise ValueError(f"No valid ALTO files found for {self.doc}")

        return sorted(numbered + others, key=lambda x: x.num)


def parse_document_id(document_name):
    """
    Parse a document folder name into (internal_id, volume).

    Handles both attached-letter volumes ("LIV0002a_reconciled" -> ("LIV0002", "a"))
    and underscore markers ("LIV0031_t2_reconciled" -> ("LIV0031", "t2"),
    "LIV0326_v2_altos" -> ("LIV0326", "v2"), "ABC999_b_x" -> ("ABC999", "b")).
    """
    match = re.match(r"([A-Za-z]+\d+)([a-z])?(?:_([vVtTbB]\d*))?", document_name)
    if not match:
        return document_name, None
    return match.group(1), match.group(2) or match.group(3)


def canonical_document_id(document_name):
    """
    Canonical short id for xml:id use: internal id + volume marker.

    "LIV0008_reconciled" -> "LIV0008"; "LIV0002a_reconciled" -> "LIV0002a";
    "LIV0031_t2_reconciled" -> "LIV0031_t2".
    """
    internal_id, volume = parse_document_id(document_name)
    if not volume:
        return xml_id_safe(internal_id)
    sep = "" if len(volume) == 1 and volume.isalpha() else "_"
    return xml_id_safe(f"{internal_id}{sep}{volume}")
