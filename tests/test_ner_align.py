"""
Tests cibles pour src/enrichment/ner_align.py (Phase 8 : alignement des
spans NER sur les noeuds XML, fusion CamemBERT/GLiNER, resolution des
recouvrements, orchestration).

Perimetre EXCLUSIF : align_spans_to_nodes, merge_model_results,
resolve_overlaps, align_and_inject. inject_entities et
backfill_reg_fragments sont deja couverts par tests/test_ner_dual_anchor.py
et ne sont pas dupliques ici (sauf en tant qu'etapes traversees par le test
d'integration de align_and_inject).

Les objets NERBlock / NERSpan (definis dans ner_detect.py, hors perimetre)
ne sont pas importes : on construit des doublures legeres
(types.SimpleNamespace) portant uniquement les attributs lus par
ner_align.py :
  - block : lang, text, source, container, char_to_w, char_to_reg
  - span  : start_char, end_char, text, entity_type, confidence, model

filter_aligned_by_pos (ner_filter.py, hors perimetre) est neutralisee par
monkeypatch dans les tests de align_and_inject pour isoler l'orchestrateur.
"""

from types import SimpleNamespace

import pytest
from lxml import etree

from src.enrichment.ner_align import (
    AlignedEntity,
    align_and_inject,
    align_spans_to_nodes,
    merge_model_results,
    resolve_overlaps,
)
from src.enrichment.ner_filter import filter_aligned_by_pos

NS = "http://www.tei-c.org/ns/1.0"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


def qlocal(el):
    """Nom local du tag, robuste aux deux conventions de namespace."""
    return etree.QName(el).localname


def _sub(parent, tag, text=None, **attrs):
    el = etree.SubElement(parent, f"{{{NS}}}{tag}")
    if text is not None:
        el.text = text
    for k, v in attrs.items():
        el.set(k, v)
    return el


def build_choice(parent, orig_words, reg_text):
    """Un <choice> avec <orig><s><w>... tokenise et un <reg> modernise."""
    choice = _sub(parent, "choice")
    orig = _sub(choice, "orig")
    s = _sub(orig, "s")
    ws = [_sub(s, "w", text=word) for word in orig_words]
    reg = _sub(choice, "reg", text=reg_text)
    return choice, orig, ws, reg


def make_span(start, end, text, entity_type="person", confidence=0.8, model="camembert"):
    """Doublure legere de NERSpan (seuls les attributs lus sont portes)."""
    return SimpleNamespace(
        start_char=start,
        end_char=end,
        text=text,
        entity_type=entity_type,
        confidence=confidence,
        model=model,
    )


def make_block(source, container, text="", char_to_w=None, char_to_reg=None):
    """Doublure legere de NERBlock."""
    return SimpleNamespace(
        lang="fra",
        text=text,
        source=source,
        container=container,
        char_to_w=char_to_w or [],
        char_to_reg=char_to_reg or [],
    )


CERT = {"low": 0.0, "medium": 0.6, "high": 0.85}
ENTITY_TYPES = {
    "person": {"tei_element": "persName"},
    "place": {"tei_element": "placeName"},
}


# =============================================================================
# align_spans_to_nodes -- source "orig"
# =============================================================================


def test_align_orig_spans_covers_words():
    root = etree.Element(f"{{{NS}}}ab")
    s = _sub(root, "s")
    w1 = _sub(s, "w", text="Roy")
    w2 = _sub(s, "w", text="Louis")
    char_to_w = [(0, 3, w1), (4, 9, w2)]
    block = make_block("orig", root, text="Roy Louis", char_to_w=char_to_w)
    span = make_span(0, 9, "Roy Louis", entity_type="person")

    result = align_spans_to_nodes(block, [span])

    assert len(result) == 1
    ent = result[0]
    assert ent.w_elements == [w1, w2]
    assert qlocal(ent.w_elements[0]) == "w"
    assert ent.entity_type == "person"
    assert ent.text == "Roy Louis"


