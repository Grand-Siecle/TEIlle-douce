"""Putting a string on a line of a terminal, safely.

Both views need this and both got it wrong in the same two ways, so it
lives in one place: the panel measured in cells and the summary measured
in `len()`, and neither the summary's `FAILED` lines nor its two-column
asides removed control characters — an httpx message is two lines, and
one of them then landed unindented and unclipped in the middle of the
report.

Nothing here knows what a run is. It is arithmetic on text.
"""

import re
import unicodedata

# Everything a terminal would act on rather than print: a newline in a
# clipped string renders as a second line the width contract never saw.
_CONTROL = re.compile(r"[\x00-\x1f\x7f]")

ELLIPSIS = "…"


def cells(text):
    """Terminal columns, not characters.

    A CJK glyph occupies two columns for one `len()`, so a width contract
    measured in characters lets the line run off the edge of the very
    terminals it was written for.
    """
    return sum(2 if unicodedata.east_asian_width(c) in "WF" else 1
               for c in text)


def clip(text, room):
    """`text`, safe to put on one line of `room` columns.

    Control characters go first: clipping by length keeps a newline, so
    the panel renders one line and the terminal shows two — the second
    unindented and unclipped. httpx error messages are two lines, so this
    is not hypothetical.
    """
    text = _CONTROL.sub(" ", text)
    if room <= 0:
        return ""
    if cells(text) <= room:
        return text
    kept, used = [], 0
    for char in text:
        width = cells(char)
        if used + width > room - 1:
            break
        kept.append(char)
        used += width
    return "".join(kept) + ELLIPSIS


def clip_left(text, room):
    """`text`, cut at the START — for a value whose END identifies it.

    A path and a log name are read from the right: `/data/corpus/tei_out`
    cut to `/data/corpu…` has lost the only part that says which run this
    was, while `…rpus/tei_out` still answers the question.
    """
    text = _CONTROL.sub(" ", text)
    if room <= 0:
        return ""
    if cells(text) <= room:
        return text
    kept, used = [], 0
    for char in reversed(text):
        width = cells(char)
        if used + width > room - 1:
            break
        kept.append(char)
        used += width
    return ELLIPSIS + "".join(reversed(kept))


def shorten_path(path, room):
    """A path that still names its leaf.

    Clipping a path from the right drops exactly the directory the
    operator is looking for — `-o /data/grand-siecle/tei_output` became
    `/data/grand-sie…` on the panel's top line. The leaf is kept whole
    whenever it fits at all, and the middle is what goes.
    """
    path = _CONTROL.sub(" ", str(path))
    if room <= 0:
        return ""
    if cells(path) <= room:
        return path
    head, slash, leaf = path.rpartition("/")
    if not slash or cells(leaf) + 2 >= room:
        # No leaf worth protecting, or a leaf that would fill the line on
        # its own: fall back to keeping the end, which is still the half
        # that identifies it.
        return clip_left(path, room)
    return clip(head, room - cells(leaf) - 1) + "/" + leaf


def pad(left, right, room, keep="right"):
    """`left` flush left, `right` flush right, one line, never wider.

    `keep` names the half that survives whole; the other is CLIPPED, and
    never dropped. Both halves are load-bearing everywhere this is used,
    and which one is more so depends on the caller — which is why it is
    an argument and not a rule.

    On the panel the right column wins: it holds `elapsed 1:12:40`, `47%`,
    `9.1 pages/s` — short fixed fields carrying the news, against an
    elastic document name or path. Clipping the composed line, which is
    what this used to do, dropped that column entirely as soon as the
    left grew, and an absolute `-o` path took the elapsed time and the
    estimate off the panel on every frame.

    In the summary the left wins: it holds a label and a COUNT, and
    `11 of 1…` is not a short number, it is a wrong one.
    """
    left, right = _CONTROL.sub(" ", left), _CONTROL.sub(" ", right)
    gap = room - cells(left) - cells(right)
    if gap >= 1:
        return left + " " * gap + right
    if keep == "left":
        if cells(left) >= room:
            # No room for the right at all. The left is what survives,
            # whole if it fits exactly — `gap >= 1` above is about the
            # SPACE between the columns, and falling through on account
            # of it clipped a left that fitted.
            return left if cells(left) == room else clip(left, room)
        shortened = clip(right, room - cells(left) - 1)
        # No separator without something after it: one cell of room left
        # produced `left + " " + ""`, and the same string goes into a log
        # file where a trailing space is noise.
        return f"{left} {shortened}" if shortened else left
    if cells(right) + 2 > room:
        return clip(left + " " + right, room)
    return clip(left, room - cells(right) - 1) + " " + right
