# -----------------------------------------------------------
# Tests for src/enrichment/reconstructor.py (Phase 6): rebuilds a TEI
# container's children as <s>/<w>/<pc>/<lb>/<hi>/<foreign> from the
# sentences produced by the segmenter, out of aligned NLP tokens.
# Run: venv/bin/python -m pytest tests/test_reconstructor.py -q
# -----------------------------------------------------------
import re

import pytest
from lxml import etree

from src.constants import NS_TEI, NS_XML, XML_ID
from src.enrichment.reconstructor import (
    rebuild_container,
    _insert_lb,
    _create_w,
    _create_pc,
    _create_cross_line_w,
)
from src.enrichment.client import NLPToken
from src.enrichment.extractor import TextSpan
from src.enrichment.aligner import AlignedToken
from src.enrichment.segmenter import Sentence

# xml:lang key, mirrors the module-level constant private to reconstructor.py
XML_LANG = f"{{{NS_XML}}}lang"


# =============================================================================
# Helpers (docs/rapport_audit.md 4.2 : tags nus et namespaces coexistent)
# =============================================================================

def qlocal(el):
    """Nom local du tag, quel que soit son namespace (ou son absence)."""
    return etree.QName(el).localname


def texte_total(el):
    """Concatene tout le contenu textuel d'un element (text + tails, recursif)."""
    return "".join(el.itertext())


def mk_token(form, lemma="", pos="", morph="", treated=None, is_punct=False, origin_lang=""):
    """Build a real NLPToken. treated=None means 'unchanged form' (no @norm)."""
    return NLPToken(
        form=form,
        lemma=lemma,
        pos=pos,
        morph=morph,
        treated=form if treated is None else treated,
        is_punctuation=is_punct,
        origin_lang=origin_lang,
    )


def mk_span(line_index=0, lb_element=None, lb_corresp=None, hi_element=None,
            hi_rend=None, has_hyphen=False, lang=None, foreign_element=None, text=""):
    """Build a real TextSpan."""
    return TextSpan(
        text=text,
        offset_start=0,
        offset_end=len(text),
        lb_element=lb_element,
        lb_corresp=lb_corresp,
        hi_element=hi_element,
        hi_rend=hi_rend,
        line_index=line_index,
        has_hyphen=has_hyphen,
        lang=lang,
        foreign_element=foreign_element,
    )


def mk_aligned(token, spans=None, lb_elements=None, hi_element=None,
               is_cross_line=False, original_parts=None):
    """Build a real AlignedToken."""
    return AlignedToken(
        token=token,
        spans=[mk_span(line_index=0)] if spans is None else spans,
        lb_elements=[] if lb_elements is None else lb_elements,
        hi_element=hi_element,
        is_cross_line=is_cross_line,
        original_parts=[] if original_parts is None else original_parts,
    )


def mk_sentence(xml_id, tokens, next_id=None, prev_id=None):
    """Build a real Sentence."""
    return Sentence(xml_id=xml_id, tokens=tokens, next_id=next_id, prev_id=prev_id)


def mk_container(xml_id="ab1", lang=None, extra_attrs="", body="ancien texte"):
    """An in-memory TEI-namespaced container (<ab>), never a fixture file."""
    attrs = f' xml:id="{xml_id}"'
    if lang:
        attrs += f' xml:lang="{lang}"'
    if extra_attrs:
        attrs += f" {extra_attrs}"
    xml = f'<ab xmlns="{NS_TEI}"{attrs}>{body}</ab>'
    return etree.fromstring(xml)


# =============================================================================
# 1. rebuild_container nominal
# =============================================================================

