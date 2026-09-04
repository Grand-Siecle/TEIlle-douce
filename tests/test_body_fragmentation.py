"""
Tests cibles pour la fragmentation de phrases dans teille_douce/body/builder.py.

Perimetre EXCLUSIF : _walk_sentence_children, _parse_line_groups,
_append_tokens_with_foreign, _rebuild_with_modernization, ainsi que les
dataclasses _SentenceSegment / _LineGroup.

_rebuild_with_modernization contient (docs/rapport_audit.md §4.8) un bloc
duplique mot pour mot qui pose @part/@next/@prev, une fois dans la branche
"modernisee" (choice/orig, ~l.246-268) et une fois dans la branche "non
modernisee" (<s> direct sous le conteneur, ~l.283-304). Les assertions
portent sur le XML produit (jamais sur les variables internes de la
fonction), afin de rester stables une fois ce bloc extrait en
_set_fragment_links().
"""

from lxml import etree

from teille_douce.constants import NS_XML, XML_ID
from teille_douce.body.builder import (
    _SentenceSegment,
    _LineGroup,
    _walk_sentence_children,
    _parse_line_groups,
    _append_tokens_with_foreign,
    _rebuild_with_modernization,
)


XML_LANG = f"{{{NS_XML}}}lang"


def qlocal(el):
    """Nom local d'un element, namespace ou non (convention du depot)."""
    return etree.QName(el).localname


def _ids_in(container):
    """Ensemble de tous les xml:id presents dans le conteneur reconstruit."""
    return {el.get(XML_ID) for el in container.iter() if el.get(XML_ID)}


def _fragment_signature(container):
    """(xml:id, part, next, prev) de chaque fragment <s>, en ordre document.

    Peu importe que le <s> soit un enfant direct du conteneur ou niche
    dans <choice><orig> : container.iter() les trouve tous dans le meme
    ordre document, ce qui permet de comparer les deux chemins dupliques.
    """
    return [
        (el.get(XML_ID), el.get("part"), el.get("next"), el.get("prev"))
        for el in container.iter()
        if qlocal(el) == "s"
    ]


# ---------------------------------------------------------------------------
# 1. _parse_line_groups
# ---------------------------------------------------------------------------

def test_parse_line_groups_mono_line_sentence():
    container = etree.fromstring(
        '<ab><s xml:id="s1"><w>Bonjour</w><pc>.</pc></s></ab>'
    )
    groups = _parse_line_groups(container)

    assert len(groups) == 1
    group = groups[0]
    assert isinstance(group, _LineGroup)
    assert group.lb_corresp is None
    assert group.lb_element is None
    assert len(group.segments) == 1

    seg = group.segments[0]
    assert isinstance(seg, _SentenceSegment)
    assert seg.s_xml_id == "s1"
    assert seg.is_full is True
    assert [qlocal(t) for t in seg.tokens] == ["w", "pc"]
    assert seg.token_langs == [None, None]


def test_parse_line_groups_two_line_sentence():
    container = etree.fromstring(
        '<ab><lb corresp="l1"/>'
        '<s xml:id="s1"><w>Bonjour</w><lb corresp="l2"/><w>monde</w></s></ab>'
    )
    groups = _parse_line_groups(container)

    assert len(groups) == 2
    g0, g1 = groups
    assert g0.lb_corresp == "l1"
    assert g1.lb_corresp == "l2"
    assert qlocal(g1.lb_element) == "lb"

    assert len(g0.segments) == 1 and len(g1.segments) == 1
    seg0, seg1 = g0.segments[0], g1.segments[0]
    assert seg0.s_xml_id == seg1.s_xml_id == "s1"
    assert [t.text for t in seg0.tokens] == ["Bonjour"]
    assert [t.text for t in seg1.tokens] == ["monde"]
    # Phrase a cheval sur 2 lignes -> aucun des deux fragments n'est "complet"
    assert seg0.is_full is False
    assert seg1.is_full is False


