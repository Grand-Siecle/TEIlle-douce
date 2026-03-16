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

logger = logging.getLogger(__name__)


# Named tuple for file data
File = namedtuple("File", ["num", "filepath"])


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
                    others.append(File(999999, filepath))
            else:
                others.append(File(999999, filepath))
                logger.warning("File ignored (no page number detected): %s", filepath.name)

        if not numbered and not others:
            raise ValueError(f"No valid ALTO files found for {self.doc}")

        return sorted(numbered + others, key=lambda x: x.num)
