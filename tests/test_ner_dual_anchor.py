# -----------------------------------------------------------
# Coherence tests for NER dual anchoring (<orig> + <reg>).
#
# Policy: every entity carrying <w> anchors is injected in <orig>
# (diplomatic view) AND, when a modernized <reg> exists, in <reg>
# (modernized view). Both copies receive the same @ref in Phase 9.
#
# Run from the repo root:
#   venv/bin/python tests/test_ner_dual_anchor.py
# -----------------------------------------------------------
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lxml import etree

from src.enrichment.ner_align import (
    AlignedEntity,
    backfill_reg_fragments,
    inject_entities,
)
from src.enrichment.ner_resolve import ResolvedEntity, add_refs_to_body

NS = "http://www.tei-c.org/ns/1.0"
XML_LANG = "{http://www.w3.org/XML/1998/namespace}lang"

ENTITY_TYPES = {
    "person": {"tei_element": "persName"},
    "place": {"tei_element": "placeName"},
}
CERT = {"low": 0.0, "medium": 0.6, "high": 0.85}


def _sub(parent, tag, text=None, **attrs):
    el = etree.SubElement(parent, f"{{{NS}}}{tag}")
    if text is not None:
        el.text = text
    for k, v in attrs.items():
        el.set(k, v)
    return el


def build_choice(ab, orig_words, reg_text):
    """One <choice> with tokenized <orig><s><w>… and a modernized <reg>."""
    choice = _sub(ab, "choice")
    orig = _sub(choice, "orig")
    s = _sub(orig, "s")
    ws = [_sub(s, "w", text=word) for word in orig_words]
    reg = _sub(choice, "reg", text=reg_text, type="modernized")
    return choice, orig, ws, reg


def build_tree():
    root = etree.Element(f"{{{NS}}}TEI", nsmap={None: NS})
    text_el = _sub(root, "text")
    body = _sub(text_el, "body")
    ab = _sub(body, "ab")
    ab.set(XML_LANG, "fra")
    return root, ab


def wrappers_in(scope, tag="persName"):
    return [e for e in scope.iter(f"{{{NS}}}{tag}") if e.get("resp") == "#ner-auto"]


def test_dual_injection_both_anchors():
    """Entity with both anchors lands in <orig> AND <reg>."""
    root, ab = build_tree()
    choice, orig, (w1, w2), reg = build_choice(ab, ["Roy", "Louis"], "Roi Louis")

    ent = AlignedEntity(
        entity_type="person", text="Roi Louis", confidence=0.9, model="both",
        w_elements=[w1, w2], reg_fragments=[(reg, 0, 9)],
    )
    inject_entities([ent], CERT, ENTITY_TYPES)

    orig_wraps = wrappers_in(orig)
    assert len(orig_wraps) == 1, f"expected 1 <orig> wrapper, got {len(orig_wraps)}"
    assert [w.text for w in orig_wraps[0]] == ["Roy", "Louis"]

    reg_wraps = wrappers_in(reg)
    assert len(reg_wraps) == 1, f"expected 1 <reg> wrapper, got {len(reg_wraps)}"
    assert reg_wraps[0].text == "Roi Louis"


def test_backfill_full_entity():
    """CamemBERT-only entity gets <reg> fragments computed from its <w>."""
    root, ab = build_tree()
    choice, orig, (w1, w2), reg = build_choice(ab, ["Roy", "Louis"], "Roi Louis")

    ent = AlignedEntity(
        entity_type="person", text="Roy Louis", confidence=0.8, model="camembert",
        w_elements=[w1, w2],
    )
    backfill_reg_fragments([ent])
    assert ent.reg_fragments == [(reg, 0, 9)], f"got {ent.reg_fragments}"

    inject_entities([ent], CERT, ENTITY_TYPES)
    assert len(wrappers_in(orig)) == 1
    assert len(wrappers_in(reg)) == 1
    assert wrappers_in(reg)[0].text == "Roi Louis"


def test_backfill_partial_entity():
    """Backfill maps a sub-span of the line (second word only)."""
    root, ab = build_tree()
    choice, orig, (w1, w2), reg = build_choice(ab, ["Roy", "Louis"], "Roi Louis")

    ent = AlignedEntity(
        entity_type="person", text="Louis", confidence=0.8, model="camembert",
        w_elements=[w2],
    )
    backfill_reg_fragments([ent])
    assert ent.reg_fragments == [(reg, 4, 9)], f"got {ent.reg_fragments}"