def test_parse_line_groups_three_line_sentence():
    container = etree.fromstring(
        '<ab><lb corresp="l1"/>'
        '<s xml:id="s1"><w>A</w><lb corresp="l2"/><w>B</w>'
        '<lb corresp="l3"/><w>C</w></s></ab>'
    )
    groups = _parse_line_groups(container)

    assert len(groups) == 3
    assert [g.lb_corresp for g in groups] == ["l1", "l2", "l3"]
    assert [g.segments[0].tokens[0].text for g in groups] == ["A", "B", "C"]
    assert all(g.segments[0].is_full is False for g in groups)
    assert all(g.segments[0].s_xml_id == "s1" for g in groups)


def test_parse_line_groups_skips_non_element_nodes():
    # Un commentaire XML directement enfant du conteneur n'a pas de tag
    # str -> il doit etre ignore sans interrompre le decoupage en groupes.
    container = etree.fromstring(
        '<ab><!-- note --><s xml:id="s1"><w>Bonjour</w></s></ab>'
    )
    groups = _parse_line_groups(container)
    assert len(groups) == 1
    assert groups[0].segments[0].s_xml_id == "s1"


# ---------------------------------------------------------------------------
# 2. _walk_sentence_children
# ---------------------------------------------------------------------------

def test_walk_sentence_children_flattens_and_propagates_foreign_lang():
    s_elem = etree.fromstring(
        '<s xml:id="s1">'
        '<w>Bonjour</w>'
        '<hi rend="italic"><w>cher</w>'
        '<foreign xml:lang="la"><w>amicus</w><pc>.</pc></foreign></hi>'
        '<lb corresp="l2"/>'
        '<foreign xml:lang="grc"><w>ellen</w></foreign>'
        '</s>'
    )
    events = list(_walk_sentence_children(s_elem))

    kinds = [(e[0], qlocal(e[1]), e[2]) for e in events]
    assert kinds == [
        ("token", "w", None),      # Bonjour, hors <foreign>
        ("token", "w", None),      # cher, dans <hi> mais pas <foreign>
        ("token", "w", "la"),      # amicus, dans <foreign xml:lang="la">
        ("token", "pc", "la"),     # ".", meme <foreign>
        ("lb", "lb", None),        # <lb/> hors <foreign>
        ("token", "w", "grc"),     # ellen, dans <foreign xml:lang="grc">
    ]
    # Les <hi> sont traverses mais pas reifies dans les evenements.
    assert all(kind != "hi" for _, kind, _ in kinds)


def test_walk_sentence_children_skips_non_element_nodes():
    # Un noeud commentaire XML n'a pas de tag str -> il doit etre ignore
    # silencieusement, sans interrompre le parcours des tokens voisins.
    s_elem = etree.fromstring(
        '<s xml:id="s1"><w>a</w><!-- note interne --><w>b</w></s>'
    )
    events = list(_walk_sentence_children(s_elem))
    assert [t.text for kind, t, _ in events if kind == "token"] == ["a", "b"]


def test_walk_sentence_children_foreign_without_lang_inherits_ambient():
    s_elem = etree.fromstring(
        '<s xml:id="s1">'
        '<foreign xml:lang="la"><w>alpha</w>'
        '<foreign><w>beta</w></foreign></foreign>'
        '</s>'
    )
    events = list(_walk_sentence_children(s_elem))
    langs = [(t.text, lang) for kind, t, lang in events if kind == "token"]

    # Le <foreign> imbrique sans xml:lang propre herite de la langue ambiante.
    assert langs == [("alpha", "la"), ("beta", "la")]


# ---------------------------------------------------------------------------
# 7. _append_tokens_with_foreign
# ---------------------------------------------------------------------------

def _tok(tag, text):
    el = etree.Element(tag)
    el.text = text
    return el