def test_rebuild_container_nominal_word_order_and_attributes():
    t_le = mk_token("Le", lemma="le", pos="DET", morph="Definite=Def")
    t_chien = mk_token("chien", lemma="chien", pos="NOUN", morph="Gender=Masc|Number=Sing")
    # old spelling normalized: treated differs from form -> @norm expected
    t_estoit = mk_token("estoit", lemma="etre", pos="VERB", morph="Mood=Ind|Tense=Pres", treated="etait")
    t_dot = mk_token(".", is_punct=True)

    sent1 = mk_sentence(
        "s1",
        [mk_aligned(t_le), mk_aligned(t_chien), mk_aligned(t_estoit), mk_aligned(t_dot)],
        next_id="s2", prev_id="s0",
    )
    # a second, trivial sentence -> the sentence loop must repeat correctly
    sent2 = mk_sentence("s2", [mk_aligned(mk_token("Fin", pos="NOUN"))], prev_id="s1")

    container = mk_container()
    rebuild_container(container, [sent1, sent2], spans=[], primary_lang="fra")

    s_elements = list(container)
    assert [qlocal(e) for e in s_elements] == ["s", "s"]

    s1 = s_elements[0]
    assert s1.get(XML_ID) == "s1"
    assert s1.get("next") == "s2"
    assert s1.get("prev") == "s0"

    children = list(s1)
    assert [qlocal(c) for c in children] == ["w", "w", "w", "pc"]

    w_le, w_chien, w_estoit, pc_dot = children
    assert w_le.text == "Le"
    assert w_le.get("lemma") == "le"
    assert w_le.get("pos") == "DET"
    assert w_le.get("msd") == "Definite=Def"
    assert w_le.get("norm") is None  # treated == form -> no @norm

    assert w_estoit.text == "estoit"
    assert w_estoit.get("norm") == "etait"

    assert pc_dot.text == "."
    assert pc_dot.get("join") == "left"

    # invariant: reconstructed text == concatenation of token forms, no loss/dup
    assert texte_total(s1) == "Le" + "chien" + "estoit" + "."

    s2 = s_elements[1]
    assert s2.get(XML_ID) == "s2"
    assert s2.get("prev") == "s1"
    assert s2.get("next") is None
    assert texte_total(s2) == "Fin"


def test_rebuild_container_primary_lang_defaults_from_container_attribute():
    # primary_lang=None -> falls back to container's own xml:lang
    container = mk_container(lang="fra")
    sent = mk_sentence("s1", [mk_aligned(mk_token("mot", origin_lang="lat"))])

    rebuild_container(container, [sent], spans=[], primary_lang=None)

    s = container[0]
    w_or_foreign = list(s)
    assert [qlocal(c) for c in w_or_foreign] == ["foreign"]
    assert w_or_foreign[0].get(XML_LANG) == "lat"


def test_rebuild_container_spans_parameter_is_dead():
    """
    Surprising finding: the *positional* `spans` argument of
    rebuild_container is never read inside the function body (only
    at.spans, the per-token attribute, is used). Passing anything for it
    -- None, [], or garbage -- must not change the output.
    """
    sent = mk_sentence("s1", [mk_aligned(mk_token("mot"))])

    c1 = mk_container()
    rebuild_container(c1, [sent], spans=None, primary_lang="fra")

    c2 = mk_container()
    rebuild_container(c2, [sent], spans=["not", "a", "textspan", 42], primary_lang="fra")

    assert etree.tostring(c1) == etree.tostring(c2)


# =============================================================================
# 2. _create_w / _create_pc
# =============================================================================

def test_create_w_omits_empty_attributes_and_keeps_text():
    parent = etree.Element("s")
    at = mk_aligned(mk_token("mot"))  # no lemma/pos/morph, treated == form
    _create_w(parent, at)

    w = parent[0]
    assert qlocal(w) == "w"
    assert w.text == "mot"
    assert w.attrib == {}  # not even an empty lemma/pos/msd/norm


def test_create_w_sets_all_attributes_when_present():
    parent = etree.Element("s")
    at = mk_aligned(mk_token("estoit", lemma="etre", pos="VERB", morph="Tense=Past", treated="etait"))
    _create_w(parent, at)

    w = parent[0]
    assert w.text == "estoit"
    assert w.get("lemma") == "etre"
    assert w.get("pos") == "VERB"
    assert w.get("msd") == "Tense=Past"
    assert w.get("norm") == "etait"
    assert w.get(XML_ID) is None  # only cross-line fragments get an xml:id


def test_create_pc_join_left_right_and_neither():
    parent = etree.Element("s")
    at_comma = mk_aligned(mk_token(",", is_punct=True))
    at_paren = mk_aligned(mk_token("(", is_punct=True))
    at_dash = mk_aligned(mk_token("-", is_punct=True))

    _create_pc(parent, at_comma)
    _create_pc(parent, at_paren)
    _create_pc(parent, at_dash)

    pc_comma, pc_paren, pc_dash = list(parent)
    assert pc_comma.text == "," and pc_comma.get("join") == "left"
    assert pc_paren.text == "(" and pc_paren.get("join") == "right"
    assert pc_dash.text == "-" and "join" not in pc_dash.attrib


# =============================================================================
# 3. rebuild_container + <lb>
# =============================================================================

