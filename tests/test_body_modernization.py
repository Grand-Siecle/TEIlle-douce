# Characterization tests for the modernization entry points in
# src/body/builder.py: apply_modernization, apply_modernization_enriched,
# _wrap_plain_lines, _append_choice.
#
# Scope: entry points only. build_body, _apply_language_detection,
# _parse_line_groups, _walk_sentence_children, _rebuild_with_modernization,
# _append_tokens_with_foreign, _insert_foreign_inline, _build_offset_to_line,
# _splice_lb_tail and src/enrichment/reconstructor.py are covered by other
# test files written in parallel; not touched here.
#
# No network calls: the real modernization API runs on localhost:8011 and
# is not started for tests. Modernized strings are always passed in as
# parameters, never produced by calling the API.
#
# Fixtures are TEI containers built in memory via etree.fromstring on bare
# (non-namespaced) literal strings -- this matches how the pipeline actually
# builds the tree before serialization (docs/rapport_audit.md SS4.2: root is
# created with nsmap={None: NS_TEI} but etree.SubElement(root, "body") still
# produces a *bare* tag in memory; it only becomes namespaced after
# serialize+reparse). apply_modernization/_wrap_plain_lines rely on bare
# tag lookups (root.find(".//body"), c.tag == "s"), so fixtures must match.
#
# Run: venv/bin/python -m pytest tests/test_body_modernization.py -q
from unittest.mock import patch

import pytest
from lxml import etree

from src.body import builder
from src.body.builder import (
    apply_modernization,
    apply_modernization_enriched,
    _append_choice,
    _wrap_plain_lines,
)


def qlocal(el):
    """Local (namespace-stripped) tag name -- robust to the project's mix
    of bare and namespaced tags (audit SS4.2)."""
    return etree.QName(el).localname


# =============================================================================
# 1. apply_modernization -- choice only when the text actually differs,
#    alignment is strictly 1:1 by <lb> document-order index.
# =============================================================================

def test_apply_modernization_wraps_only_the_differing_line():
    xml = """<TEI><text><body>
    <ab><lb corresp="l1"/>Premiere ligne<lb corresp="l2"/>Seconde ligne</ab>
    </body></text></TEI>"""
    root = etree.fromstring(xml)

    count = apply_modernization(root, ["Premiere ligne", "Seconde ligne modernisee"])

    assert count == 1
    ab = root.find(".//ab")
    tags = [qlocal(c) for c in ab]
    assert tags == ["lb", "lb", "choice"]
    lb1, lb2, choice = list(ab)
    # Untouched line: tail preserved, no <choice> inserted after it.
    assert lb1.tail == "Premiere ligne"
    # Modernized line: tail cleared, <choice> spliced in right after the <lb>.
    assert lb2.tail is None
    orig, reg = list(choice)
    assert qlocal(orig) == "orig" and orig.text == "Seconde ligne"
    assert qlocal(reg) == "reg" and reg.text == "Seconde ligne modernisee"
    assert reg.get("type") == "modernized"


def test_apply_modernization_aligns_strictly_by_lb_index():
    # Only the middle line differs -- proves alignment is per-index, not
    # "wrap everything from the first difference onward".
    xml = """<TEI><text><body>
    <ab><lb corresp="l1"/>Un<lb corresp="l2"/>Deux<lb corresp="l3"/>Trois</ab>
    </body></text></TEI>"""
    root = etree.fromstring(xml)

    count = apply_modernization(root, ["Un", "Deux modernise", "Trois"])

    assert count == 1
    ab = root.find(".//ab")
    tags = [qlocal(c) for c in ab]
    assert tags == ["lb", "lb", "choice", "lb"]
    lb1, lb2, choice, lb3 = list(ab)
    assert lb1.tail == "Un"
    assert lb3.tail == "Trois"
    orig, reg = list(choice)
    assert orig.text == "Deux"
    assert reg.text == "Deux modernise"