def test_append_tokens_with_foreign_groups_consecutive_same_lang():
    parent = etree.Element("s")
    w1, w2, pc1, w3, w4 = (
        _tok("w", "Bonjour"),
        _tok("w", "amicus"),
        _tok("pc", "."),
        _tok("w", "ellen"),
        _tok("w", "pote"),
    )
    tokens = [w1, w2, pc1, w3, w4]
    langs = [None, "la", "la", "grc", "grc"]

    _append_tokens_with_foreign(parent, tokens, langs)

    assert len(parent) == 3
    assert qlocal(parent[0]) == "w" and parent[0] is w1

    foreign_la = parent[1]
    assert qlocal(foreign_la) == "foreign"
    assert foreign_la.get(XML_LANG) == "la"
    assert [c.text for c in foreign_la] == ["amicus", "."]

    foreign_grc = parent[2]
    assert qlocal(foreign_grc) == "foreign"
    assert foreign_grc.get(XML_LANG) == "grc"
    assert [c.text for c in foreign_grc] == ["ellen", "pote"]


def test_append_tokens_with_foreign_resets_grouping_after_none():
    parent = etree.Element("s")
    tokens = [_tok("w", "a"), _tok("w", "b"), _tok("w", "c"), _tok("w", "d")]
    langs = ["la", "la", None, "la"]

    _append_tokens_with_foreign(parent, tokens, langs)

    # Le token sans langue "casse" le regroupement : deux <foreign
    # xml:lang="la"> distincts sont crees, pas fusionnes.
    kinds = [qlocal(c) for c in parent]
    assert kinds == ["foreign", "w", "foreign"]
    assert [c.text for c in parent[0]] == ["a", "b"]
    assert parent[1].text == "c"
    assert [c.text for c in parent[2]] == ["d"]


# ---------------------------------------------------------------------------
# 3/4/5/6. _rebuild_with_modernization
# ---------------------------------------------------------------------------

TWO_LINE_XML = (
    '<ab><lb corresp="l1"/>'
    '<s xml:id="s1"><w>Bonjour</w><lb corresp="l2"/><w>monde</w><pc>.</pc></s>'
    '</ab>'
)

THREE_LINE_XML = (
    '<ab><lb corresp="l1"/>'
    '<s xml:id="s1"><w>A</w><lb corresp="l2"/><w>B</w>'
    '<lb corresp="l3"/><w>C</w></s></ab>'
)


def test_rebuild_keeps_facs_on_recreated_lb():
    """Audit 1.6 : le rebuild recree les <lb> en recopiant @facs avec @corresp."""
    xml = ('<ab><lb corresp="l1" facs="#zl1"/>'
           '<s xml:id="s1"><w>Bonjour</w></s></ab>')
    container = etree.fromstring(xml)
    groups = _parse_line_groups(container)

    _rebuild_with_modernization(container, groups, {"l1": "Bonjour reg"})

    lb = next(c for c in container if qlocal(c) == "lb")
    assert lb.get("corresp") == "l1"
    assert lb.get("facs") == "#zl1"


def test_rebuild_fragment_initial_and_final_two_lines():
    """2 lignes, toutes deux modernisees -> passe par le bloc l.246-268."""
    container = etree.fromstring(TWO_LINE_XML)
    groups = _parse_line_groups(container)
    corresp_to_mod = {"l1": "Bonjour reg", "l2": "monde. reg"}

    count = _rebuild_with_modernization(container, groups, corresp_to_mod)

    assert count == 2
    children = list(container)
    assert [qlocal(c) for c in children] == ["lb", "choice", "lb", "choice"]

    choice_i, choice_f = children[1], children[3]
    orig_i, orig_f = choice_i[0], choice_f[0]
    assert qlocal(orig_i) == "orig" and qlocal(orig_f) == "orig"
    s_i, s_f = orig_i[0], orig_f[0]
    # Le fragment vit bien sous <choice><orig>, pas directement sous le conteneur.
    assert s_i.getparent() is orig_i

    assert s_i.get(XML_ID) == "s1_0"
    assert s_i.get("part") == "I"
    assert s_i.get("next") == "#s1_1"
    assert s_i.get("prev") is None

    assert s_f.get(XML_ID) == "s1_1"
    assert s_f.get("part") == "F"
    assert s_f.get("prev") == "#s1_0"
    assert s_f.get("next") is None

    # Les references @next/@prev pointent vers des xml:id qui existent bien.
    ids = _ids_in(container)
    assert s_i.get("next")[1:] in ids
    assert s_f.get("prev")[1:] in ids

    assert choice_i[1].text == "Bonjour reg"
    assert choice_f[1].text == "monde. reg"


