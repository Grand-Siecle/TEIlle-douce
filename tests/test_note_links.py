# Run: venv/bin/python tests/test_note_links.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lxml import etree
from src.body.note_links import link_notes_to_lines

XML_ID = "{http://www.w3.org/XML/1998/namespace}id"

# Surface avec une MainZone (2 lignes) et une MarginTextZone qui recouvre
# verticalement la 2e ligne seulement.
TEI_MIN = """<TEI>
  <sourceDoc>
    <surface xml:id="f1">
      <zone xml:id="zone_main" type="MainZone" uly="0" lry="1000">
        <zone xml:id="zoneLine_a" type="DefaultLine" uly="100" lry="150"/>
        <zone xml:id="zoneLine_b" type="DefaultLine" uly="500" lry="560"/>
      </zone>
      <zone xml:id="zone_marge" type="MarginTextZone" uly="490" lry="580"/>
    </surface>
  </sourceDoc>
  <text><body><div>
    <note corresp="#zone_marge" type="MarginTextZone">texte de marge</note>
  </div></body></text>
</TEI>"""


def test_note_targets_overlapping_line():
    root = etree.fromstring(TEI_MIN)
    linked = link_notes_to_lines(root)
    note = root.find(".//note")
    assert linked == 1
    assert note.get("target") == "#zoneLine_b", note.get("target")


def test_no_overlap_no_target():
    root = etree.fromstring(TEI_MIN.replace('uly="490" lry="580"', 'uly="2000" lry="2100"'))
    linked = link_notes_to_lines(root)
    assert linked == 0
    assert root.find(".//note").get("target") is None


if __name__ == "__main__":
    test_note_targets_overlapping_line()
    test_no_overlap_no_target()
    print("OK test_note_links")
