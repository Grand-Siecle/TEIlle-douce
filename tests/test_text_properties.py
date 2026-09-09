# -----------------------------------------------------------
# `report/text.py`, as properties rather than examples.
#
# Every renderer in this project has a width sweep, and the same defect
# has slipped through four of them: a sweep that starts above the widths
# where the bug lives. `docs/developer-guide.md` lists it first among the
# five hollow shapes, and it has cost six review rounds.
#
# The cure is not more examples. This module is pure arithmetic on text
# with invariants its own docstrings state — "never wider", "the leaf is
# kept whole", "the half that survives whole" — so Hypothesis picks the
# widths and the strings, including the ones nobody thinks of: a CJK
# glyph two cells wide, a newline in an httpx message, a lone ellipsis.
#
# Run: venv/bin/python -m pytest tests/test_text_properties.py -q
# -----------------------------------------------------------
from hypothesis import assume, given, settings
from hypothesis import strategies as st

from teille_douce.report.text import (ELLIPSIS, cells, clip, clip_left, pad,
                                      shorten_path)

# The alphabet is the corpus this project actually sees, plus the two
# kinds of character its contract is written about: something two cells
# wide, and something a terminal would act on rather than print.
TEXT = st.text(alphabet=st.sampled_from(
    "abcXYZ 0129/._-¬éàœ—…\n\t\x00\x7f漢字ｱ"), max_size=40)
# The same alphabet with nothing a terminal would act on. Used where the
# property is about what SURVIVES: filtering the control characters out
# with `assume` throws away nine inputs in ten, which Hypothesis rightly
# calls a health failure — the generator has to produce what the test is
# about, not sieve for it.
PRINTABLE = st.text(alphabet=st.sampled_from(
    "abcXYZ 0129/._-¬éàœ—…漢字ｱ"), max_size=40)
ROOM = st.integers(min_value=-3, max_value=60)
CONTROL = set(chr(code) for code in range(0x20)) | {chr(0x7f)}


@st.composite
def _text_and_a_room_that_cuts_it(draw):
    """A printable string, and a room narrower than it."""
    text = draw(PRINTABLE.filter(lambda said: cells(said) > 2))
    return text, draw(st.integers(min_value=1, max_value=cells(text) - 1))


@st.composite
def _a_path_and_room_for_its_leaf(draw):
    """A path, and a room too narrow for the whole but wide enough that
    the leaf must survive."""
    parts = draw(st.lists(PRINTABLE.filter(lambda part: part and "/" not in part),
                          min_size=2, max_size=4))
    path = "/".join(parts)
    leaf = parts[-1]
    room = draw(st.integers(min_value=cells(leaf) + 3,
                            max_value=max(cells(leaf) + 3, cells(path))))
    return path, leaf, room


# A half that carries something: `pad` treats one made only of spaces as
# no half at all, since a column of spaces is a line of noise.
CARRYING = PRINTABLE.map(str.strip).filter(lambda said: said)


@st.composite
def _two_halves_and_room_for_both(draw):
    """Two halves that carry something, and a room that fits them."""
    left = draw(CARRYING)
    right = draw(CARRYING)
    room = draw(st.integers(min_value=cells(left) + cells(right) + 1,
                            max_value=cells(left) + cells(right) + 30))
    return left, right, room


def _printable(text):
    """No character a terminal would act on rather than print."""
    return not (CONTROL & set(text))


# =============================================================================
# Never wider than the room, whatever the string
# =============================================================================

@given(TEXT, ROOM)
def test_clip_never_exceeds_the_room(text, room):
    assert cells(clip(text, room)) <= max(0, room)


@given(TEXT, ROOM)
def test_clip_left_never_exceeds_the_room(text, room):
    assert cells(clip_left(text, room)) <= max(0, room)


@given(TEXT, ROOM)
def test_shorten_path_never_exceeds_the_room(text, room):
    assert cells(shorten_path(text, room)) <= max(0, room)


@given(TEXT, TEXT, ROOM, st.sampled_from(("left", "right")))
def test_pad_never_exceeds_the_room(left, right, room, keep):
    assert cells(pad(left, right, room, keep=keep)) <= max(0, room)


# =============================================================================
# Nothing a terminal would act on survives
# =============================================================================