def test_rebuild_container_inserts_lb_at_each_line_change_in_place():
    lb1 = etree.Element("lb")
    lb2 = etree.Element("lb")

    at_il = mk_aligned(mk_token("Il"), spans=[mk_span(line_index=0)])
    at_vient = mk_aligned(
        mk_token("vient"),
        spans=[mk_span(line_index=1, lb_element=lb1, lb_corresp="#zoneLine_2")],
    )
    at_dot1 = mk_aligned(mk_token(".", is_punct=True), spans=[mk_span(line_index=1)])
    at_encore = mk_aligned(
        mk_token("encore"),
        spans=[mk_span(line_index=2, lb_element=lb2, lb_corresp="#zoneLine_3")],
    )
    at_dot2 = mk_aligned(mk_token(".", is_punct=True), spans=[mk_span(line_index=2)])

    sent = mk_sentence("s1", [at_il, at_vient, at_dot1, at_encore, at_dot2])
    container = mk_container()
    rebuild_container(container, [sent], spans=[], primary_lang="fra")

    s = container[0]
    tags = [qlocal(c) for c in s]
    assert tags == ["w", "lb", "w", "pc", "lb", "w", "pc"]

    lbs = [c for c in s if qlocal(c) == "lb"]
    assert lbs[0].get("corresp") == "#zoneLine_2"
    assert lbs[1].get("corresp") == "#zoneLine_3"

    # the <lb/> carries no text of its own -> reconstructed text is exactly
    # the concatenation of the token forms
    assert texte_total(s) == "Il" + "vient" + "." + "encore" + "."


def test_rebuild_container_same_lb_never_reinserted():
    # two tokens both claiming the *same* line boundary (line_index unchanged
    # after the first insertion) must not produce a second <lb/>
    lb1 = etree.Element("lb")
    at_a = mk_aligned(
        mk_token("premier"),
        spans=[mk_span(line_index=1, lb_element=lb1, lb_corresp="#zoneLine_2")],
    )
    at_b = mk_aligned(
        mk_token("second"),
        spans=[mk_span(line_index=1, lb_element=lb1, lb_corresp="#zoneLine_2")],
    )
    sent = mk_sentence("s1", [at_a, at_b])
    container = mk_container()
    rebuild_container(container, [sent], spans=[], primary_lang="fra")

    s = container[0]
    assert [qlocal(c) for c in s].count("lb") == 1


def test_insert_lb_directly_sets_corresp_and_tracks_inserted_id():
    lb_orig = etree.Element("lb")
    span = mk_span(lb_element=lb_orig, lb_corresp="#zoneLine_9")
    parent = etree.Element("s")
    inserted = set()

    _insert_lb(parent, span, "unused-aligned-token-arg", inserted)

    assert qlocal(parent[0]) == "lb"
    assert parent[0].get("corresp") == "#zoneLine_9"
    assert id(lb_orig) in inserted


# =============================================================================
# 4. _create_cross_line_w
# =============================================================================

def test_create_cross_line_w_two_fragments_part_and_links():
    lb1 = etree.Element("lb")
    lb1.set("corresp", "#zoneLine_5")

    token = mk_token(
        "protection", lemma="protection", pos="NOUN",
        morph="Case=Nom|Number=Sing", treated="Protexion",
    )
    at = mk_aligned(
        token,
        spans=[mk_span(line_index=0), mk_span(line_index=1)],
        lb_elements=[lb1],
        is_cross_line=True,
        original_parts=["Pro", "tection"],
    )
    parent = etree.Element("s")
    inserted = set()

    _create_cross_line_w(parent, at, inserted)

    tags = [qlocal(c) for c in parent]
    assert tags == ["w", "lb", "w"]

    w0, lb, w1 = list(parent)
    assert w0.text == "Pro"
    assert w1.text == "tection"

    id0, id1 = w0.get(XML_ID), w1.get(XML_ID)
    assert id0 and id1 and id0 != id1
    assert re.match(r"^w_[0-9a-f]{12}_0$", id0)
    assert re.match(r"^w_[0-9a-f]{12}_1$", id1)

    assert w0.get("part") == "I"
    assert w1.get("part") == "F"
    assert w0.get("next") == f"#{id1}"
    assert w0.get("prev") is None
    assert w1.get("prev") == f"#{id0}"
    assert w1.get("next") is None

    # shared attrs copied to both fragments
    assert w0.get("lemma") == "protection" == w1.get("lemma")
    assert w0.get("pos") == "NOUN" == w1.get("pos")
    assert w0.get("msd") == "Case=Nom|Number=Sing" == w1.get("msd")

    # @norm reflects the normalized full word, only on the initial fragment
    assert w0.get("norm") == "Protexion"
    assert w1.get("norm") is None

    assert lb.get("corresp") == "#zoneLine_5"
    assert id(lb1) in inserted

    # invariant: no text lost/duplicated across the split
    assert texte_total(parent) == "".join(at.original_parts) == "Protection"