def test_align_orig_spans_uncovered_span_dropped_silently():
    """A span whose char range overlaps no <w> is dropped without error --
    frozen behavior: no exception, no trace, just fewer results than spans."""
    root = etree.Element(f"{{{NS}}}ab")
    s = _sub(root, "s")
    w1 = _sub(s, "w", text="Roy")
    char_to_w = [(0, 3, w1)]
    block = make_block("orig", root, text="Roy", char_to_w=char_to_w)
    span_in = make_span(0, 3, "Roy")
    span_out = make_span(50, 60, "Nowhere")

    result = align_spans_to_nodes(block, [span_in, span_out])

    assert len(result) == 1
    assert result[0].text == "Roy"


def test_align_spans_to_nodes_no_spans_returns_empty_list():
    block = make_block("orig", etree.Element(f"{{{NS}}}ab"))
    assert align_spans_to_nodes(block, []) == []


def test_align_spans_to_nodes_unknown_source_returns_empty_list():
    block = make_block("mystery-source", etree.Element(f"{{{NS}}}ab"))
    span = make_span(0, 3, "x")
    assert align_spans_to_nodes(block, [span]) == []


# =============================================================================
# align_spans_to_nodes -- source "reg" (correspondance positionnelle)
# =============================================================================


def test_align_reg_spans_positional_mapping_collects_fragments():
    root = etree.Element(f"{{{NS}}}ab")
    choice, orig, (w1, w2), reg = build_choice(root, ["Roy", "Louis"], "Roi Louis")
    reg_text = "Roi Louis"
    char_to_reg = [(0, len(reg_text), reg)]
    block = make_block("reg", root, text=reg_text, char_to_reg=char_to_reg)
    span = make_span(0, len(reg_text), "Roi Louis", model="gliner")

    result = align_spans_to_nodes(block, [span])

    assert len(result) == 1
    ent = result[0]
    assert ent.w_elements == [w1, w2]
    assert ent.reg_fragments == [(reg, 0, 9)]
    assert qlocal(ent.reg_fragments[0][0]) == "reg"


def test_align_reg_spans_fallback_by_attribute_when_index_out_of_range():
    """When the <reg> word index has no matching <w> by position (more reg
    words than orig <w>), the code falls back to matching @norm/@lemma."""
    root = etree.Element(f"{{{NS}}}ab")
    choice = _sub(root, "choice")
    orig = _sub(choice, "orig")
    s = _sub(orig, "s")
    w1 = _sub(s, "w", text="Loys", norm="Louis")  # single <w>, index 0 only
    reg_text = "Le Roi Louis"  # 3 words -> "Louis" is word index 2
    reg = _sub(choice, "reg", text=reg_text)
    char_to_reg = [(0, len(reg_text), reg)]
    block = make_block("reg", root, text=reg_text, char_to_reg=char_to_reg)

    start = reg_text.index("Louis")
    span = make_span(start, start + len("Louis"), "Louis", model="gliner")

    result = align_spans_to_nodes(block, [span])

    assert len(result) == 1
    assert result[0].w_elements == [w1]


def test_align_reg_spans_fallback_fails_span_dropped_silently():
    """When the index is out of range AND no @norm/@lemma matches, the
    entire span is silently discarded -- no exception, nothing surfaced."""
    root = etree.Element(f"{{{NS}}}ab")
    choice = _sub(root, "choice")
    orig = _sub(choice, "orig")
    s = _sub(orig, "s")
    _sub(s, "w", text="Loys")  # no @norm/@lemma at all
    reg_text = "Le Roi Louis"
    reg = _sub(choice, "reg", text=reg_text)
    char_to_reg = [(0, len(reg_text), reg)]
    block = make_block("reg", root, text=reg_text, char_to_reg=char_to_reg)

    start = reg_text.index("Louis")
    span = make_span(start, start + len("Louis"), "Louis", model="gliner")

    result = align_spans_to_nodes(block, [span])

    assert result == []


