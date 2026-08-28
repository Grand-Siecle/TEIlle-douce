# -----------------------------------------------------------
# Characterization tests for src/sourcedoc/builder.py and
# src/sourcedoc/elements.py, written ahead of the refactor
# described in docs/rapport_audit.md (SS3.4, 3.5, 3.7, 4.9, 2.3).
#
# These tests assert on the produced TEI XML, never on internal
# call mechanics (which signatures / re-parsing / find() usage
# are explicitly slated to change), so they should survive that
# refactor unchanged.
#
# Run: venv/bin/python -m pytest tests/test_sourcedoc_builder.py -q
# -----------------------------------------------------------
from pathlib import Path

import pytest
from lxml import etree

from src.constants import NS_ALTO, XML_ID
from src.metadata.iiif import IIIFMapping
from src.sourcedoc import builder
from src.sourcedoc.builder import extract_labels, build_sourcedoc
from src.sourcedoc.elements import SurfaceTree


def qlocal(el):
    """Local (namespace-stripped) tag name -- robust to the project's mix
    of bare and namespaced tags (audit SS4.2)."""
    return etree.QName(el).localname


# =============================================================================
# Fixtures (minimal ALTO XML, written to tmp_path -- no OCR_test/ dependency)
# =============================================================================

GOOD_ALTO_TMPL = """<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Tags>
    <OtherTag ID="BT1" LABEL="MainZone"/>
    <OtherTag ID="LT1" LABEL="DefaultLine"/>
  </Tags>
  <Layout><Page WIDTH="1000" HEIGHT="1500"><PrintSpace>
    <TextBlock ID="tb1" TAGREFS="BT1" HPOS="100" VPOS="200" WIDTH="300" HEIGHT="400">
      <TextLine ID="tl1" TAGREFS="LT1" HPOS="100" VPOS="200" WIDTH="300" HEIGHT="40" BASELINE="100 240 400 240">
        <String ID="s1" CONTENT="{word}" HPOS="100" VPOS="200" WIDTH="140" HEIGHT="40" WC="0.9"/>
      </TextLine>
    </TextBlock>
  </PrintSpace></Page></Layout>
</alto>"""

# Missing the closing </OtherTag> tag. Historically the worker's
# recover=True parser silently repaired this while extract_labels()'s
# strict parse crashed on it (the "two inconsistent parsers" gap of audit
# SS2.3). Since the fix there is one strict parser: this page is rejected,
# reported and skipped -- never silently repaired.
MALFORMED_ALTO = """<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Tags><OtherTag ID="BT1" LABEL="MainZone"></Tags>
  <Layout><Page WIDTH="1000" HEIGHT="1500"><PrintSpace>
    <TextBlock ID="tb1"><TextLine ID="tl1" BASELINE="0 0 10 10"><String ID="s1" CONTENT="x"/></TextLine></TextBlock>
  </PrintSpace></Page></Layout>
</alto>
"""


def write_alto(tmp_path, name, content):
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


# =============================================================================
# 1. extract_labels -- UNIT
# =============================================================================

def test_extract_labels_maps_id_to_label(tmp_path):
    alto = write_alto(tmp_path, "f1.xml", GOOD_ALTO_TMPL.format(word="hello"))
    labels = extract_labels(alto)
    assert labels == {"BT1": "MainZone", "LT1": "DefaultLine"}


# =============================================================================
# 2. SurfaceTree -- zones/lines -- UNIT
# =============================================================================

ALTO_FOR_LINES = """<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout><Page WIDTH="1000" HEIGHT="1500"><PrintSpace>
    <TextBlock ID="tb1">
      <TextLine ID="tl1" BASELINE="100.0 240.0 400.0 240.0">
        <String ID="s1" CONTENT="fallback" HPOS="0" VPOS="0" WIDTH="10" HEIGHT="10"/>
      </TextLine>
    </TextBlock>
  </PrintSpace></Page></Layout>
</alto>"""