def test_apply_modernization_identical_text_produces_no_choice():
    xml = """<TEI><text><body>
    <ab><lb corresp="l1"/>Premiere ligne<lb corresp="l2"/>Seconde ligne</ab>
    </body></text></TEI>"""
    root = etree.fromstring(xml)

    count = apply_modernization(root, ["Premiere ligne", "Seconde ligne"])

    assert count == 0
    ab = root.find(".//ab")
    assert [qlocal(c) for c in ab] == ["lb", "lb"]


# =============================================================================
# 2. apply_modernization -- edge cases: short/long lists, None, "".
# =============================================================================

def test_apply_modernization_shorter_list_leaves_trailing_lbs_untouched():
    xml = """<TEI><text><body>
    <ab><lb corresp="l1"/>Un<lb corresp="l2"/>Deux<lb corresp="l3"/>Trois</ab>
    </body></text></TEI>"""
    root = etree.fromstring(xml)

    # Only 2 entries for 3 <lb>: the loop must break, not crash or wrap
    # past the end of the list.
    count = apply_modernization(root, ["Un", "Deux modernise"])

    assert count == 1
    ab = root.find(".//ab")
    assert [qlocal(c) for c in ab] == ["lb", "lb", "choice", "lb"]
    lb3 = ab[-1]
    assert lb3.tail == "Trois"


def test_apply_modernization_longer_list_ignores_extra_entries():
    xml = """<TEI><text><body>
    <ab><lb corresp="l1"/>Seule ligne</ab>
    </body></text></TEI>"""
    root = etree.fromstring(xml)

    count = apply_modernization(root, ["Seule ligne modifiee", "extra1", "extra2"])

    assert count == 1
    ab = root.find(".//ab")
    assert [qlocal(c) for c in ab] == ["lb", "choice"]


def test_apply_modernization_none_entry_is_skipped():
    xml = """<TEI><text><body>
    <ab><lb corresp="l1"/>Un<lb corresp="l2"/>Deux</ab>
    </body></text></TEI>"""
    root = etree.fromstring(xml)

    count = apply_modernization(root, [None, "Deux modernise"])

    assert count == 1
    ab = root.find(".//ab")
    assert [qlocal(c) for c in ab] == ["lb", "lb", "choice"]
    assert ab[0].tail == "Un"


def test_apply_modernization_empty_string_entry_is_skipped():
    xml = """<TEI><text><body>
    <ab><lb corresp="l1"/>Un<lb corresp="l2"/>Deux</ab>
    </body></text></TEI>"""
    root = etree.fromstring(xml)

    count = apply_modernization(root, ["", "Deux modernise"])

    assert count == 1
    ab = root.find(".//ab")
    assert [qlocal(c) for c in ab] == ["lb", "lb", "choice"]
    assert ab[0].tail == "Un"


def test_apply_modernization_empty_modernized_list_no_crash():
    xml = """<TEI><text><body>
    <ab><lb corresp="l1"/>Un<lb corresp="l2"/>Deux</ab>
    </body></text></TEI>"""
    root = etree.fromstring(xml)

    count = apply_modernization(root, [])

    assert count == 0
    ab = root.find(".//ab")
    assert [qlocal(c) for c in ab] == ["lb", "lb"]


def test_apply_modernization_no_body_returns_zero():
    root = etree.fromstring("<TEI><text></text></TEI>")

    assert apply_modernization(root, ["whatever"]) == 0


# =============================================================================
# 3. _append_choice -- exact structure produced, effect on preexisting text.
# =============================================================================