def test_align_raw_spans_builds_text_node_entities():
    container = etree.Element(f"{{{NS}}}p")
    container.text = "Jean vint hier."
    block = make_block("raw", container, text=container.text)
    span = make_span(0, 4, "Jean", entity_type="person", model="gliner")

    result = align_spans_to_nodes(block, [span])

    assert len(result) == 1
    ent = result[0]
    assert ent.text_node is container
    assert ent.text_start == 0
    assert ent.text_end == 4
    assert ent.w_elements == []
    assert ent.reg_fragments == []


# =============================================================================
# merge_model_results
# =============================================================================


def test_merge_model_results_empty_and_single_sided_lists():
    assert merge_model_results([], []) == []

    cam = AlignedEntity(entity_type="person", text="Roy", confidence=0.7, model="camembert", w_elements=[])
    cam_list = [cam]
    cam_only = merge_model_results(cam_list, [])
    assert cam_only == cam_list
    assert cam_only is not cam_list  # new list instance, not the input
    assert cam_only[0] is cam

    gli = AlignedEntity(entity_type="place", text="Paris", confidence=0.6, model="gliner", w_elements=[])
    gli_list = [gli]
    gli_only = merge_model_results([], gli_list)
    assert gli_only == gli_list
    assert gli_only is not gli_list
    assert gli_only[0] is gli


def test_merge_model_results_same_words_same_type_merges_and_boosts_confidence():
    w1, w2 = object(), object()
    cam = AlignedEntity(entity_type="person", text="Roy Louis", confidence=0.7, model="camembert", w_elements=[w1, w2])
    gli = AlignedEntity(
        entity_type="person", text="Roi Louis", confidence=0.75, model="gliner",
        w_elements=[w1, w2], reg_fragments=[("reg-placeholder", 0, 9)],
    )

    result = merge_model_results([cam], [gli])

    assert len(result) == 1
    merged = result[0]
    assert merged.model == "both"
    assert merged.confidence == pytest.approx(0.85)  # max(0.7, 0.75) + 0.1
    assert merged.w_elements == [w1, w2]
    assert merged.reg_fragments == [("reg-placeholder", 0, 9)]


def test_merge_model_results_confidence_clipped_at_one():
    w1 = object()
    cam = AlignedEntity(entity_type="person", text="A", confidence=0.95, model="camembert", w_elements=[w1])
    gli = AlignedEntity(entity_type="person", text="A", confidence=0.98, model="gliner", w_elements=[w1])

    result = merge_model_results([cam], [gli])

    assert result[0].confidence == 1.0


def test_merge_model_results_same_words_different_type_keeps_both():
    """
    Memes mots, types differents -> les DEUX entites sont conservees.

    Une seule atteindra la sortie (resolve_overlaps ne garde qu'une annotation
    par <w>), mais le choix ne se fait pas ici : voir le test suivant, qui
    explique pourquoi supprimer la perdante des maintenant perdrait des
    mentions entieres.
    """
    w1, w2 = object(), object()
    cam = AlignedEntity(entity_type="person", text="Roy Louis", confidence=0.7, model="camembert", w_elements=[w1, w2])
    gli = AlignedEntity(
        entity_type="place", text="Roi Louis", confidence=0.8, model="gliner",
        w_elements=[w1, w2], reg_fragments=[("reg", 0, 9)],
    )

    result = merge_model_results([cam], [gli])

    assert len(result) == 2
    assert result[0] is cam  # intacte
    second = result[1]
    assert second.entity_type == "place"
    assert second.model == "gliner"   # garde son propre modele, pas "both"
    assert second.confidence == 0.8   # aucun bonus de confiance ici
    # L'entite ajoutee emprunte la cartographie <w> de CamemBERT, pas celle de
    # GLiNER (qui venait de _align_reg_spans).
    assert second.w_elements == cam.w_elements
    assert second.reg_fragments == [("reg", 0, 9)]


