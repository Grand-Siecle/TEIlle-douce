# -----------------------------------------------------------
# Characterization / boundary tests for the <foreign> inline
# splicing arithmetic in src/body/builder.py, flagged by
# docs/rapport_audit.md as a major risk (offset off-by-one,
# silent text loss/duplication, 0% covered):
#
#   - _insert_foreign_inline (offset -> per-line splice ops)
#   - _build_offset_to_line   (joined-text offset -> (line, j) map)
#   - _splice_lb_tail         (splices <foreign> into one <lb/> tail)
#
# These three functions are called directly with hand-built
# line_texts / segments -- never through the real Lingua detector
# (slow, non-deterministic). Fixtures mirror the real pipeline's
# invariant (src/body/builder.py ~L376-377): each <lb/> is a direct
# child of the container and its .tail equals the corresponding
# line_texts[i] string.
#
# Run: venv/bin/python -m pytest tests/test_body_foreign.py -q
# -----------------------------------------------------------
import pytest
from lxml import etree

from src.body.builder import (
    _build_offset_to_line,
    _insert_foreign_inline,
    _splice_lb_tail,
)


def qlocal(el):
    """Local (namespace-stripped) tag name -- robust to the project's mix
    of bare and namespaced tags (audit SS4.2)."""
    return etree.QName(el).localname


def texte_total(el):
    """Concatenation of every bit of text content held inside *el*
    (el.text plus every descendant's text/tail, in document order).
    Does NOT include el's own .tail (that belongs to el's parent).

    This is the central invariant checked by every test below: this
    value must be identical before and after a splice operation --
    nothing may be lost or duplicated.
    """
    return "".join(el.itertext())


XML_LANG_ATT = "{http://www.w3.org/XML/1998/namespace}lang"


def make_container(tag, line_texts):
    """Build <tag><lb/>line0<lb/>line1...</tag>, mirroring how the real
    pipeline attaches lines: one <lb/> per entry of line_texts, appended
    as a direct child of the container, with lb.tail == line_texts[i].
    """
    container = etree.Element(tag)
    for i, text in enumerate(line_texts):
        lb = etree.SubElement(container, "lb", corresp=f"#l{i}")
        lb.tail = text
    return container


# =============================================================================
# 1. _build_offset_to_line
# =============================================================================


def test_offset_to_line_single_line():
    line_texts = ["abc"]
    assert _build_offset_to_line(line_texts) == [(0, 0), (0, 1), (0, 2)]


def test_offset_to_line_two_lines_boundary():
    # joined = "ab cd" ; offset 2 is the separator space (marker -1,-1),
    # offset 3 is the first char of line 1 -- this is the exact frontier.
    line_texts = ["ab", "cd"]
    table = _build_offset_to_line(line_texts)
    joined = " ".join(line_texts)
    assert len(table) == len(joined)
    assert table == [(0, 0), (0, 1), (-1, -1), (1, 0), (1, 1)]
    # explicit boundary check requested by the brief
    assert table[len("ab")] == (-1, -1)
    assert table[len("ab") + 1] == (1, 0)


def test_offset_to_line_three_lines():
    line_texts = ["A", "BB", "C"]
    joined = " ".join(line_texts)
    table = _build_offset_to_line(line_texts)
    assert len(table) == len(joined)
    assert table == [
        (0, 0),
        (-1, -1),
        (1, 0),
        (1, 1),
        (-1, -1),
        (2, 0),
    ]


def test_offset_to_line_empty_line_in_middle_is_skipped_not_double_separated():
    # An empty line contributes nothing -- no entry, and critically no
    # extra separator marker either side of it.
    line_texts = ["ab", "", "cd"]
    joined = " ".join(t for t in line_texts if t)  # "ab cd"
    table = _build_offset_to_line(line_texts)
    assert len(table) == len(joined)
    assert table == [(0, 0), (0, 1), (-1, -1), (2, 0), (2, 1)]


def test_offset_to_line_all_empty_lines():
    assert _build_offset_to_line(["", "", ""]) == []


# =============================================================================
# 2. _splice_lb_tail
# =============================================================================


def test_splice_lb_tail_insertion_at_start():
    tail = "hello world"
    container = make_container("ab", [tail])
    lb = container[0]

    _splice_lb_tail(lb, [(0, 5, "la")])

    assert not lb.tail  # nothing before the foreign run
    foreign = lb.getnext()
    assert qlocal(foreign) == "foreign"
    assert foreign.get("{http://www.w3.org/XML/1998/namespace}lang") == "la"
    assert foreign.text == tail[0:5]
    assert foreign.tail == tail[5:]
    assert texte_total(container) == tail


def test_splice_lb_tail_insertion_in_middle():
    tail = "hello world"
    container = make_container("ab", [tail])
    lb = container[0]

    _splice_lb_tail(lb, [(2, 4, "la")])

    assert lb.tail == tail[0:2]
    foreign = lb.getnext()
    assert foreign.text == tail[2:4]
    assert foreign.tail == tail[4:]
    assert texte_total(container) == tail