def test_backfill_without_choice():
    """<w> outside any <choice>: entity stays orig-only, no crash."""
    root, ab = build_tree()
    s = _sub(ab, "s")
    w1 = _sub(s, "w", text="Paris")

    ent = AlignedEntity(
        entity_type="place", text="Paris", confidence=0.8, model="camembert",
        w_elements=[w1],
    )
    backfill_reg_fragments([ent])
    assert ent.reg_fragments == []

    inject_entities([ent], CERT, ENTITY_TYPES)
    assert len(wrappers_in(ab, "placeName")) == 1


def test_same_ref_on_both_layers():
    """Phase 9 gives the <orig> copy and the <reg> copy the same @ref."""
    root, ab = build_tree()
    choice, orig, (w1, w2), reg = build_choice(ab, ["Roy", "Louis"], "Roi Louis")

    ent = AlignedEntity(
        entity_type="person", text="Roi Louis", confidence=0.9, model="both",
        w_elements=[w1, w2], reg_fragments=[(reg, 0, 9)],
    )
    inject_entities([ent], CERT, ENTITY_TYPES)

    resolved = ResolvedEntity(
        entity_type="person", canonical_name="Louis",
        xml_id="pers-test1", mentions=[ent],
    )
    add_refs_to_body(root, [resolved], ENTITY_TYPES)

    all_wraps = wrappers_in(ab)
    assert len(all_wraps) == 2
    refs = sorted(w.get("ref") or "MISSING" for w in all_wraps)
    assert refs == ["#pers-test1", "#pers-test1"], f"got {refs}"


def test_multi_choice_entity_refs():
    """Entity spanning two <choice> lines: 2 orig + 2 reg wrappers, same @ref."""
    root, ab = build_tree()
    c1, orig1, (w1,), reg1 = build_choice(ab, ["Loys"], "Louis")
    c2, orig2, (w2,), reg2 = build_choice(ab, ["Quatorziesme"], "Quatorzième")

    ent = AlignedEntity(
        entity_type="person", text="Loys Quatorziesme", confidence=0.8,
        model="camembert", w_elements=[w1, w2],
    )
    backfill_reg_fragments([ent])
    assert ent.reg_fragments == [(reg1, 0, 5), (reg2, 0, 11)], f"got {ent.reg_fragments}"

    inject_entities([ent], CERT, ENTITY_TYPES)
    resolved = ResolvedEntity(
        entity_type="person", canonical_name="Louis XIV",
        xml_id="pers-test2", mentions=[ent],
    )
    add_refs_to_body(root, [resolved], ENTITY_TYPES)

    all_wraps = wrappers_in(ab)
    assert len(all_wraps) == 4, f"expected 4 wrappers, got {len(all_wraps)}"
    missing = [etree.tostring(w) for w in all_wraps if not w.get("ref")]
    assert not missing, f"wrappers without @ref: {missing}"
    refs = {w.get("ref") for w in all_wraps}
    assert refs == {"#pers-test2"}, f"got {refs}"


def test_non_adjacent_w_preserves_order():
    """A <pc> between entity <w> must stay in place: one wrapper per run of
    adjacent <w>, no reordering of the diplomatic text."""
    root, ab = build_tree()
    choice = _sub(ab, "choice")
    orig = _sub(choice, "orig")
    s = _sub(orig, "s")
    w1 = _sub(s, "w", text="Saint")
    pc = _sub(s, "pc", text="-")
    w2 = _sub(s, "w", text="Denis")
    reg = _sub(choice, "reg", text="Saint-Denis", type="modernized")

    ent = AlignedEntity(
        entity_type="place", text="Saint-Denis", confidence=0.9, model="both",
        w_elements=[w1, w2], reg_fragments=[(reg, 0, 11)],
    )
    inject_entities([ent], CERT, ENTITY_TYPES)

    children = list(s)
    tags = [c.tag.split("}")[-1] for c in children]
    assert tags == ["placeName", "pc", "placeName"], f"got {tags}"
    assert [w.text for w in children[0]] == ["Saint"]
    assert [w.text for w in children[2]] == ["Denis"]

    resolved = ResolvedEntity(
        entity_type="place", canonical_name="Saint-Denis",
        xml_id="place-test3", mentions=[ent],
    )
    add_refs_to_body(root, [resolved], ENTITY_TYPES)
    refs = {w.get("ref") for w in wrappers_in(ab, "placeName")}
    assert refs == {"#place-test3"}, f"got {refs}"


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS  {t.__name__}")
        except AssertionError as e:
            failed += 1
            print(f"FAIL  {t.__name__}: {e}")
        except Exception as e:
            failed += 1
            print(f"ERROR {t.__name__}: {type(e).__name__}: {e}")
    print(f"\n{len(tests) - failed}/{len(tests)} tests passed")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