def test_keeping_both_types_saves_the_mention_when_pos_filter_rejects_the_winner():
    """
    Non-regression : pourquoi la branche ci-dessus doit garder les DEUX.

    filter_aligned_by_pos() s'execute entre merge_model_results() et
    resolve_overlaps(), et plusieurs de ses regles dependent du type -- une
    'person' doit porter un nom propre. L'annotation la plus confiante peut
    donc etre rejetee alors que l'autre passe.

    Ici « Apollon » est etiquete NOMcom : la lecture 'person' (0.9) est
    rejetee par les regles POS, et seule la presence de la lecture 'artwork'
    (0.6) sauve la mention. Ne garder que la plus confiante a la fusion la
    ferait disparaitre entierement -- ce que ce test interdit.
    """
    w = etree.Element("w")
    w.set(XML_ID, "w1")
    w.text = "Apollon"
    w.set("pos", "NOMcom")

    cam = AlignedEntity("person", "Apollon", 0.9, "camembert", [w], [])
    gli = AlignedEntity("artwork", "Apollon", 0.6, "gliner", [w], [])

    # La chaine reelle d'align_and_inject : fusion -> filtre POS -> recouvrements
    fusionnees = merge_model_results([cam], [gli])
    filtrees = filter_aligned_by_pos(fusionnees)
    finales = resolve_overlaps(filtrees)

    assert [e.entity_type for e in finales] == ["artwork"], (
        "la mention doit survivre en 'artwork' apres rejet de la lecture "
        "'person' par les regles POS"
    )


def test_merge_model_results_partial_overlap_above_threshold_still_merges():
    """Threshold is a Jaccard ratio (intersection/union) > 0.5, not an exact
    <w>-set match as the docstring implies: 2/3 overlap already merges."""
    w1, w2, w3 = object(), object(), object()
    cam = AlignedEntity(entity_type="person", text="A B C", confidence=0.6, model="camembert", w_elements=[w1, w2, w3])
    gli = AlignedEntity(entity_type="person", text="A B", confidence=0.6, model="gliner", w_elements=[w1, w2])

    result = merge_model_results([cam], [gli])

    assert len(result) == 1
    assert result[0].model == "both"
    assert result[0].w_elements == [w1, w2, w3]  # full CamemBERT w-set kept


def test_merge_model_results_overlap_at_threshold_keeps_both_independently():
    """At the boundary (ratio == 0.5, NOT > 0.5) neither merge branch fires.
    The docstring claims "partial overlap -> keep the one with better
    confidence", but the actual code always keeps CamemBERT's entity here
    regardless of confidence, AND never marks the GLiNER entity as used --
    so it reappears via the "unmatched GLiNER" pass. Both survive, unmerged,
    even though they describe (mostly) the same words."""
    w1, w2 = object(), object()
    cam = AlignedEntity(entity_type="person", text="Roy", confidence=0.3, model="camembert", w_elements=[w1, w2])
    gli = AlignedEntity(entity_type="person", text="Roi", confidence=0.99, model="gliner", w_elements=[w1])

    result = merge_model_results([cam], [gli])

    assert len(result) == 2
    assert result[0] is cam
    assert result[1] is gli
    # Low-confidence CamemBERT entity is the one kept "as the merge result",
    # despite GLiNER being far more confident -- no comparison ever happens.
    assert result[0].confidence == 0.3


# =============================================================================
# resolve_overlaps
# =============================================================================


def test_resolve_overlaps_empty_list():
    assert resolve_overlaps([]) == []


def test_resolve_overlaps_tokenized_conflict_keeps_higher_confidence():
    w1, w2, w3 = object(), object(), object()
    high = AlignedEntity(entity_type="person", text="A", confidence=0.9, model="camembert", w_elements=[w1, w2])
    low = AlignedEntity(entity_type="place", text="B", confidence=0.4, model="camembert", w_elements=[w2, w3])

    result = resolve_overlaps([low, high])  # input order deliberately reversed

    assert len(result) == 1
    assert result[0] is high


