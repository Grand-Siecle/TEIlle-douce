# -----------------------------------------------------------
# Characterization tests for teille_douce/sourcedoc/builder.py and
# teille_douce/sourcedoc/elements.py, written ahead of the refactor
# described in docs/rapport_audit.md (SS3.4, 3.5, 3.7, 4.9, 2.3).
#
# These tests assert on the produced TEI XML, never on internal
# call mechanics (which signatures / re-parsing / find() usage
# are explicitly slated to change), so they should survive that
# refactor unchanged.
#
# Run: venv/bin/python -m pytest tests/test_sourcedoc_builder.py -q
# -----------------------------------------------------------
import logging
from dataclasses import replace
from pathlib import Path

import pytest
from lxml import etree

from teille_douce.constants import NS_ALTO, XML_ID
from teille_douce.metadata.iiif import IIIFMapping
from teille_douce.sourcedoc import builder
from teille_douce.sourcedoc.builder import extract_labels, build_sourcedoc
from teille_douce.settings import get_settings, set_settings
from teille_douce.sourcedoc.elements import SurfaceTree


@pytest.fixture(autouse=True)
def _restore_settings():
    """The worker count is a setting now. Cases that shrink the pool install
    their own; this hands the next file an untouched one."""
    before = get_settings()
    yield
    set_settings(before)


def _workers(count):
    """Run this case's pool with `count` workers."""
    set_settings(replace(get_settings(), max_workers=count))


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
# SS2.3). Since the fix: strict parse first, then one explicit recovery
# attempt -- the page is kept but the recovery is REPORTED (warning),
# never silent. Only a page that not even libxml2 recovery can read is
# skipped (see UNRECOVERABLE_ALTO below).
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

    zone_block = tree.zone1(surface, {"type": "MainZone"}, "tb1")
    assert zone_block.get(XML_ID).startswith("zone_")
    assert zone_block.get("type") == "MainZone"

    # "default" (lowercase, as produced by some ALTO/TAGREFS sources) is
    # normalized to the SegmOnto "DefaultLine" taxon.
    zone_line = tree.zone2(zone_block, "tb1", {"type": "default"}, "tl1")
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
    _workers(2)

    f1 = write_alto(tmp_path, "f1.xml", GOOD_ALTO_TMPL.format(word="pageone"))
    f2 = write_alto(tmp_path, "f2.xml", GOOD_ALTO_TMPL.format(word="pagetwo"))

    output_root = etree.Element("TEI")
    result, skipped = build_sourcedoc("DOC1", output_root, [f2, f1], [], [], {})

    assert result is output_root
    assert skipped == []
    source_doc = result.find("sourceDoc")
    assert source_doc is not None

    surfaces = source_doc.findall("surface")
    assert [s.get(XML_ID) for s in surfaces] == ["f1", "f2"]

    lines_text = [
        [el.text for el in s.iter() if qlocal(el) == "line"] for s in surfaces
    ]
    assert lines_text == [["pageone"], ["pagetwo"]]


# =============================================================================
# 4bis. Identifiants deterministes (audit 2.8)
# =============================================================================

def test_build_sourcedoc_ids_are_deterministic_across_runs(tmp_path, monkeypatch):
    """Audit 2.8 : memes ALTO en entree -> memes xml:id en sortie (uuid5),
    et un document different produit des ids differents."""
    _workers(1)
    f1 = write_alto(tmp_path, "f1.xml", GOOD_ALTO_TMPL.format(word="hello"))

    def ids_pour(document):
        root = etree.Element("TEI")
        build_sourcedoc(document, root, [f1], [], [], {})
        return [el.get(XML_ID) for el in root.iter() if el.get(XML_ID)]

    run1, run2 = ids_pour("DOC1"), ids_pour("DOC1")
    assert run1 == run2, "deux executions sur le meme document divergent"
    assert len(run1) == len(set(run1)), "xml:id dupliques dans une meme sortie"
    assert ids_pour("DOC2") != run1, (
        "deux documents distincts ne doivent pas partager leurs ids"
    )


