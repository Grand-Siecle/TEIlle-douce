from lxml import etree
from pathlib import Path
from src.teiheader_build import teiheader
from src.sourcedoc_build import sourcedoc
from src.text_data import Text
from src.body_build import body
from src.teiheader_metadata.iiif_data import IIIFMapping


class TEI:
    metadata = {"sru": None, "iiif": None}
    tags = {}
    root = None
    segmonto_zones = None
    segmonto_lines = None

    def __init__(self, document, filepaths, doc_dir=None):
        """
        Args:
            document: nom du document
            filepaths: liste des fichiers ALTO
            doc_dir: dossier contenant les fichiers (pour auto-détection du mapping IIIF)
        """
        self.d = document
        self.fp = filepaths
        self.doc_dir = doc_dir
        self.metadata = {"sru": None, "iiif": None}
        self.tags = {}
        self.root = None
        self.segmonto_zones = None
        self.segmonto_lines = None
        self._iiif_mapping = None

    def build_tree(self):
        """Parse and map data from ALTO files to an XML-TEI tree."""
        xml_id_att = {
            "{http://www.w3.org/XML/1998/namespace}id": f"ark_12148_{self.d}"
        }
        nsmap = {
            None: "http://www.tei-c.org/ns/1.0"
        }
        self.root = etree.Element("TEI", xml_id_att, nsmap=nsmap)

    @property
    def iiif_mapping(self):
        """Charge automatiquement le mapping IIIF"""
        if self._iiif_mapping is None and self.doc_dir:

            mapping_csv = IIIFMapping.detect_csv(Path(self.doc_dir), self.fp)
            if mapping_csv:
                self._iiif_mapping = IIIFMapping()
                self._iiif_mapping.load_from_csv(mapping_csv)

        return self._iiif_mapping

    def build_header(self, config, version):
        """Construit le teiHeader."""
        if hasattr(self, "metadata") and self.metadata and self.metadata.get("sru"):
            meta = self.metadata
        else:
            meta = {
                "sru": {
                    "found": False,
                    "ark": None,
                    "title": None,
                    "date": None,
                    "publisher": None,
                    "place": None,
                    "language": None,
                },
                "iiif": {
                    "Creator": None,
                    "Title": None,
                    "Date": None,
                    "Publisher": None,
                    "Place": None,
                    "Licence": None,
                    "Extent": None,
                    "Dimensions": None,
                    "manifest": None,
                    "thumbnail": None,
                },
            }

        self.metadata = meta
        self.root, self.segmonto_zones, self.segmonto_lines = teiheader(
            self.metadata,
            self.d,
            self.root,
            len(self.fp),
            config,
            version,
            self.fp,
            self.segmonto_zones,
            self.segmonto_lines,
        )

    def build_sourcedoc(self, config, progress=None, parent_task_pages=None):
        """Construit le <sourceDoc> avec les données ALTO."""
        sourcedoc(
            self.d,
            self.root,
            self.fp,
            self.tags,
            self.segmonto_zones,
            self.segmonto_lines,
            config["iiifURI"],
            progress=progress,
            parent_task_pages=parent_task_pages,
            iiif_mapping=self.iiif_mapping
        )

    def build_body(self):
        """Construit le <body> du TEI."""
        text = Text(self.root)
        body(self.root, text.data)