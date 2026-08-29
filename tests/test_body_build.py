# Run: venv/bin/python -m pytest tests/test_body_build.py -q
"""
Tests unitaires pour src/body/builder.py::build_body et
_apply_language_detection, ainsi que src/body/text.py::Text._extract_lines.

Perimetre strict : uniquement ces fonctions. Les autres fonctions de
builder.py (_walk_sentence_children, _parse_line_groups,
_rebuild_with_modernization, _append_tokens_with_foreign, _append_choice,
apply_modernization, apply_modernization_enriched, _wrap_plain_lines,
_insert_foreign_inline, _build_offset_to_line, _splice_lb_tail) sont
couvertes par d'autres lots de tests en parallele et ne sont pas testees ici.

Les fixtures sont des objets Line construits a la main (pas d'ALTO reel,
pas de OCR_test/) et build_body est toujours appele avec detect_lang=False,
sauf dans les tests dedies a _apply_language_detection ou le detecteur
lingua reel est remplace par un objet factice (_StubDetector).
"""

import pytest
from lxml import etree

from src.body.builder import build_body, _apply_language_detection, XML_LANG
from src.body.text import Text, Line


def qlocal(el):
    """Nom local du tag, robuste aux deux conventions de namespace (audit 4.2)."""
    return etree.QName(el).localname


def make_line(id, zone_type, zone_id, page_id, text="texte", line_type="DefaultLine", n=None, page_n=None):
    """Construit un Line a la main, comme le ferait Text._extract_lines."""
    return Line(
        id=id,
        n=n,
        text=text,
        line_type=line_type,
        zone_type=zone_type,
        zone_id=zone_id,
        page_id=page_id,
        page_n=page_n,
    )


# =============================================================================
# 1. build_body -- dispatch par type de zone
# =============================================================================


def test_build_body_dispatches_zone_types_to_expected_elements():
    lines = [
        make_line("l_main", "MainZone", "zone_main", "p1", text="corps du texte"),
        make_line("l_margin", "MarginTextZone", "zone_margin", "p1", text="note de marge"),
        make_line("l_num", "NumberingZone", "zone_num", "p1", text="12"),
        make_line("l_quire", "QuireMarksZone", "zone_quire", "p1", text="A2"),
        make_line("l_running", "RunningTitleZone", "zone_running", "p1", text="Titre courant"),
    ]
    root = etree.Element("TEI")

    build_body(root, lines, detect_lang=False)

    div = root.find(".//div")
    tags = [qlocal(el) for el in div]
    # une seule <pb> (page unique), puis un element par ligne rencontree.
    # NumberingZone/QuireMarksZone/RunningTitleZone produisent chacun leur
    # PROPRE <fw> -- contrairement a <ab>/<note>, ces trois types ne
    # fusionnent jamais entre lignes consecutives : le code ne teste jamais
    # `last_element.tag != "fw"` avant d'en creer un nouveau.
    assert tags == ["pb", "ab", "note", "fw", "fw", "fw"]

    ab = div[1]
    assert ab.get("corresp") == "#zone_main"
    assert ab.get("type") == "MainZone"

    note = div[2]
    assert note.get("corresp") == "#zone_margin"
    assert note.get("type") == "MarginTextZone"

    fw_num, fw_quire, fw_running = div[3], div[4], div[5]
    assert (fw_num.get("type"), fw_quire.get("type"), fw_running.get("type")) == (
        "NumberingZone",
        "QuireMarksZone",
        "RunningTitleZone",
    )


def test_build_body_marginzone_lines_merge_into_single_note():
    # Contrairement a NumberingZone/QuireMarksZone/RunningTitleZone,
    # MarginTextZone teste bien `last_element.tag != "note"` : des lignes
    # consecutives du meme type de zone fusionnent dans le meme <note>.
    lines = [
        make_line("l1", "MarginTextZone", "zone_margin", "p1", text="premiere"),
        make_line("l2", "MarginTextZone", "zone_margin", "p1", text="seconde"),
    ]
    root = etree.Element("TEI")

    build_body(root, lines, detect_lang=False)

    div = root.find(".//div")
    tags = [qlocal(el) for el in div]
    assert tags == ["pb", "note"]
    note = div[1]
    lbs = [c for c in note if qlocal(c) == "lb"]
    assert len(lbs) == 2


# =============================================================================
# 2. build_body -- pagination (<pb>)
# =============================================================================