def test_append_choice_structure_and_preexisting_text():
    parent = etree.fromstring("<ab>Avant<lb/>Milieu</ab>")
    lb = parent.find("lb")

    choice = _append_choice(parent, "texte original", "texte modernise")

    assert qlocal(choice) == "choice"
    children = list(choice)
    assert [qlocal(c) for c in children] == ["orig", "reg"]
    orig, reg = children
    assert orig.text == "texte original"
    assert reg.text == "texte modernise"
    assert reg.get("type") == "modernized"

    # _append_choice only appends -- it does not touch existing text/tails,
    # and does not reposition itself (callers like apply_modernization use
    # lb.addnext(choice) afterward to splice it into place).
    assert parent.text == "Avant"
    assert lb.tail == "Milieu"
    assert list(parent)[-1] is choice
    assert choice.tail is None


def test_append_choice_returns_the_created_element():
    parent = etree.fromstring("<ab/>")
    choice = _append_choice(parent, "a", "b")
    assert choice is parent[-1]
    assert choice.getparent() is parent


# =============================================================================
# 4. apply_modernization_enriched -- routing only (not the detailed result
#    of _rebuild_with_modernization, which another agent's suite covers).
# =============================================================================

def test_apply_modernization_enriched_routes_by_container_shape():
    xml = """<TEI><text><body>
    <ab><s xml:id="s1"><lb corresp="l1"/><w>Mot</w></s></ab>
    <ab><lb corresp="l2"/>Texte brut</ab>
    </body></text></TEI>"""
    root = etree.fromstring(xml)
    corresp_to_mod = {"l1": "Mot modernise", "l2": "Texte brut modernise"}

    enriched_ab, plain_ab = root.findall(".//ab")

    with patch.object(builder, "_rebuild_with_modernization", return_value=0) as mock_rebuild, \
         patch.object(builder, "_wrap_plain_lines", return_value=0) as mock_wrap:
        apply_modernization_enriched(root, corresp_to_mod)

        # Enriched container (has an <s> child) is routed to
        # _rebuild_with_modernization, never to _wrap_plain_lines.
        mock_rebuild.assert_called_once()
        assert mock_rebuild.call_args[0][0] is enriched_ab

        # Non-enriched container (no <s> child) falls back to
        # _wrap_plain_lines, never to _rebuild_with_modernization.
        mock_wrap.assert_called_once()
        assert mock_wrap.call_args[0][0] is plain_ab


def test_apply_modernization_enriched_skips_enriched_container_without_mod():
    # Enriched container whose only <lb corresp> has no entry in the
    # mapping: has_mod is False, so neither routing function should run
    # at all for it (unlike the plain-container branch, which always
    # calls _wrap_plain_lines regardless of whether anything matches).
    xml = """<TEI><text><body>
    <ab><s xml:id="s1"><lb corresp="l1"/><w>Mot</w></s></ab>
    </body></text></TEI>"""
    root = etree.fromstring(xml)

    with patch.object(builder, "_rebuild_with_modernization", return_value=0) as mock_rebuild, \
         patch.object(builder, "_wrap_plain_lines", return_value=0) as mock_wrap:
        count = apply_modernization_enriched(root, {})

    assert count == 0
    mock_rebuild.assert_not_called()
    mock_wrap.assert_not_called()


def test_apply_modernization_enriched_no_body_returns_zero():
    root = etree.fromstring("<TEI><text></text></TEI>")
    assert apply_modernization_enriched(root, {"l1": "x"}) == 0


# =============================================================================
# 5. _wrap_plain_lines -- <lb> tail wrapping keyed by @corresp.
# =============================================================================

def test_wrap_plain_lines_matches_by_corresp_and_leaves_others_intact():
    xml = (
        "<ab>"
        '<lb corresp="l1"/>Meme texte'
        '<lb corresp="l2"/>Texte different'
        '<lb corresp="l3"/>Texte sans entree dans le mapping'
        "<lb/>Texte sans attribut corresp"
        "</ab>"
    )
    container = etree.fromstring(xml)
    mapping = {"l1": "Meme texte", "l2": "Texte different modernise"}

    count = _wrap_plain_lines(container, mapping)

    assert count == 1
    tags = [qlocal(c) for c in container]
    assert tags == ["lb", "lb", "choice", "lb", "lb"]

    lbs = [c for c in container if qlocal(c) == "lb"]
    lb1, lb2, lb3, lb4 = lbs
    # l1: modernized text equals original -> left alone, no <choice>.
    assert lb1.tail == "Meme texte"
    # l2: modernized text differs -> wrapped, tail cleared.
    assert lb2.tail is None
    # l3: has a @corresp but no entry in the mapping -> left intact.
    assert lb3.tail == "Texte sans entree dans le mapping"
    # no @corresp at all -> left intact.
    assert lb4.tail == "Texte sans attribut corresp"

    choice = container[2]
    orig, reg = list(choice)
    assert orig.text == "Texte different"
    assert reg.text == "Texte different modernise"


