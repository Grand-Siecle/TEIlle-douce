# -----------------------------------------------------------
# Shared constants for ALTO2TEI pipeline
# XML namespaces and internal constants used across modules
# -----------------------------------------------------------

import uuid

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

# XML namespace attribute key
XML_ID = f"{{{NS_XML}}}id"

# =============================================================================
# SEGMONTO TAXONOMY
# =============================================================================

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

# SegmOnto line types and their documentation URLs
SEGMONTO_LINES = {
    "CustomLine": "https://segmonto.github.io/gd/gdL/CustomLine/",
    "DefaultLine": "https://segmonto.github.io/gd/gdL/DefaultLine",
    "DropCapitalLine": "https://segmonto.github.io/gd/gdL/DropCapitalLine",
    "HeadingLine": "https://segmonto.github.io/gd/gdL/HeadingLine",
    "InterlinearLine": "https://segmonto.github.io/gd/gdL/InterlinearLine",
    "MusicLine": "https://segmonto.github.io/gd/gdL/MusicLine",
}