DUPLICATE_BLOCK_ALTO = """<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Tags>
    <OtherTag ID="BT1" LABEL="MainZone"/>
    <OtherTag ID="LT1" LABEL="DefaultLine"/>
  </Tags>
  <Layout><Page WIDTH="1000" HEIGHT="1500"><PrintSpace>
    <TextBlock ID="tb1" TAGREFS="BT1" HPOS="100" VPOS="200" WIDTH="300" HEIGHT="400">
      <TextLine ID="tl1" TAGREFS="LT1" HPOS="100" VPOS="200" WIDTH="300" HEIGHT="40" BASELINE="100 240 400 240">
        <String ID="s1" CONTENT="premier" HPOS="100" VPOS="200" WIDTH="140" HEIGHT="40"/>
      </TextLine>
    </TextBlock>
    <TextBlock ID="tb1" TAGREFS="BT1" HPOS="100" VPOS="700" WIDTH="300" HEIGHT="400">
      <TextLine ID="tl2" TAGREFS="LT1" HPOS="100" VPOS="700" WIDTH="300" HEIGHT="40" BASELINE="100 740 400 740">
        <String ID="s2" CONTENT="second" HPOS="100" VPOS="700" WIDTH="140" HEIGHT="40"/>
      </TextLine>
    </TextBlock>
  </PrintSpace></Page></Layout>
</alto>"""


def test_build_sourcedoc_reports_duplicate_alto_ids_from_workers(tmp_path, monkeypatch, caplog):
    """La desambiguisation des ID ALTO dupliques a lieu dans un worker
    forkserver : un logger appele la-bas n'atteint jamais le log du parent.
    Le signalement doit donc voyager par le tuple de retour du worker et
    ressortir en warning cote parent."""
    _workers(1)
    f = write_alto(tmp_path, "f1.xml", DUPLICATE_BLOCK_ALTO)

    output_root = etree.Element("TEI")
    with caplog.at_level(logging.WARNING, logger="teille_douce.sourcedoc.builder"):
        _, skipped = build_sourcedoc("DOC1", output_root, [f], [], [], {})

    assert skipped == []
    ids = [el.get(XML_ID) for el in output_root.iter() if el.get(XML_ID)]
    assert len(ids) == len(set(ids)), "xml:id dupliques malgre la desambiguisation"
    assert any("duplicate ALTO id" in r.message for r in caplog.records), (
        [r.message for r in caplog.records]
    )


def test_surfacetree_disambiguates_duplicate_alto_ids_deterministically():
    """Les exports ALTO reels dupliquent parfois un ID sur une page (cas
    present dans la fixture e2e : deux TextBlock ID='block_2'). uuid4
    masquait le doublon ; uuid5 doit desambiguiser sans collision xml:id,
    et de facon reproductible d'un run a l'autre."""
    from teille_douce.sourcedoc.elements import SurfaceTree

    def deux_zones():
        tree = SurfaceTree("DOC1", "f1", etree.Element("alto"))
        surface = etree.Element("surface")
        z1 = tree.zone1(surface, {}, "block_2")
        z2 = tree.zone1(surface, {}, "block_2")
        return z1.get(XML_ID), z2.get(XML_ID)

    a1, a2 = deux_zones()
    b1, b2 = deux_zones()
    assert a1 != a2, "deux blocs au meme ID ALTO doivent avoir des xml:id distincts"
    assert (a1, a2) == (b1, b2), "la desambiguisation doit rester deterministe"


# =============================================================================
# 5-6. audit SS2.3 -- page malformee : recuperee avec warning quand libxml2
# le peut, signalee et sautee sinon ; jamais reparee en silence, jamais
# fatale au document. OtherTag sans LABEL n'interrompt plus l'extraction.
# =============================================================================

# Not even libxml2 recovery can build a tree out of this.
UNRECOVERABLE_ALTO = "\x00\x01ceci n'est pas du XML\x02"


def test_malformed_alto_page_does_not_crash_the_run(tmp_path, monkeypatch, caplog):
    _workers(1)

    good = write_alto(tmp_path, "f1.xml", GOOD_ALTO_TMPL.format(word="hello"))
    write_alto(tmp_path, "f2.xml", MALFORMED_ALTO)
    (tmp_path / "f3.xml").write_text(UNRECOVERABLE_ALTO, encoding="utf-8")

    output_root = etree.Element("TEI")
    with caplog.at_level(logging.WARNING, logger="teille_douce.sourcedoc.builder"):
        _, skipped = build_sourcedoc(
            "DOC1", output_root,
            [good, tmp_path / "f2.xml", tmp_path / "f3.xml"],
            {}, [], [], {},
        )

    surfaces = output_root.findall(".//surface")
    # The clean page and the RECOVERABLE malformed page are both produced;
    # only the unrecoverable one is skipped -- and both anomalies are
    # reported (warning for the recovery, error for the skip).
    assert [s.get(XML_ID) for s in surfaces] == ["f1", "f2"]
    assert skipped == [3]
    messages = [r.message for r in caplog.records]
    assert any("recovered" in m for m in messages), messages
    assert any("page 3 skipped" in m for m in messages), messages


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