def test_surfacetree_ids_normalization_and_baseline_path():
    root = etree.fromstring(ALTO_FOR_LINES.encode())
    tree = SurfaceTree("DOC1", "f1", root)

    surface = tree.surface({"n": "1"})
    assert surface.get(XML_ID) == "f1"

    zone_block = tree.zone1(surface, {"type": "MainZone"}, "tb1", 1)
    assert zone_block.get(XML_ID).startswith("zone_")
    assert zone_block.get("type") == "MainZone"

    # "default" (lowercase, as produced by some ALTO/TAGREFS sources) is
    # normalized to the SegmOnto "DefaultLine" taxon.
    zone_line = tree.zone2(zone_block, "tb1", {"type": "default"}, "tl1", 1)
    assert zone_line.get(XML_ID).startswith("zoneLine_")
    assert zone_line.get("type") == "DefaultLine"

    # Baseline <path> is built from the ALTO TextLine's BASELINE attribute.
    paths = [c for c in zone_line if qlocal(c) == "path"]
    assert len(paths) == 1
    assert paths[0].get(XML_ID).startswith("path_")
    assert paths[0].get("points") == "100,240 400,240"

    # <line> uses pre-extracted text when given...
    line_el = tree.line(zone_line, "tb1", "tl1", 1, "hello world")
    assert line_el.get(XML_ID).startswith("line_")
    assert line_el.get("n") == "1"
    assert line_el.text == "hello world"

    # ...and falls back to the ALTO String CONTENT when extracted_words
    # is empty.
    line_el2 = tree.line(zone_line, "tb1", "tl1", 2, "")
    assert line_el2.text == "fallback"


def test_surfacetree_surface_adds_iiif_graphic_when_mapped():
    root = etree.fromstring(ALTO_FOR_LINES.encode())
    mapping = IIIFMapping()
    mapping.mapping = {"f1": "https://example.org/iiif/f1/full/full/0/native.jpg"}

    tree = SurfaceTree("DOC1", "f1", root, mapping)
    surface = tree.surface({"n": "1"})

    graphics = [c for c in surface if qlocal(c) == "graphic"]
    assert len(graphics) == 1
    assert graphics[0].get("url") == "https://example.org/iiif/f1/full/full/0/native.jpg"

    # No entry for this folio in the mapping -> no <graphic> emitted.
    other = SurfaceTree("DOC1", "unmapped", root, mapping).surface({"n": "1"})
    assert [c for c in other if qlocal(c) == "graphic"] == []


# =============================================================================
# 3. SurfaceTree -- strings/glyphs -- UNIT
# =============================================================================

ALTO_FOR_STRINGS = """<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout><Page WIDTH="1000" HEIGHT="1500"><PrintSpace>
    <TextBlock ID="tb1"><TextLine ID="tl1">
      <String ID="s1" CONTENT="hi" WC="0.87"/>
      <String ID="s2" CONTENT="lo"/>
      <Glyph ID="g1" CONTENT="h" GC="0.5" WC="0.66"/>
      <Glyph ID="g2" CONTENT="i"/>
    </TextLine></TextBlock>
  </PrintSpace></Page></Layout>
</alto>"""


def test_zone3_string_certainty_conditional_and_target():
    root = etree.fromstring(ALTO_FOR_STRINGS.encode())
    tree = SurfaceTree("DOC1", "f1", root)
    textline = etree.Element("zone")

    with_wc = tree.zone3(textline, "tb1", "tl1", {"type": "String"}, "s1", 1)
    certs = [c for c in with_wc if qlocal(c) == "certainty"]
    assert len(certs) == 1
    assert certs[0].get("degree") == "0.87"
    assert certs[0].get("target") == f"#{with_wc.get(XML_ID)}"

    # No WC attribute on the ALTO String -> no <certainty> injected.
    without_wc = tree.zone3(textline, "tb1", "tl1", {"type": "String"}, "s2", 1)
    assert [c for c in without_wc if qlocal(c) == "certainty"] == []


