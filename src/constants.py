# -----------------------------------------------------------
# Shared constants for ALTO2TEI pipeline
# XML namespaces and internal constants used across modules
# -----------------------------------------------------------

import re
import uuid

from lxml import etree

# =============================================================================
# IDENTIFIERS
# =============================================================================

# Namespace for the pipeline's deterministic uuid5 identifiers (audit 2.8):
# the same ALTO input must yield byte-identical TEI ids on every run, so
# outputs can be diffed and the external entity-reconciliation step can
# rely on stable ids.
UUID_NAMESPACE = uuid.uuid5(uuid.NAMESPACE_DNS, "alto2tei")

# =============================================================================
# XML NAMESPACES
# =============================================================================

# ALTO v4 namespace (input format)
NS_ALTO_URI = "http://www.loc.gov/standards/alto/ns-v4#"
NS_ALTO = {"a": NS_ALTO_URI}

# TEI namespace (output format)
NS_TEI = "http://www.tei-c.org/ns/1.0"

# XML namespace (for xml:id attributes)
NS_XML = "http://www.w3.org/XML/1998/namespace"

# XML namespace attribute keys
XML_ID = f"{{{NS_XML}}}id"
XML_LANG = f"{{{NS_XML}}}lang"

def tag_like(reference, tag):
    """
    Element name for *tag* following *reference*'s namespace convention.

    The pipeline builds its tree with BARE tags (the root's nsmap puts
    everything in the TEI namespace at serialization), but a TEI file
    read back from disk is namespaced. Code that injects elements into
    a tree it did not build must follow whatever convention that tree
    uses, or the two coexist and lookups silently miss half the
    elements (audit 4.2).
    """
    if reference is not None:
        ns = etree.QName(reference).namespace if isinstance(reference.tag, str) else None
        if ns:
            return f"{{{ns}}}{tag}"
    return tag


# SegmOnto tag syntax: "MainZone:column#1" -> ("MainZone", "column", "1").
# Single definition: the header taxonomy and the sourcedoc attributes
# must read the same labels the same way (audit 4.9).
SEGMONTO_TAG_RE = re.compile(r"(\w+):?(\w+)?#?(\d?)?")

# =============================================================================
# SEGMONTO TAXONOMY
# =============================================================================

# =============================================================================
# SEGMONTO TAXONOMY CONFIGURATION
# =============================================================================

# Referentiel des mots-cles du header. Les sujets viennent des colonnes
# Sujet et Matiere du CSV, elles-memes reprises des notices du catalogue :
# sans @scheme pointant vers cette declaration, <keywords> n'indique pas
# d'ou vient son vocabulaire, et rien ne distingue un descripteur
# controle d'un mot-cle libre (audit 1.15).
KEYWORDS_TAXONOMY = {
    "id": "catalogue-subjects",
    "label": "Sujets et matières des notices de catalogue "
             "(colonnes Sujet et Matiere du CSV local)",
}


# Jeu d'etiquettes morphosyntaxiques de chaque modele. Les <w> portent
# @pos="NOMcom" et @msd="NOMB.=s|GENRE=m" sans dire de quel referentiel
# ces valeurs viennent : un lecteur qui ne connait pas CATTEX ne peut ni
# les interpreter ni les convertir (audit 1.11). Declare une fois dans
# l'encodingDesc, pointe par @ana sur chaque <w> annote.
POS_TAGSETS = {
    "freem": {
        "id": "cattex-freem",
        "label": "CATTEX (FreEM) — francais pre-classique et classique",
        "url": "https://freem-corpora.github.io/",
    },
    "lasla": {
        "id": "lasla",
        "label": "LASLA — latin",
        "url": "https://www.lasla.uliege.be/",
    },
    "grc": {
        "id": "perseus-grc",
        "label": "Perseus / Ancient Greek Dependency Treebank — grec ancien",
        "url": "https://perseusdl.github.io/treebank_data/",
    },
}


# SegmOnto zone types and their documentation URLs
SEGMONTO_ZONES = {
    "CustomZone": "https://segmonto.github.io/gd/gdZ/CustomZone/",
    "DamageZone": "https://segmonto.github.io/gd/gdZ/DamageZone",
    "DecorationZone": "https://segmonto.github.io/gd/gdZ/DecorationZone",
    "DigitizationArtefactzone": "https://segmonto.github.io/gd/gdZ/DigitizationArtefactzone",
    "DropCapitalZone": "https://segmonto.github.io/gd/gdZ/DropCapitalZone",
    "GraphicZone": "https://segmonto.github.io/gd/gdZ/GraphicZone",
    "MainZone": "https://segmonto.github.io/gd/gdZ/MainZone",
    "MarginTextZone": "https://segmonto.github.io/gd/gdZ/MarginTextZone",
    "MusicZone": "https://segmonto.github.io/gd/gdZ/MusicZone",
    "NumberingZone": "https://segmonto.github.io/gd/gdZ/NumberingZone",
    "QuireMarksZone": "https://segmonto.github.io/gd/gdZ/QuireMarksZone",
    "RunningTitleZone": "https://segmonto.github.io/gd/gdZ/RunningTitleZone",
    "SealZone": "https://segmonto.github.io/gd/gdZ/SealZone",
    "StampZone": "https://segmonto.github.io/gd/gdZ/StampZone",
    "TableZone": "https://segmonto.github.io/gd/gdZ/TableZone",
    "TitlePageZone": "https://segmonto.github.io/gd/gdZ/TitlePageZone",
}

# Body containers holding transcribed lines. Their @type carries the
# SegmOnto zone label, so every phase that walks a line back up to its
# zone stops at one of these.
TEXT_CONTAINERS = ("ab", "note", "fw", "head", "titlePart")

# Zones that carry the page apparatus rather than the text: folio
# numbers, quire signatures, running titles. They are transcribed and
# they belong to the edition, but a volume's extent — the figure a
# catalogue asks for — means the running text, and a running title
# repeated over 600 pages would add thousands of words to it.
APPARATUS_ZONES = frozenset({
    "NumberingZone", "QuireMarksZone", "RunningTitleZone",
})

# SegmOnto line types and their documentation URLs
SEGMONTO_LINES = {
    "CustomLine": "https://segmonto.github.io/gd/gdL/CustomLine/",
    "DefaultLine": "https://segmonto.github.io/gd/gdL/DefaultLine",
    "DropCapitalLine": "https://segmonto.github.io/gd/gdL/DropCapitalLine",
    "HeadingLine": "https://segmonto.github.io/gd/gdL/HeadingLine",
    "InterlinearLine": "https://segmonto.github.io/gd/gdL/InterlinearLine",
    "MusicLine": "https://segmonto.github.io/gd/gdL/MusicLine",
}