def test_splice_lb_tail_insertion_at_end():
    tail = "hello world"
    container = make_container("ab", [tail])
    lb = container[0]

    _splice_lb_tail(lb, [(6, 11, "la")])

    assert lb.tail == tail[0:6]
    foreign = lb.getnext()
    assert foreign.text == tail[6:11]
    assert not foreign.tail  # segment reaches the very last character
    assert texte_total(container) == tail


def test_splice_lb_tail_multiple_ops_same_lb():
    tail = "abcdefgh"
    container = make_container("ab", [tail])
    lb = container[0]

    _splice_lb_tail(lb, [(1, 3, "la"), (5, 7, "it")])

    siblings = list(container)
    assert [qlocal(s) for s in siblings] == ["lb", "foreign", "foreign"]
    assert lb.tail == tail[0:1]
    f1, f2 = siblings[1], siblings[2]
    assert (f1.text, f1.get("{http://www.w3.org/XML/1998/namespace}lang")) == (
        tail[1:3],
        "la",
    )
    assert f1.tail == tail[3:5]
    assert (f2.text, f2.get("{http://www.w3.org/XML/1998/namespace}lang")) == (
        tail[5:7],
        "it",
    )
    assert f2.tail == tail[7:]
    assert texte_total(container) == tail


def test_splice_lb_tail_empty_ops_list_leaves_tail_unchanged():
    tail = "unchanged text"
    container = make_container("ab", [tail])
    lb = container[0]

    _splice_lb_tail(lb, [])

    assert lb.tail == tail
    assert list(container) == [lb]  # no siblings inserted
    assert texte_total(container) == tail


def test_splice_lb_tail_empty_tail_is_a_noop():
    container = make_container("ab", [""])
    lb = container[0]
    assert not lb.tail

    _splice_lb_tail(lb, [(0, 1, "la")])

    assert not lb.tail
    assert list(container) == [lb]


# =============================================================================
# 3. _insert_foreign_inline -- nominal case
# =============================================================================


def test_insert_foreign_inline_nominal_middle_of_single_line():
    line = "bonjour mundus ami"
    container = make_container("ab", [line])
    start = line.index("mundus")
    end = start + len("mundus")

    _insert_foreign_inline(container, [line], [(start, end, "la")])

    lb = container[0]
    assert lb.tail == line[:start]
    foreign = lb.getnext()
    assert qlocal(foreign) == "foreign"
    assert foreign.get("{http://www.w3.org/XML/1998/namespace}lang") == "la"
    assert foreign.text == "mundus"
    assert foreign.tail == line[end:]
    assert texte_total(container) == line


# =============================================================================
# 4. _insert_foreign_inline -- boundaries
# =============================================================================


def test_insert_foreign_inline_segment_at_offset_zero():
    line = "mundus bonus"
    container = make_container("ab", [line])
    end = len("mundus")

    _insert_foreign_inline(container, [line], [(0, end, "la")])

    lb = container[0]
    assert not lb.tail
    foreign = lb.getnext()
    assert foreign.text == line[:end]
    assert foreign.tail == line[end:]
    assert texte_total(container) == line


def test_insert_foreign_inline_segment_ending_at_last_character():
    line = "bonus mundus"
    container = make_container("ab", [line])
    start = line.index("mundus")

    _insert_foreign_inline(container, [line], [(start, len(line), "la")])

    lb = container[0]
    assert lb.tail == line[:start]
    foreign = lb.getnext()
    assert foreign.text == line[start:]
    assert not foreign.tail
    assert texte_total(container) == line


def test_insert_foreign_inline_segment_covers_entire_line():
    line = "totuslatinus"
    container = make_container("ab", [line])

    _insert_foreign_inline(container, [line], [(0, len(line), "la")])

    lb = container[0]
    assert not lb.tail
    foreign = lb.getnext()
    assert foreign.text == line
    assert not foreign.tail
    assert texte_total(container) == line


# =============================================================================
# 5. _insert_foreign_inline -- cross-line segment
# =============================================================================


def test_insert_foreign_inline_segment_spans_two_lines():
    line0 = "magna lati"
    line1 = "numque bonum"
    line_texts = [line0, line1]
    container = make_container("ab", line_texts)

    joined = " ".join(line_texts)
    seg_start = joined.index("lati")
    seg_end = joined.index("numque") + len("num")
    segments = [(seg_start, seg_end, "la")]

    original_total = texte_total(container)
    assert original_total == line0 + line1  # sanity: raw doc has no separator

    _insert_foreign_inline(container, line_texts, segments)

    lb0, lb1 = container[0], container[2]
    assert qlocal(container[1]) == "foreign"
    assert qlocal(container[3]) == "foreign"

    foreign0 = container[1]
    assert lb0.tail == "magna "
    assert foreign0.text == "lati"
    assert foreign0.get("{http://www.w3.org/XML/1998/namespace}lang") == "la"
    assert not foreign0.tail  # segment reaches the end of line0's tail

    foreign1 = container[3]
    assert not lb1.tail  # segment starts at offset 0 of line1's tail
    assert foreign1.text == "num"
    assert foreign1.get("{http://www.w3.org/XML/1998/namespace}lang") == "la"
    assert foreign1.tail == "que bonum"

    # The central invariant: no character lost or duplicated across the
    # line break, even though the split point falls mid-word.
    assert texte_total(container) == original_total