def test_resolve_overlaps_raw_text_ranges_overlap():
    node = object()
    high = AlignedEntity(entity_type="person", text="A", confidence=0.9, model="camembert", text_node=node, text_start=0, text_end=10)
    low = AlignedEntity(entity_type="place", text="B", confidence=0.5, model="camembert", text_node=node, text_start=5, text_end=15)

    result = resolve_overlaps([low, high])

    assert len(result) == 1
    assert result[0] is high


def test_resolve_overlaps_no_overlap_keeps_both():
    w1, w2, w3, w4 = object(), object(), object(), object()
    a = AlignedEntity(entity_type="person", text="A", confidence=0.9, model="camembert", w_elements=[w1, w2])
    b = AlignedEntity(entity_type="place", text="B", confidence=0.4, model="camembert", w_elements=[w3, w4])

    result = resolve_overlaps([a, b])

    assert len(result) == 2
    assert a in result and b in result


def test_resolve_overlaps_entity_without_anchor_or_text_node_is_dropped():
    """An entity with neither w_elements nor a text_node (e.g. a reg-only
    fragment that never got a <w> mapping) matches neither branch of the
    if/elif and silently falls out of the result -- another way spans can
    be lost without any error or log surfaced to the caller."""
    orphan = AlignedEntity(entity_type="person", text="ghost", confidence=0.99, model="camembert")

    result = resolve_overlaps([orphan])

    assert result == []


# =============================================================================
# align_and_inject -- integration
# =============================================================================


def test_align_and_inject_empty_inputs():
    assert align_and_inject([], [], ENTITY_TYPES, CERT) == []

    block = make_block("orig", etree.Element(f"{{{NS}}}ab"))
    assert align_and_inject([block], [], ENTITY_TYPES, CERT) == []


def test_align_and_inject_orig_only_container_takes_else_branch(monkeypatch):
    """A container with only <orig> spans (no <reg> counterpart) skips
    merge_model_results and goes through align_and_inject's plain
    extend-the-lists branch."""
    monkeypatch.setattr("src.enrichment.ner_align.filter_aligned_by_pos", lambda x, *a, **k: x)

    root = etree.Element(f"{{{NS}}}ab")
    s = _sub(root, "s")
    w1 = _sub(s, "w", text="Petrus")
    block = make_block("orig", root, text="Petrus", char_to_w=[(0, 6, w1)])
    span = make_span(0, 6, "Petrus", entity_type="person", confidence=0.7, model="camembert")

    resolved = align_and_inject([block], [[span]], ENTITY_TYPES, CERT)

    assert len(resolved) == 1
    ent = resolved[0]
    # No <choice>/<reg> ancestor: backfill_reg_fragments is a documented no-op.
    assert ent.reg_fragments == []

    wraps = [e for e in root.iter(f"{{{NS}}}persName")]
    assert len(wraps) == 1
    assert wraps[0][0].text == "Petrus"