def test_wrap_plain_lines_no_matching_lb_returns_zero():
    container = etree.fromstring('<ab><lb corresp="l1"/>Texte</ab>')
    assert _wrap_plain_lines(container, {}) == 0
    assert container[0].tail == "Texte"


# =============================================================================
# Audit SS1.12 : une lecture modernisee est attribuee — @resp (elle est
# automatique) et @cert (le score de similarite, deja calcule en amont
# pour rejeter les hallucinations, dit si c'est une simple normalisation
# orthographique ou une reecriture lourde).
# =============================================================================

def test_apply_modernization_reg_carries_resp_and_cert():
    xml = """<TEI><text><body>
    <ab><lb corresp="l1"/>Texte original</ab>
    </body></text></TEI>"""
    root = etree.fromstring(xml)

    apply_modernization(root, ["Texte modifie"])

    reg = root.find(".//reg")
    assert reg is not None
    assert reg.get("resp") is not None
    assert reg.get("cert") is not None


def test_reg_cert_grades_with_the_similarity_of_the_reading():
    """Audit 1.12 : @cert distingue une normalisation orthographique
    legere d'une reecriture lourde — le score existait deja, il servait
    seulement a rejeter les hallucinations. Les seuils sont cales sur la
    distribution reelle du corpus (similarite 0.81-1.00, mediane 0.96),
    donc sur des lignes entieres, pas sur quelques mots."""
    def cert_pour(original, modernise):
        root = etree.fromstring(
            f'<TEI><text><body><ab><lb corresp="l1"/>{original}</ab></body></text></TEI>'
        )
        apply_modernization(root, [modernise])
        return root.find(".//reg").get("cert")

    ligne = ("Des raisons qui nous obligent a sanctifier le jour du Seigneur "
             "et a le passer en oeuvres de pieté")
    # une graphie modernisee sur une ligne entiere : lecture sure
    assert cert_pour(ligne + " estoit", ligne + " était") == "high"
    # la moitie de la ligne reecrite : lecture a prendre avec precaution
    assert cert_pour(ligne, "Des raisons obscures parfaitement etrangeres au propos") == "low"

    # et la gradation est monotone : plus la lecture s'ecarte, moins on
    # l'affirme
    ordre = ["high", "medium", "low"]
    proches = cert_pour(ligne + " estoit", ligne + " était")
    lointaines = cert_pour(ligne, "Des raisons obscures parfaitement etrangeres au propos")
    assert ordre.index(proches) < ordre.index(lointaines)


def test_modernization_responsibility_is_declared_and_idempotent():
    """Le @resp des lectures doit resoudre : le respStmt est declare dans
    le header, et un second passage ne le duplique pas."""
    from src.tei import declare_modernization_responsibility
    from src.constants import XML_ID

    root = etree.fromstring(
        '<TEI><teiHeader><fileDesc><titleStmt><title>T</title></titleStmt>'
        "</fileDesc></teiHeader></TEI>"
    )
    declare_modernization_responsibility(root)
    declare_modernization_responsibility(root)

    resp = [e for e in root.iter() if e.get(XML_ID) == "modernize-auto"]
    assert len(resp) == 1, f"{len(resp)} respStmt modernize-auto"
    assert qlocal(resp[0]) == "respStmt"