# =============================================================================
# 6. _insert_foreign_inline -- degraded inputs
# =============================================================================


def test_insert_foreign_inline_empty_segments_list_is_a_noop():
    line = "some plain text"
    container = make_container("ab", [line])
    original_total = texte_total(container)

    _insert_foreign_inline(container, [line], [])

    assert list(container) == [container[0]]  # still just the lb
    assert qlocal(container[0]) == "lb"
    assert container[0].tail == line
    assert texte_total(container) == original_total


def test_insert_foreign_inline_segment_fully_out_of_bounds_is_ignored():
    line = "short"
    container = make_container("ab", [line])
    original_total = texte_total(container)

    _insert_foreign_inline(container, [line], [(10, 20, "la")])

    assert len(container) == 1
    assert qlocal(container[0]) == "lb"
    assert container[0].tail == line
    assert texte_total(container) == original_total


def test_insert_foreign_inline_segment_partially_out_of_bounds_is_clamped():
    line = "abcde"
    container = make_container("ab", [line])
    original_total = texte_total(container)

    # end (20) is beyond len(line) (5); should clamp to the trailing
    # portion of the line rather than raising or silently dropping.
    _insert_foreign_inline(container, [line], [(2, 20, "la")])

    lb = container[0]
    assert lb.tail == line[:2]
    foreign = lb.getnext()
    assert foreign.text == line[2:]
    assert not foreign.tail
    assert texte_total(container) == original_total


def test_insert_foreign_inline_two_adjacent_segments():
    line = "abcdefgh"
    container = make_container("ab", [line])
    original_total = texte_total(container)

    _insert_foreign_inline(container, [line], [(0, 3, "la"), (3, 6, "it")])

    assert [qlocal(c) for c in container] == ["lb", "foreign", "foreign"]
    lb, f1, f2 = container
    assert not lb.tail
    assert f1.text == "abc"
    assert f1.get("{http://www.w3.org/XML/1998/namespace}lang") == "la"
    assert f2.text == "def"
    assert f2.get("{http://www.w3.org/XML/1998/namespace}lang") == "it"
    assert f2.tail == "gh"
    assert texte_total(container) == original_total


def test_insert_foreign_inline_line_index_beyond_available_lb_is_ignored():
    # Defensive guard: if line_texts describes more lines than the
    # container actually has <lb/> children for (a line_texts/<lb>
    # mismatch), the splice for the missing line is silently skipped
    # rather than raising -- and the lines that DO have a matching
    # <lb/> are still spliced correctly.
    line0, line1 = "primus", "secundus"
    line_texts = [line0, line1]
    container = make_container("ab", [line0])  # only one <lb/>, not two
    original_total = texte_total(container)

    joined = " ".join(line_texts)
    seg_start = joined.index("secundus")
    segments = [(seg_start, seg_start + len("secundus"), "la")]

    _insert_foreign_inline(container, line_texts, segments)

    # No <lb/> exists for line_idx=1, so nothing is inserted at all.
    assert list(container) == [container[0]]
    assert qlocal(container[0]) == "lb"
    assert container[0].tail == line0
    assert texte_total(container) == original_total


def test_two_overlapping_segments_keep_both_languages():
    """Deux segments ne peuvent pas couvrir les memes caracteres — un
    element XML n'a qu'une langue — mais le second etait perdu en
    entier, sa langue avec (audit 6.1). Il est rogne a ce qui reste
    libre. L'invariant tient dans les deux cas : aucun texte perdu ni
    duplique."""
    line = "abcdefgh"
    container = make_container("ab", [line])
    original_total = texte_total(container)

    _insert_foreign_inline(container, [line], [(0, 5, "la"), (3, 8, "it")])

    assert [qlocal(c) for c in container] == ["lb", "foreign", "foreign"]
    lb, premier, second = container
    assert not lb.tail
    assert (premier.text, premier.get(XML_LANG_ATT)) == ("abcde", "la")
    assert (second.text, second.get(XML_LANG_ATT)) == ("fgh", "it")
    assert texte_total(container) == original_total


def test_a_segment_entirely_covered_by_another_is_dropped_with_a_warning(caplog):
    """Rien ne reste a lui donner : le perdre est le seul choix, mais il
    doit se voir dans les logs."""
    line = "abcdefgh"
    container = make_container("ab", [line])

    with caplog.at_level("WARNING"):
        _insert_foreign_inline(container, [line], [(0, 8, "la"), (2, 5, "it")])

    assert [qlocal(c) for c in container] == ["lb", "foreign"]
    assert "it" in caplog.text and "dropped" in caplog.text