def test_align_and_inject_full_pipeline(monkeypatch):
    """Full chain on one French container: align both sources, merge
    (person entity merges across models; place entity from CamemBERT alone
    survives the merge step but not overlap resolution), backfill (no-op,
    already dual-anchored), then dual injection into <orig> and <reg>."""
    monkeypatch.setattr("src.enrichment.ner_align.filter_aligned_by_pos", lambda x, *a, **k: x)

    root = etree.Element(f"{{{NS}}}TEI", nsmap={None: NS})
    text_el = _sub(root, "text")
    body = _sub(text_el, "body")
    ab = _sub(body, "ab")

    choice, orig, ws, reg = build_choice(ab, ["Roy", "Louis", "de", "France"], "Roi Louis de France")
    w1, w2, w3, w4 = ws

    orig_text = "Roy Louis de France"
    reg_text = "Roi Louis de France"
    char_to_w = [(0, 3, w1), (4, 9, w2), (10, 12, w3), (13, 19, w4)]
    char_to_reg = [(0, len(reg_text), reg)]

    block_orig = make_block("orig", ab, text=orig_text, char_to_w=char_to_w)
    block_reg = make_block("reg", ab, text=reg_text, char_to_reg=char_to_reg)

    # CamemBERT sees two (overlapping) entities on <orig>; GLiNER only
    # confirms the person on <reg>.
    span_person_orig = make_span(0, 9, "Roy Louis", entity_type="person", confidence=0.8, model="camembert")
    span_place_orig = make_span(4, 12, "Louis de", entity_type="place", confidence=0.6, model="camembert")
    span_person_reg = make_span(0, 9, "Roi Louis", entity_type="person", confidence=0.9, model="gliner")

    blocks = [block_orig, block_reg]
    all_spans = [[span_person_orig, span_place_orig], [span_person_reg]]

    resolved = align_and_inject(blocks, all_spans, ENTITY_TYPES, CERT)

    # Overlap resolution drops the place entity: it shares <w2> with the
    # (higher-confidence, merged) person entity.
    assert len(resolved) == 1
    ent = resolved[0]
    assert ent.entity_type == "person"
    assert ent.model == "both"
    assert ent.confidence == pytest.approx(1.0)  # min(1.0, max(0.8, 0.9) + 0.1)

    orig_wraps = list(orig.iter(f"{{{NS}}}persName"))
    assert len(orig_wraps) == 1
    assert [qlocal(w) for w in orig_wraps[0]] == ["w", "w"]
    assert [w.text for w in orig_wraps[0]] == ["Roy", "Louis"]

    reg_wraps = list(reg.iter(f"{{{NS}}}persName"))
    assert len(reg_wraps) == 1
    assert reg_wraps[0].text == "Roi Louis"

    # The dropped place entity never made it into the tree.
    assert not list(ab.iter(f"{{{NS}}}placeName"))


# =============================================================================
# Dates : la valeur machine, quand le texte la porte (audit 1.10)
# =============================================================================

def test_a_date_entity_carries_its_machine_readable_value():
    """Une <date> qui ne dit que « M.DC.LIX » ne se trie pas, ne se
    filtre pas et ne se place sur aucune frise — ce pour quoi on extrait
    une date."""
    from src.enrichment.ner_align import _make_entity_element
    from config import NER_ENTITY_TYPES

    elem = _make_entity_element("date", "high", NER_ENTITY_TYPES, text="M.DC.LIX")

    assert qlocal(elem) == "date"
    assert elem.get("when") == "1659"
    assert elem.get("cert") == "high"


def test_a_date_the_parser_cannot_read_gets_no_value():
    from src.enrichment.ner_align import _make_entity_element
    from config import NER_ENTITY_TYPES

    elem = _make_entity_element("date", "medium", NER_ENTITY_TYPES,
                                text="le 23 juin 1652")

    assert elem.get("when") is None
    assert elem.get("notBefore") is None


def test_the_ner_certainty_survives_a_date_read_with_low_confidence():
    """Le @cert vient de la confiance du modele NER ; une date lue avec
    une inference basse ne doit pas le remonter."""
    from src.enrichment.ner_align import _make_entity_element
    from config import NER_ENTITY_TYPES

    elem = _make_entity_element("date", "low", NER_ENTITY_TYPES, text="159")

    assert elem.get("cert") == "low"
    assert (elem.get("notBefore"), elem.get("notAfter")) == ("1590", "1599")


def test_other_entity_types_are_untouched_by_the_date_reader():
    from src.enrichment.ner_align import _make_entity_element
    from config import NER_ENTITY_TYPES

    elem = _make_entity_element("person", "high", NER_ENTITY_TYPES, text="1659")

    assert elem.get("when") is None
