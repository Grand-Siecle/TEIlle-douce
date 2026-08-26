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

# Meme MainZone, mais zoneLine_b (uly=500, plus bas) declaree AVANT
# zoneLine_a (uly=100, plus haut) dans le document ; la note recouvre les
# deux lignes -> l'ancrage doit rester la plus haute malgre l'ordre du doc.
TEI_ORDER = """<TEI>
  <sourceDoc>
    <surface xml:id="f1">
      <zone xml:id="zone_main" type="MainZone" uly="0" lry="1000">
        <zone xml:id="zoneLine_b" type="DefaultLine" uly="500" lry="560"/>
        <zone xml:id="zoneLine_a" type="DefaultLine" uly="100" lry="150"/>
      </zone>
      <zone xml:id="zone_marge" type="MarginTextZone" uly="50" lry="600"/>
    </surface>
  </sourceDoc>
  <text><body><div>
    <note corresp="#zone_marge" type="MarginTextZone">texte de marge</note>
  </div></body></text>
</TEI>"""

# 3 lignes principales, une note dont le texte de marge commence par "*"
# et dont l'appel se retrouve dans le texte de la ligne b (pas la plus
# haute) -> b doit devenir l'ancre.
TEI_STAR = """<TEI>
  <sourceDoc>
    <surface xml:id="f1">
      <zone xml:id="zone_main" type="MainZone" uly="0" lry="1000">
        <zone xml:id="zoneLine_a" type="DefaultLine" uly="100" lry="150"><line xml:id="l_a">premiere ligne sans appel</line></zone>
        <zone xml:id="zoneLine_b" type="DefaultLine" uly="160" lry="210"><line xml:id="l_b">* la ligne avec l appel</line></zone>
        <zone xml:id="zoneLine_c" type="DefaultLine" uly="220" lry="270"><line xml:id="l_c">troisieme ligne</line></zone>
      </zone>
      <zone xml:id="zone_marge" type="MarginTextZone" uly="90" lry="280"><line xml:id="l_m">* Deus dixit</line></zone>
    </surface>
  </sourceDoc>
  <text><body><div>
    <note corresp="#zone_marge" type="MarginTextZone">note</note>
  </div></body></text>
</TEI>"""

# Une seule ligne principale, recouverte par deux zones de marge distinctes
# (donc deux notes) -> meme ancre -> numerotation @n="1"/"2" dans l'ordre
# vertical des zones de marge (uly croissant), independamment de l'ordre
# des <note> dans le document (ici delibrement inverse : marge2 avant
# marge1 dans le corps du texte).
TEI_MULTI_NOTE = """<TEI>
  <sourceDoc>
    <surface xml:id="f1">
      <zone xml:id="zone_main" type="MainZone" uly="0" lry="1000">
        <zone xml:id="zoneLine_a" type="DefaultLine" uly="100" lry="150"><line xml:id="l_a">ligne unique</line></zone>
      </zone>
      <zone xml:id="zone_marge1" type="MarginTextZone" uly="95" lry="160"><line xml:id="l_m1">premiere note</line></zone>
      <zone xml:id="zone_marge2" type="MarginTextZone" uly="120" lry="160"><line xml:id="l_m2">deuxieme note</line></zone>
    </surface>
  </sourceDoc>
  <text><body><div>
    <note corresp="#zone_marge2" type="MarginTextZone">note B</note>
    <note corresp="#zone_marge1" type="MarginTextZone">note A</note>
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


# Structure fidele au corpus reel : la MarginTextZone ne porte pas de
# <line> directement mais imbrique ses propres zoneLine_* (chacune avec
# son <line>) -> _zone_text doit descendre dans cette imbrication pour
# trouver le texte de la premiere ligne de marge.
TEI_STAR_NESTED_MARGIN = """<TEI>
  <sourceDoc>
    <surface xml:id="f1">
      <zone xml:id="zone_main" type="MainZone" uly="0" lry="1000">
        <zone xml:id="zoneLine_a" type="DefaultLine" uly="100" lry="150"><line xml:id="l_a">premiere ligne sans appel</line></zone>
        <zone xml:id="zoneLine_b" type="DefaultLine" uly="160" lry="210"><line xml:id="l_b">* la ligne avec l appel</line></zone>
        <zone xml:id="zoneLine_c" type="DefaultLine" uly="220" lry="270"><line xml:id="l_c">troisieme ligne</line></zone>
      </zone>
      <zone xml:id="zone_marge" type="MarginTextZone" uly="90" lry="280">
        <zone xml:id="zoneLine_m" type="DefaultLine" uly="90" lry="140"><line xml:id="l_m">* Deus dixit</line></zone>
      </zone>
    </surface>
  </sourceDoc>
  <text><body><div>
    <note corresp="#zone_marge" type="MarginTextZone">note</note>
  </div></body></text>
</TEI>"""


def test_star_promotes_anchor_nested_margin():
    root = etree.fromstring(TEI_STAR_NESTED_MARGIN)
    link_notes_to_lines(root)
    tgt = root.find(".//note").get("target").split()
    assert tgt[0] == "#zoneLine_b", tgt


def test_single_note_no_n():
    root = etree.fromstring(TEI_MIN)
    link_notes_to_lines(root)
    note = root.find(".//note")
    assert note.get("n") is None


def test_anchor_is_first_line_sorted():
    root = etree.fromstring(TEI_ORDER)
    link_notes_to_lines(root)
    note = root.find(".//note")
    tgt = note.get("target").split()
    assert tgt[0] == "#zoneLine_a", tgt
    assert set(tgt) == {"#zoneLine_a", "#zoneLine_b"}


def test_star_promotes_anchor():
    root = etree.fromstring(TEI_STAR)
    link_notes_to_lines(root)
    tgt = root.find(".//note").get("target").split()
    assert tgt[0] == "#zoneLine_b", tgt          # l appel * promeut la ligne b
    assert set(tgt) == {"#zoneLine_a", "#zoneLine_b", "#zoneLine_c"}


def test_star_fallback_without_match():
    xml = TEI_STAR.replace("* la ligne avec l appel", "ligne sans appel")
    root = etree.fromstring(xml)
    link_notes_to_lines(root)
    tgt = root.find(".//note").get("target").split()
    assert tgt[0] == "#zoneLine_a", tgt          # fallback : la plus haute


def test_multiple_notes_same_anchor_numbered():
    root = etree.fromstring(TEI_MULTI_NOTE)
    linked = link_notes_to_lines(root)
    assert linked == 2
    notes = root.findall(".//note")
    note_marge1 = [n for n in notes if n.get("corresp") == "#zone_marge1"][0]
    note_marge2 = [n for n in notes if n.get("corresp") == "#zone_marge2"][0]
    assert note_marge1.get("target") == "#zoneLine_a"
    assert note_marge2.get("target") == "#zoneLine_a"
    assert note_marge1.get("n") == "1", note_marge1.get("n")  # marge la plus haute (uly=95)
    assert note_marge2.get("n") == "2", note_marge2.get("n")


if __name__ == "__main__":
    test_note_targets_overlapping_line()
    test_no_overlap_no_target()
    test_single_note_no_n()
    test_anchor_is_first_line_sorted()
    test_star_promotes_anchor()
    test_star_promotes_anchor_nested_margin()
    test_star_fallback_without_match()
    test_multiple_notes_same_anchor_numbered()
    print("OK test_note_links")