def test_rebuild_fragment_median_three_lines():
    """3 lignes, toutes modernisees -> le fragment M a @next ET @prev."""
    container = etree.fromstring(THREE_LINE_XML)
    groups = _parse_line_groups(container)
    corresp_to_mod = {"l1": "regA", "l2": "regB", "l3": "regC"}

    count = _rebuild_with_modernization(container, groups, corresp_to_mod)

    assert count == 3
    sig = _fragment_signature(container)
    assert sig == [
        ("s1_0", "I", "#s1_1", None),
        ("s1_1", "M", "#s1_2", "#s1_0"),
        ("s1_2", "F", None, "#s1_1"),
    ]
    ids = _ids_in(container)
    assert "s1_0" in ids and "s1_1" in ids and "s1_2" in ids


def test_rebuild_second_duplicated_block_matches_modernized_chaining():
    """Meme phrase sur 3 lignes, AUCUNE ligne modernisee -> passe par le
    bloc l.283-304 (fragments <s> directs sous le conteneur, pas de
    <choice>/<orig>). Le chainage @part/@next/@prev doit etre EXACTEMENT
    identique a celui produit par le bloc l.246-268 (test ci-dessus) :
    c'est ce test qui protege le futur refactor §4.8.
    """
    container = etree.fromstring(THREE_LINE_XML)
    groups = _parse_line_groups(container)

    count = _rebuild_with_modernization(container, groups, {})

    assert count == 0  # rien de modernise -> pas de <choice>
    children = list(container)
    assert [qlocal(c) for c in children] == ["lb", "s", "lb", "s", "lb", "s"]
    # Les fragments sont des enfants directs du conteneur, pas d'<orig>.
    for c in children:
        if qlocal(c) == "s":
            assert c.getparent() is container

    sig_nonmod = _fragment_signature(container)
    assert sig_nonmod == [
        ("s1_0", "I", "#s1_1", None),
        ("s1_1", "M", "#s1_2", "#s1_0"),
        ("s1_2", "F", None, "#s1_1"),
    ]

    # Reconstruction du meme cas via le chemin modernise (bloc l.246-268),
    # pour comparer les deux signatures de chainage terme a terme.
    container_mod = etree.fromstring(THREE_LINE_XML)
    groups_mod = _parse_line_groups(container_mod)
    _rebuild_with_modernization(
        container_mod, groups_mod, {"l1": "regA", "l2": "regB", "l3": "regC"}
    )
    sig_mod = _fragment_signature(container_mod)

    assert sig_nonmod == sig_mod


def test_rebuild_mono_line_sentence_no_fragmentation():
    container = etree.fromstring(
        '<ab type="marge"><s xml:id="s1"><w>Bonjour</w><pc>.</pc></s></ab>'
    )
    groups = _parse_line_groups(container)

    count = _rebuild_with_modernization(container, groups, {})

    assert count == 0
    # Les attributs du conteneur (ex. @type) survivent au clear/rebuild.
    assert container.get("type") == "marge"
    children = list(container)
    # Pas de <lb/> (aucune ligne explicite) et un seul <s>, non fragmente.
    assert [qlocal(c) for c in children] == ["s"]
    s_new = children[0]
    assert s_new.get(XML_ID) == "s1"
    assert s_new.get("part") is None
    assert s_new.get("next") is None
    assert s_new.get("prev") is None
    assert [c.text for c in s_new] == ["Bonjour", "."]