def test_build_body_inserts_one_pb_per_page_change_no_duplicates():
    lines = [
        make_line("l1", "MainZone", "zone_a", "p1", text="a1"),
        make_line("l2", "MainZone", "zone_a", "p1", text="a2"),
        make_line("l3", "MarginTextZone", "zone_b", "p2", text="b1"),
        make_line("l4", "MainZone", "zone_c", "p3", text="c1"),
    ]
    root = etree.Element("TEI")

    build_body(root, lines, detect_lang=False)

    div = root.find(".//div")
    tags = [qlocal(el) for el in div]
    assert tags == ["pb", "ab", "pb", "note", "pb", "ab"]

    # aucun <pb> avant le premier contenu de page
    assert qlocal(div[0]) == "pb"

    pbs = [el for el in div if qlocal(el) == "pb"]
    assert len(pbs) == 3  # un par page, jamais de doublon
    assert [pb.get("corresp") for pb in pbs] == ["#p1", "#p2", "#p3"]

    # deux lignes MainZone consecutives sur la meme page -> un seul <ab>,
    # pas de <pb> intercale
    ab_p1 = div[1]
    assert qlocal(ab_p1) == "ab"
    assert len([c for c in ab_p1 if qlocal(c) == "lb"]) == 2


# =============================================================================
# 3. build_body -- regroupement <hi> (DropCapitalLine / HeadingLine)
# =============================================================================


def test_build_body_groups_dropcap_and_heading_lines_into_hi():
    lines = [
        make_line("l1", "MainZone", "zone_a", "p1", text="D", line_type="DropCapitalLine"),
        make_line("l2", "MainZone", "zone_a", "p1", text="ii", line_type="DropCapitalLine"),
        make_line("l3", "MainZone", "zone_a", "p1", text="Titre", line_type="HeadingLine"),
        make_line("l4", "MainZone", "zone_a", "p1", text="texte normal", line_type="DefaultLine"),
        make_line("l5", "MainZone", "zone_a", "p1", text="D2", line_type="DropCapitalLine"),
    ]
    root = etree.Element("TEI")

    build_body(root, lines, detect_lang=False)

    div = root.find(".//div")
    ab = div[1]
    assert qlocal(ab) == "ab"

    children = list(ab)
    kinds = [(qlocal(c), c.get("rend")) for c in children]
    assert kinds == [
        ("hi", "DropCapitalLine"),
        ("hi", "HeadingLine"),
        ("lb", None),
        ("hi", "DropCapitalLine"),
    ]

    # les 2 DropCapitalLine consecutives sont regroupees dans le meme <hi>
    assert len(list(children[0])) == 2
    # la HeadingLine ouvre un nouveau <hi> (rend different)
    assert len(list(children[1])) == 1
    # une DefaultLine interrompt le regroupement : le <hi rend="DropCapitalLine">
    # qui suit est un NOUVEAU <hi>, meme si le rend est identique au premier
    assert children[0] is not children[3]
    assert len(list(children[3])) == 1


# =============================================================================
# 4. build_body -- <lb> par ligne, @corresp vers le sourceDoc
# =============================================================================


def test_build_body_emits_one_lb_per_line_with_corresp_to_sourcedoc():
    lines = [
        make_line("lid1", "MainZone", "zone_a", "p1", text="alpha"),
        make_line("lid2", "MarginTextZone", "zone_b", "p1", text="beta"),
        make_line("lid3", "NumberingZone", "zone_c", "p1", text="3"),
    ]
    root = etree.Element("TEI")

    build_body(root, lines, detect_lang=False)

    div = root.find(".//div")
    lbs = [el for el in div.iter() if qlocal(el) == "lb"]
    assert len(lbs) == 3
    assert [lb.get("corresp") for lb in lbs] == ["#lid1", "#lid2", "#lid3"]
    assert [lb.tail for lb in lbs] == ["alpha", "beta", "3"]


# =============================================================================
# 5. Text._extract_lines
# =============================================================================

TEI_SOURCE = """<TEI>
  <sourceDoc>
    <surface xml:id="f1" n="3">
      <zone xml:id="zone_main" type="MainZone">
        <zone xml:id="zoneLine_a" type="DefaultLine"><line n="1">premiere ligne</line></zone>
        <zone xml:id="zoneLine_b" type="HeadingLine"><line n="2">titre</line></zone>
      </zone>
      <zone xml:id="zone_marge" type="MarginTextZone">
        <zone xml:id="zoneLine_m" type="DefaultLine"><line n="1">note marginale</line></zone>
      </zone>
    </surface>
  </sourceDoc>
</TEI>"""


