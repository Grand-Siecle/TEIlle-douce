# -----------------------------------------------------------
# TEIlle-douce - ALTO to TEI XML conversion pipeline
# -----------------------------------------------------------
"""
TEIlle-douce package.

Converts ALTO (Analyzed Layout and Text Object) XML files from OCR/HTR systems
into TEI (Text Encoding Initiative) XML format with SegmOnto taxonomy.

Main modules:
    - tei: Central TEI document class
    - teiheader: TEI header construction
    - sourcedoc: SourceDoc construction from ALTO
    - body: Body construction from extracted text
    - metadata: Metadata loading (CSV, IIIF)
    - utils: Shared utilities

Usage:
    from teille_douce import TEI
    from teille_douce.teiheader import build_header
    from teille_douce.sourcedoc import build_sourcedoc
    from teille_douce.body import build_body
    from teille_douce.metadata import load_metadata, IIIFMapping
"""

from .tei import TEI
from .constants import NS_ALTO, NS_TEI, NS_XML, XML_ID

__all__ = [
    "TEI",
    "NS_ALTO",
    "NS_TEI",
    "NS_XML",
    "XML_ID",
]

__version__ = "2.0.0"
