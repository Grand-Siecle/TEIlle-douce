from lxml import etree
from src.teiheader_metadata.clean_data import Metadata
from src.teiheader_build import teiheader
from src.sourcedoc_build import sourcedoc
from src.text_data import Text
from src.body_build import body

class TEI:
    metadata = {"sru":None, "iiif":None}
    tags = {}
    root = None
    segmonto_zones = None
    segmonto_lines = None
    def __init__(self, document, filepaths):
        self.d = document  # (str) this document's name / name of directory contiaining the ALTO files
        self.fp = filepaths  # (list) paths of ALTO files
        self.metadata  # (dict) dict with two keys ("iiif", "sru"), each of which is equal to its own dictionary of metadata
        self.tags  # (dict) a label-ref pair for each tag used in this document's ALTO files
        self.root  # (etree_Element) root for this document's XML-TEI tree
        self.segmonto_zones
        self.segmonto_lines

    def build_tree(self):
        """Parse and map data from ALTO files to an XML-TEI tree."""

        # 👇 Inclure segmonto dès le début
        tei_root_att = {
            "xmlns": "http://www.tei-c.org/ns/1.0",
            "{http://www.w3.org/XML/1998/namespace}id": f"ark_12148_{self.d}"
        }

        nsmap = {
            None: "http://www.tei-c.org/ns/1.0",  # Namespace par défaut
            "segmonto": "https://segmonto.github.io/ontology#"
        }

        self.root = etree.Element("TEI", tei_root_att, nsmap=nsmap)

    def build_header(self, config, version):
        """
        Construit le teiHeader.
        Si self.metadata a été déjà fournie (injection manuelle depuis le CSV),
        on la réutilise telle quelle au lieu d'appeler SRU/IIIF.
        """
        # Si les métadonnées ont été injectées manuellement, on les garde
        if hasattr(self, "metadata") and self.metadata:
            meta = self.metadata
        else:
            # sinon on crée une structure vide (mode offline par défaut)
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

        # On sauvegarde ce dictionnaire pour le reste de la construction
        self.metadata = meta

        # Appel normal à la fonction de construction du header
        from src.teiheader_build import teiheader
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
        sourcedoc(
            self.d,
            self.root,
            self.fp,
            self.tags,
            self.segmonto_zones,
            self.segmonto_lines,
            config["iiifURI"],
            progress=progress,
            parent_task_pages=parent_task_pages
        )

    def build_body(self):
        text = Text(self.root)
        body(self.root, text.data)