def test_text_extract_lines_walks_zone_zoneline_line():
    root = etree.fromstring(TEI_SOURCE.encode("utf-8"))

    text = Text(root)

    assert len(text.data) == 3
    l_main, l_heading, l_margin = text.data

    assert l_main.id == "zoneLine_a"
    assert l_main.n == "1"
    assert l_main.text == "premiere ligne"
    assert l_main.line_type == "DefaultLine"
    assert l_main.zone_type == "MainZone"
    assert l_main.zone_id == "zone_main"
    assert l_main.page_id == "f1"
    # le @n de la surface remonte au niveau ligne (plumbing de l'audit 1.6)
    assert l_main.page_n == "3"

    assert l_heading.id == "zoneLine_b"
    assert l_heading.line_type == "HeadingLine"
    assert l_heading.zone_type == "MainZone"
    assert l_heading.zone_id == "zone_main"
    assert l_heading.page_id == "f1"

    assert l_margin.id == "zoneLine_m"
    assert l_margin.text == "note marginale"
    assert l_margin.zone_type == "MarginTextZone"
    assert l_margin.zone_id == "zone_marge"
    assert l_margin.page_id == "f1"


TEI_SOURCE_EMPTY_LINE = """<TEI>
  <sourceDoc>
    <surface xml:id="f1">
      <zone xml:id="zone_main" type="MainZone">
        <zone xml:id="zoneLine_a" type="DefaultLine"><line n="1"/></zone>
      </zone>
    </surface>
  </sourceDoc>
</TEI>"""


def test_text_extract_lines_defaults_missing_text_to_empty_string():
    root = etree.fromstring(TEI_SOURCE_EMPTY_LINE.encode("utf-8"))

    text = Text(root)

    assert len(text.data) == 1
    assert text.data[0].text == ""
    # surface sans @n : page_n reste None
    assert text.data[0].page_n is None


# =============================================================================
# 6. _apply_language_detection (detecteur stubbe -- pas de lingua reel)
# =============================================================================


class _StubDetector:
    """Detecteur factice : aucun modele lingua charge, tests rapides."""

    def __init__(self, lang=None):
        self.lang = lang
        self.detect_calls = []
        self.foreign_calls = []

    def detect(self, text):
        self.detect_calls.append(text)
        return self.lang

    def detect_foreign_segments(self, text, primary_lang=None):
        self.foreign_calls.append((text, primary_lang))
        return []  # _insert_foreign_inline est hors perimetre de ce fichier

    def detect_primary_and_segments(self, text):
        """API combinee utilisee par _apply_language_detection : le stub
        journalise dans les deux listes historiques."""
        self.detect_calls.append(text)
        self.foreign_calls.append((text, self.lang))
        return self.lang, []


def test_apply_language_detection_sets_xml_lang_from_stub_detector():
    ab = etree.Element("ab")
    note = etree.Element("note")
    empty = etree.Element("ab")

    containers = [
        (ab, ["Bonjour", "le monde"]),
        (note, ["Salut", None]),
        (empty, ["", None]),
    ]
    detector = _StubDetector(lang="fr")

    _apply_language_detection(containers, detector)

    assert ab.attrib[XML_LANG] == "fr"
    assert note.attrib[XML_LANG] == "fr"
    # conteneur sans texte exploitable -> aucun xml:lang, aucun appel a detect()
    assert XML_LANG not in empty.attrib
    assert detector.detect_calls == ["Bonjour le monde", "Salut"]
    assert detector.foreign_calls == [("Bonjour le monde", "fr"), ("Salut", "fr")]


def test_apply_language_detection_skips_xml_lang_when_detector_returns_none():
    ab = etree.Element("ab")
    detector = _StubDetector(lang=None)

    _apply_language_detection([(ab, ["texte non identifiable"])], detector)

    # detect() a bien ete appele mais un resultat falsy ne pose pas xml:lang
    assert XML_LANG not in ab.attrib
    assert detector.detect_calls == ["texte non identifiable"]
    # detect_foreign_segments est quand meme appele, avec primary_lang=None
    assert detector.foreign_calls == [("texte non identifiable", None)]


# =============================================================================
# xfail -- constats du rapport d'audit (docs/rapport_audit.md)
# =============================================================================