def test_create_cross_line_w_three_fragments_middle_part_is_M():
    lb1, lb2 = etree.Element("lb"), etree.Element("lb")
    token = mk_token("redaction", treated="redaction")
    at = mk_aligned(
        token,
        spans=[mk_span(line_index=i) for i in range(3)],
        lb_elements=[lb1, lb2],
        is_cross_line=True,
        original_parts=["re", "dac", "tion"],
    )
    parent = etree.Element("s")
    _create_cross_line_w(parent, at, set())

    ws = [c for c in parent if qlocal(c) == "w"]
    assert [w.get("part") for w in ws] == ["I", "M", "F"]

    id0, id1, id2 = (w.get(XML_ID) for w in ws)
    assert ws[0].get("next") == f"#{id1}" and ws[0].get("prev") is None
    assert ws[1].get("prev") == f"#{id0}" and ws[1].get("next") == f"#{id2}"
    assert ws[2].get("prev") == f"#{id1}" and ws[2].get("next") is None

    # @norm only ever set on the initial (I) fragment
    assert ws[0].get("norm") is None  # treated == form here -> none anyway
    assert [qlocal(c) for c in parent].count("lb") == 2
    assert texte_total(parent) == "re" + "dac" + "tion"


def test_create_cross_line_w_fewer_than_two_parts_falls_back_to_create_w():
    # original_parts=[] (falsy) -> parts = [token.form], len < 2 -> fallback
    token = mk_token("mot", lemma="mot", pos="NOUN")
    at = mk_aligned(token, is_cross_line=True, original_parts=[])
    parent = etree.Element("s")

    _create_cross_line_w(parent, at, set())

    assert [qlocal(c) for c in parent] == ["w"]
    w = parent[0]
    assert w.text == "mot"
    assert w.get(XML_ID) is None  # went through _create_w, not fragmented
    assert w.get("part") is None


def test_create_cross_line_w_lb_count_follows_lb_elements_not_parts():
    """
    Documents real (unvalidated) behaviour: the number of <lb/> emitted is
    driven by len(lb_elements), not len(parts) - 1. With 3 parts but only 1
    lb_element supplied (a malformed/mismatched aligner output), only one
    <lb/> is produced even though there are two line boundaries.
    """
    lb1 = etree.Element("lb")
    token = mk_token("redaction")
    at = mk_aligned(
        token,
        spans=[mk_span(line_index=i) for i in range(3)],
        lb_elements=[lb1],  # only 1, though parts implies 2 boundaries
        is_cross_line=True,
        original_parts=["re", "dac", "tion"],
    )
    parent = etree.Element("s")
    _create_cross_line_w(parent, at, set())

    assert [qlocal(c) for c in parent].count("lb") == 1
    # part attributes are still correct for all 3 fragments regardless
    ws = [c for c in parent if qlocal(c) == "w"]
    assert [w.get("part") for w in ws] == ["I", "M", "F"]


def test_rebuild_container_dispatches_cross_line_tokens_to_fragmenter():
    """
    rebuild_container itself (not just _create_cross_line_w in isolation)
    must route an is_cross_line token through the fragmenting path, insert
    the <lb/> between the fragments, and keep tracking the line index from
    the token's last span afterwards.
    """
    lb1 = etree.Element("lb")
    lb1.set("corresp", "#zoneLine_2")

    at_pro = mk_aligned(
        mk_token("protection", lemma="protection", pos="NOUN"),
        spans=[mk_span(line_index=0), mk_span(line_index=1)],
        lb_elements=[lb1],
        is_cross_line=True,
        original_parts=["Pro", "tection"],
    )
    # a following token on the same (second) line: since prev_line_index was
    # updated from the cross-line token's *last* span, no further <lb/> for it
    at_civile = mk_aligned(mk_token("civile", pos="ADJ"), spans=[mk_span(line_index=1)])

    sent = mk_sentence("s1", [at_pro, at_civile])
    container = mk_container()
    rebuild_container(container, [sent], spans=[], primary_lang="fra")

    s = container[0]
    tags = [qlocal(c) for c in s]
    assert tags == ["w", "lb", "w", "w"], tags
    assert tags.count("lb") == 1

    assert texte_total(s) == "Pro" + "tection" + "civile"


# =============================================================================
# 5. rebuild_container + <hi>
# =============================================================================