def test_zone4_and_car_glyph_certainty_and_content():
    root = etree.fromstring(ALTO_FOR_STRINGS.encode())
    tree = SurfaceTree("DOC1", "f1", root)
    string_zone = etree.Element("zone")

    glyph_zone = tree.zone4(string_zone, "tb1", "tl1", "s1", {"type": "Glyph"}, "g1", 1)
    certs = [c for c in glyph_zone if qlocal(c) == "certainty"]
    assert len(certs) == 1
    assert certs[0].get("target") == f"#{glyph_zone.get(XML_ID)}"

    glyph_el = root.find('.//a:Glyph[@ID="g1"]', namespaces=NS_ALTO)
    c_el = tree.car(glyph_zone, glyph_el, "tb1", "tl1", "s1", "g1", 1)
    assert qlocal(c_el) == "c"
    assert c_el.text == "h"
    c_certs = [c for c in c_el if qlocal(c) == "certainty"]
    assert len(c_certs) == 1
    assert c_certs[0].get("target") == f"#{c_el.get(XML_ID)}"

    # Glyph with neither GC nor WC -> no <certainty> anywhere.
    glyph_zone2 = tree.zone4(string_zone, "tb1", "tl1", "s1", {"type": "Glyph"}, "g2", 1)
    assert [c for c in glyph_zone2 if qlocal(c) == "certainty"] == []
    glyph_el2 = root.find('.//a:Glyph[@ID="g2"]', namespaces=NS_ALTO)
    c_el2 = tree.car(glyph_zone2, glyph_el2, "tb1", "tl1", "s1", "g2", 1)
    assert [c for c in c_el2 if qlocal(c) == "certainty"] == []
    assert c_el2.text == "i"


# =============================================================================
# 4. build_sourcedoc -- INTEG
# =============================================================================

def test_build_sourcedoc_orders_surfaces_by_page_despite_imap_unordered(tmp_path, monkeypatch):
    # A small worker pool is enough to make ordering a real (not merely
    # theoretical) concern; the two files are also handed in reversed
    # order to make sure input order isn't what saves us.
    monkeypatch.setattr(builder, "MAX_WORKERS", 2)

    f1 = write_alto(tmp_path, "f1.xml", GOOD_ALTO_TMPL.format(word="pageone"))
    f2 = write_alto(tmp_path, "f2.xml", GOOD_ALTO_TMPL.format(word="pagetwo"))

    output_root = etree.Element("TEI")
    result = build_sourcedoc("DOC1", output_root, [f2, f1], {}, [], [], {})

    assert result is output_root
    source_doc = result.find("sourceDoc")
    assert source_doc is not None

    surfaces = source_doc.findall("surface")
    assert [s.get(XML_ID) for s in surfaces] == ["f1", "f2"]

    lines_text = [
        [el.text for el in s.iter() if qlocal(el) == "line"] for s in surfaces
    ]
    assert lines_text == [["pageone"], ["pagetwo"]]


# =============================================================================
# 5-6. audit SS2.3 -- une page malformee est signalee et sautee, jamais
# reparee en silence ; OtherTag sans LABEL n'interrompt plus l'extraction.
# =============================================================================

def test_malformed_alto_page_does_not_crash_the_run(tmp_path, monkeypatch):
    monkeypatch.setattr(builder, "MAX_WORKERS", 1)

    good = write_alto(tmp_path, "f1.xml", GOOD_ALTO_TMPL.format(word="hello"))
    write_alto(tmp_path, "f2.xml", MALFORMED_ALTO)

    output_root = etree.Element("TEI")
    build_sourcedoc("DOC1", output_root, [good, tmp_path / "f2.xml"], {}, [], [], {})

    surfaces = output_root.findall(".//surface")
    # The good page is still produced; the malformed one is reported and
    # skipped rather than aborting the whole document.
    assert [s.get(XML_ID) for s in surfaces] == ["f1"]


def test_extract_labels_othertag_without_label_no_keyerror(tmp_path):
    alto = write_alto(
        tmp_path,
        "f1.xml",
        """<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Tags>
    <OtherTag ID="BT1"/>
    <OtherTag ID="LT1" LABEL="DefaultLine"/>
  </Tags>
</alto>""",
    )

    labels = extract_labels(alto)

    assert isinstance(labels, dict)
    assert labels.get("LT1") == "DefaultLine"
    # The label-less entry must not blow up extraction; it's simply not a
    # usable mapping.
    assert "BT1" not in labels