def test_build_body_titlepagezone_gets_fallback_ab_after_fix():
    """Audit 1.1 : les zones sans branche dediee tombent dans un <ab> de repli."""
    lines = [make_line("l_title", "TitlePageZone", "zone_title", "p1", text="LE TITRE DU LIVRE")]
    root = etree.Element("TEI")

    build_body(root, lines, detect_lang=False)

    div = root.find(".//div")
    fallback = [el for el in div if qlocal(el) == "ab" and el.get("type") == "TitlePageZone"]
    assert len(fallback) == 1, "attendu : <ab type='TitlePageZone'> en repli ; actuellement la ligne est perdue"
    assert fallback[0].find("lb").tail == "LE TITRE DU LIVRE"


def test_build_body_fallback_ab_groups_by_zone_without_absorbing_main_text():
    """
    Audit 1.1, non-regression du repli : deux lignes consecutives d'une meme
    zone non geree partagent un seul <ab>, deux zones distinctes donnent deux
    <ab>, et une ligne MainZone qui suit un <ab> de repli ouvre son propre
    <ab> au lieu d'etre absorbee dedans (et reciproquement).
    """
    lines = [
        make_line("l_main1", "MainZone", "zone_main", "p1", text="corps"),
        make_line("l_stamp1", "StampZone", "zone_stamp", "p1", text="cachet ligne 1"),
        make_line("l_stamp2", "StampZone", "zone_stamp", "p1", text="cachet ligne 2"),
        make_line("l_custom", "CustomZone", "zone_custom", "p1", text="marginalia"),
        make_line("l_main2", "MainZone", "zone_main2", "p1", text="suite du corps"),
    ]
    root = etree.Element("TEI")

    build_body(root, lines, detect_lang=False)

    div = root.find(".//div")
    abs_ = [el for el in div if qlocal(el) == "ab"]
    types = [el.get("type") for el in abs_]
    assert types == ["MainZone", "StampZone", "CustomZone", "MainZone"], types
    # les deux lignes StampZone sont regroupees dans le meme <ab>
    stamp = abs_[1]
    assert [lb.tail for lb in stamp.findall("lb")] == ["cachet ligne 1", "cachet ligne 2"]
    # le texte Main n'a pas fui dans les <ab> de repli
    assert [lb.tail for lb in abs_[3].findall("lb")] == ["suite du corps"]


def test_build_body_unknown_line_type_in_mainzone_is_not_dropped():
    """
    Audit 1.1 (meme famille) : dans la branche Main, un type de ligne inconnu
    (ni DropCapital/Heading ni Default*) ne doit pas perdre le texte.
    """
    lines = [
        make_line("l1", "MainZone", "zone_a", "p1", text="ligne normale"),
        make_line("l2", "MainZone", "zone_a", "p1", text="ligne inconnue", line_type="InterlinearLine"),
    ]
    root = etree.Element("TEI")

    build_body(root, lines, detect_lang=False)

    ab = root.find(".//div/ab")
    assert [lb.tail for lb in ab.findall("lb")] == ["ligne normale", "ligne inconnue"]


def test_build_body_pb_and_lb_carry_n_and_facs_after_fix():
    """Audit 1.6 : @facs (canonique cote viewers) sur pb/lb, @n de la surface sur pb."""
    lines = [make_line("l1", "MainZone", "zone_a", "p1", text="texte", page_n="7")]
    root = etree.Element("TEI")

    build_body(root, lines, detect_lang=False)

    div = root.find(".//div")
    pb = div.find("pb")
    lb = div.find(".//lb")

    assert pb.get("n") == "7", "attendu : <pb n=...> reprenant le @n de la surface"
    assert pb.get("facs") == "#p1", "attendu : <pb facs='#p1'> en plus de @corresp"
    assert pb.get("corresp") == "#p1"
    assert lb.get("facs") == "#l1", "attendu : <lb facs='#l1'> en plus de @corresp"
    assert lb.get("corresp") == "#l1"


def test_build_body_pb_without_page_n_omits_n():
    """Surface sans @n : <pb> porte @facs/@corresp mais pas de @n invente."""
    lines = [make_line("l1", "MainZone", "zone_a", "p1", text="texte")]
    root = etree.Element("TEI")

    build_body(root, lines, detect_lang=False)

    pb = root.find(".//div/pb")
    assert pb.get("facs") == "#p1"
    assert pb.get("n") is None
