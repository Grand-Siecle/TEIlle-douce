# -----------------------------------------------------------
# The one place that says what a line-break hyphen is.
# -----------------------------------------------------------
"""
Hyphenation predicates, shared by the phases that undo it.

Three phases meet a word split across two lines and each needs something
different from it (audit 4.7):

- ``enrichment/dehyphenation`` joins the spans AND keeps an offset map,
  because the tokens PyHellen returns have to be traced back to the raw
  characters they came from;
- ``modernize.dehyphenate_lines`` joins line texts within a zone and
  repeats the rejoined word on every line it crosses, because that is
  the context the API has to see;
- ``lang/detector`` deletes the hyphen without joining anything, because
  the language detector only needs a clean string.

They cannot be merged — they answer different questions — but they must
agree on WHAT a line-break hyphen is, and they did not. The alignment
phase stripped a whole RUN of marks in a fixed order, so "compte--" came
back one character shorter than the page and "ab¬-" kept its soft
hyphen; the extraction phase decided on its own, in three copies,
whether a span was hyphenated at all. Any fix to one left the others as
they were. This module holds the predicate; each phase keeps its own way
of acting on it.
"""

# The two characters an early-modern line break is marked with in this
# corpus. "¬" is what the OCR emits for the printed double hyphen; "-"
# is what a plain hyphen looks like at the end of a line, which is
# ambiguous with a genuine compound word and needs the next line to
# decide.
HYPHEN_CHARS = ("¬", "-")

# The mark that is never part of a word, whatever surrounds it: it exists
# only to say "this line ends mid-word". "-" is not one of these — it can
# be a genuine compound ("Saint-Germain"), which is why the two are told
# apart everywhere.
SOFT_HYPHEN = "¬"


def ends_with_hyphen(text):
    """True when *text* ends on a line-break hyphen, trailing spaces ignored.

    Every entry of HYPHEN_CHARS is ONE character: callers slice with
    ``[:-1]`` and test membership per character, so a two-character mark
    would be accepted here and mishandled three lines later.
    """
    return bool(text) and text.rstrip().endswith(HYPHEN_CHARS)


def strip_trailing_hyphen(text):
    """*text* without its trailing line-break hyphen.

    Trailing whitespace goes too — the hyphen is the last thing the line
    holds, and what follows it is the next line's business. A caller that
    needs the spaces must keep them itself.
    """
    stripped = text.rstrip()
    if stripped.endswith(HYPHEN_CHARS):
        return stripped[:-1]
    return stripped


def joins_words(before, after):
    """True when a hyphen between *before* and *after* splits one word.

    *before* is the text kept so far, *after* the text that follows the
    hyphen. A hyphen joins two halves of a word when a letter precedes it
    and a letter follows it: "souv¬ erain" is one word, "Paris - Lyon" is
    not, and neither is a hyphen at the very start or end of a passage.
    """
    return bool(before) and before[-1].isalpha() and bool(after) and after[0].isalpha()


def remove_soft_hyphens(text):
    """*text* without any "¬", wherever it sits.

    The soft hyphen is a mark of the printed line break and never part of
    a word: whatever a downstream service returns, dropping it is always
    the right repair.
    """
    return text.replace("¬", "") if text else text