def test_rebuild_container_hi_groups_consecutive_tokens_same_hi_element():
    hi1 = etree.Element("hi")  # stand-in for the original <hi rend="italic">
    hi2 = etree.Element("hi")  # a *different* hi run, same rend value

    at_beau = mk_aligned(
        mk_token("beau", pos="ADJ"),
        hi_element=hi1, spans=[mk_span(hi_element=hi1, hi_rend="italic")],
    )
    at_temps = mk_aligned(
        mk_token("temps", pos="NOUN"),
        hi_element=hi1, spans=[mk_span(hi_element=hi1, hi_rend="italic")],
    )
    at_dot = mk_aligned(mk_token(".", is_punct=True))
    at_clair = mk_aligned(
        mk_token("clair", pos="ADJ"),
        hi_element=hi2, spans=[mk_span(hi_element=hi2, hi_rend="italic")],
    )

    sent = mk_sentence("s1", [at_beau, at_temps, at_dot, at_clair])
    container = mk_container()
    rebuild_container(container, [sent], spans=[], primary_lang="fra")

    s = container[0]
    top_tags = [qlocal(c) for c in s]
    assert top_tags == ["hi", "pc", "hi"], top_tags

    hi_group1, pc, hi_group2 = list(s)
    assert hi_group1.get("rend") == "italic"
    assert [qlocal(c) for c in hi_group1] == ["w", "w"]
    assert [c.text for c in hi_group1] == ["beau", "temps"]

    # same @rend value, but a *different* original <hi> -> not merged
    assert hi_group1 is not hi_group2
    assert hi_group2.get("rend") == "italic"
    assert [c.text for c in hi_group2] == ["clair"]

    assert texte_total(s) == "beau" + "temps" + "." + "clair"


# =============================================================================
# 6. rebuild_container + <foreign>
# =============================================================================

def test_rebuild_container_foreign_wraps_only_non_primary_lang_runs():
    at_ainsi = mk_aligned(mk_token("Ainsi", origin_lang=""))     # unset -> primary
    at_ergo = mk_aligned(mk_token("ergo", origin_lang="lat"))    # foreign run start
    at_sum = mk_aligned(mk_token("sum", origin_lang="lat"))      # same run -> merged
    at_dit = mk_aligned(mk_token("dit", origin_lang="fra"))      # back to primary

    sent = mk_sentence("s1", [at_ainsi, at_ergo, at_sum, at_dit])
    container = mk_container()
    rebuild_container(container, [sent], spans=[], primary_lang="fra")

    s = container[0]
    top_tags = [qlocal(c) for c in s]
    assert top_tags == ["w", "foreign", "w"], top_tags

    w_ainsi, foreign, w_dit = list(s)
    assert w_ainsi.text == "Ainsi"
    assert w_ainsi.getparent() is s
    assert w_dit.text == "dit"
    assert w_dit.getparent() is s

    assert foreign.get(XML_LANG) == "lat"
    assert [qlocal(c) for c in foreign] == ["w", "w"]
    assert [c.text for c in foreign] == ["ergo", "sum"]


# =============================================================================
# 7. rebuild_container degraded inputs
# =============================================================================

def test_rebuild_container_empty_sentence_list_clears_the_container():
    container = mk_container(extra_attrs='type="marginal"', body="<w>reste</w>")
    rebuild_container(container, [], spans=[], primary_lang="fra")

    # real, observed behaviour: an empty sentence list empties the container
    # (text and all children removed), it is not left intact.
    assert container.text is None
    assert list(container) == []
    # but the container's own attributes survive the clear/restore cycle
    assert container.get(XML_ID) == "ab1"
    assert container.get("type") == "marginal"


def test_rebuild_container_sentence_without_tokens_yields_empty_s():
    sent = mk_sentence("s1", [])
    container = mk_container()
    rebuild_container(container, [sent], spans=[], primary_lang="fra")

    s = container[0]
    assert qlocal(s) == "s"
    assert s.get(XML_ID) == "s1"
    assert list(s) == []
    assert texte_total(s) == ""


def test_rebuild_container_token_with_empty_form_produces_no_stray_text():
    at_empty = mk_aligned(mk_token(""))
    at_word = mk_aligned(mk_token("mot"))
    sent = mk_sentence("s1", [at_empty, at_word])
    container = mk_container()
    rebuild_container(container, [sent], spans=[], primary_lang="fra")

    s = container[0]
    w_empty, w_mot = list(s)
    assert w_empty.text == ""
    assert w_mot.text == "mot"
    assert texte_total(s) == "" + "mot"