# =============================================================================
# 8. Two ALTO files claiming one page number
#
# order_files() derives a page number from the FIRST digit run of the file
# stem, and hands the same sentinel to every stem holding no digit at all.
# Two files can therefore claim one number. Routing worker results by that
# number dropped one of the pages and emitted the other twice, under a
# duplicate xml:id no XML parser reads back -- while the run reported a
# success.
# =============================================================================

def test_build_sourcedoc_keeps_both_pages_when_page_numbers_collide(
    tmp_path, monkeypatch, caplog
):
    """f1.xml and f1-np.xml both yield page number 1. Both are real pages
    and both must reach the output, each with its own surface."""
    _workers(2)

    first = write_alto(tmp_path, "f1.xml", GOOD_ALTO_TMPL.format(word="pageone"))
    second = write_alto(tmp_path, "f1-np.xml", GOOD_ALTO_TMPL.format(word="pagetwo"))

    output_root = etree.Element("TEI")
    with caplog.at_level(logging.WARNING, logger="teille_douce.sourcedoc.builder"):
        _, skipped = build_sourcedoc(
            "DOC1", output_root, [first, second], [], [], {}
        )

    assert skipped == []
    surfaces = output_root.findall(".//surface")
    assert [s.get(XML_ID) for s in surfaces] == ["f1", "f1-np"]

    lines_text = [
        [el.text for el in s.iter() if qlocal(el) == "line"] for s in surfaces
    ]
    assert lines_text == [["pageone"], ["pagetwo"]]

    ids = [el.get(XML_ID) for el in output_root.iter() if el.get(XML_ID)]
    assert len(ids) == len(set(ids)), "duplicate xml:id in the produced tree"

    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "page number 1" in m and "f1.xml" in m and "f1-np.xml" in m
        for m in messages
    ), messages


def test_build_sourcedoc_keeps_both_pages_when_no_filename_has_a_number(
    tmp_path, monkeypatch, caplog
):
    """Binding plates are often named without a page number. They all get
    one sentinel sort key, so they collide with each other -- and the
    sentinel must not be reported to the operator as a page number."""
    _workers(2)

    plate = write_alto(tmp_path, "plat-sup.xml", GOOD_ALTO_TMPL.format(word="plate"))
    cover = write_alto(tmp_path, "couverture.xml", GOOD_ALTO_TMPL.format(word="cover"))

    output_root = etree.Element("TEI")
    with caplog.at_level(logging.WARNING, logger="teille_douce.sourcedoc.builder"):
        _, skipped = build_sourcedoc(
            "DOC1", output_root, [plate, cover], [], [], {}
        )

    assert skipped == []
    surfaces = output_root.findall(".//surface")
    assert [s.get(XML_ID) for s in surfaces] == ["plat-sup", "couverture"]

    lines_text = [
        [el.text for el in s.iter() if qlocal(el) == "line"] for s in surfaces
    ]
    assert lines_text == [["plate"], ["cover"]]

    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "no page number" in m and "plat-sup.xml" in m and "couverture.xml" in m
        for m in messages
    ), messages
    assert not any("999999" in m for m in messages), (
        "the sort sentinel must not be shown as a page number", messages
    )


def test_build_sourcedoc_refuses_two_pages_under_one_surface_id(
    tmp_path, monkeypatch
):
    """xml_id_safe() prefixes a leading digit, so 1.xml and f1.xml both
    give the surface id "f1". Writing both welds two pages into one and
    produces a file lxml refuses to read back: the document fails instead."""
    _workers(1)

    bare = write_alto(tmp_path, "1.xml", GOOD_ALTO_TMPL.format(word="pageone"))
    prefixed = write_alto(tmp_path, "f1.xml", GOOD_ALTO_TMPL.format(word="pagetwo"))

    output_root = etree.Element("TEI")
    with pytest.raises(RuntimeError) as excinfo:
        build_sourcedoc("DOC1", output_root, [bare, prefixed], [], [], {})

    message = str(excinfo.value)
    assert "f1" in message
    assert "1.xml" in message and "f1.xml" in message