@given(TEXT, ROOM)
def test_no_control_character_reaches_a_line(text, room):
    """A newline in a clipped string renders as a second line the width
    contract never saw, unindented and unclipped. httpx messages are two
    lines, so this is not hypothetical."""
    for rendered in (clip(text, room), clip_left(text, room),
                     shorten_path(text, room)):
        assert _printable(rendered), repr(rendered)


@given(TEXT, TEXT, ROOM, st.sampled_from(("left", "right")))
def test_pad_lets_no_control_character_through(left, right, room, keep):
    assert _printable(pad(left, right, room, keep=keep))


# =============================================================================
# What fits is not touched
# =============================================================================

@given(TEXT, ROOM)
def test_a_string_that_fits_is_returned_whole(text, room):
    assume(_printable(text) and cells(text) <= room)

    assert clip(text, room) == text
    assert clip_left(text, room) == text
    assert shorten_path(text, room) == text


@given(_text_and_a_room_that_cuts_it())
def test_what_is_cut_is_marked_as_cut(case):
    """A clipped string that does not say it was clipped is a string the
    reader takes for the whole value."""
    text, room = case

    assert clip(text, room).endswith(ELLIPSIS)
    assert clip_left(text, room).startswith(ELLIPSIS)


# =============================================================================
# Which end is kept
# =============================================================================

@given(PRINTABLE, st.integers(min_value=2, max_value=60))
def test_clip_keeps_the_beginning_and_clip_left_the_end(text, room):
    """`/data/corpus/tei_out` cut to `/data/corpu…` has lost the only
    part that says which run this was."""
    kept = clip(text, room).removesuffix(ELLIPSIS)
    assert text.startswith(kept)

    tail = clip_left(text, room).removeprefix(ELLIPSIS)
    assert text.endswith(tail)


@given(_a_path_and_room_for_its_leaf())
def test_a_path_keeps_its_leaf_whenever_the_leaf_fits(case):
    """The defect this function exists for: `-o /data/grand-siecle/
    tei_output` became `/data/grand-sie…` on the panel's top line, which
    names no run at all."""
    path, leaf, room = case
    assume(cells(path) > room)

    assert shorten_path(path, room).endswith("/" + leaf)


# =============================================================================
# `pad`: which half survives whole
# =============================================================================

@given(PRINTABLE, PRINTABLE, ROOM)
def test_pad_keeps_the_left_whole_when_asked(left, right, room):
    """In the summary the left holds a label and a COUNT, and
    `11 of 1…` is not a short number, it is a wrong one."""
    assume(cells(left) <= room)

    assert left in pad(left, right, room, keep="left")


@given(PRINTABLE, CARRYING, ROOM)
def test_pad_keeps_the_right_whole_when_asked(left, right, room):
    """On the panel the right holds `elapsed 1:12:40`, `47 %`, `9.1
    pages/s` — short fixed fields carrying the news."""
    assume(cells(right) + 2 <= room)

    assert pad(left, right, room, keep="right").endswith(right)


@given(PRINTABLE.map(str.strip), PRINTABLE.map(str.strip), ROOM,
       st.sampled_from(("left", "right")))
def test_pad_never_ends_in_a_space(left, right, room, keep):
    """The same string goes into a log file, where a trailing space is
    noise — and into an issue, where it is noise someone has to strip.
    `pad`'s own docstring says "no separator without something after
    it", and said it about one of its three branches: an empty right
    half took the first, and `pad("label", "", 20)` returned the label
    and fifteen spaces, and a right half made only of spaces produced a
    line that was entirely spaces. Both found by this file, one on its
    first run and the other on the run after the first fix.

    The halves are stripped by the strategy: the property is that `pad`
    adds no trailing space, not that it removes one a caller passed in.
    """
    said = pad(left, right, room, keep=keep)

    assert said == said.rstrip(" "), repr(said)


@given(_two_halves_and_room_for_both(), st.sampled_from(("left", "right")))
@settings(max_examples=400)
def test_pad_puts_the_two_halves_in_that_order(case, keep):
    """Whatever is kept of each half, the left half is on the left."""
    left, right, room = case

    said = pad(left, right, room, keep=keep)

    assert said.index(left) < said.rindex(right)
