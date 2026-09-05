# -----------------------------------------------------------
# Seventy-eight identical warnings are one fact, not seventy-eight.
#
# A real pipeline_*.log for this corpus carries eighty WARNING lines in
# five raw shapes; printed one by one they push everything else off the
# screen, which is how a run comes to look like it is failing when it is
# repairing. The digest folds them by shape and keeps every occurrence
# counted — nothing is dropped, only the number of LINES is capped, and
# the DEBUG file handler still receives all of it.
#
# Run: venv/bin/python -m pytest tests/test_warning_digest.py -q
# -----------------------------------------------------------
import pytest

from teille_douce.report.digest import WarningDigest, shape_of


# =============================================================================
# The shape of a message
# =============================================================================

def test_digit_runs_become_a_placeholder():
    """`page 41: 19 duplicate` and `page 88: 7 duplicate` are one fact
    about the corpus, and two lines on the screen."""
    assert shape_of("page 41: 19 duplicate ALTO id(s) disambiguated") \
        == "page N: N duplicate ALTO id(s) disambiguated"


def test_the_document_prefix_is_removed():
    """It is already on the volume's own line; repeating it in every
    warning is what makes four volumes look like eighty problems."""
    assert shape_of("LIV0038_reconciled: page 41 skipped, ALTO unusable") \
        == "page N skipped, ALTO unusable"


def test_a_message_with_nothing_variable_is_its_own_shape():
    assert shape_of("PyHellen unreachable") == "PyHellen unreachable"


def test_a_path_keeps_its_shape_without_its_numbers():
    assert shape_of("cannot read OCR/LIV0044/f284-259-0285.xml") \
        == "cannot read OCR/LIVN/fN-N-N.xml"


# =============================================================================
# Folding, and what the fold has to preserve
# =============================================================================

def test_the_four_real_shapes_of_the_duplicate_warning_fold_into_one_line():
    """The measured case: four volumes, seventy-eight occurrences, and the
    integers inside the message summed rather than thrown away."""
    digest = WarningDigest()
    occurrences = [
        ("LIV0038_reconciled", "page 41: 19 duplicate ALTO id(s) disambiguated"),
        ("LIV0038_reconciled", "page 88: 103 duplicate ALTO id(s) disambiguated"),
        ("LIV0039a", "page 2: 7 duplicate ALTO id(s) disambiguated"),
        ("LIV0040", "page 12: 4000 duplicate ALTO id(s) disambiguated"),
    ]
    for document, message in occurrences:
        digest.add(document, message)
    for _ in range(74):
        digest.add("LIV0041", "page 5: 0 duplicate ALTO id(s) disambiguated")

    line, = digest.lines()

    assert line.shape == "page N: N duplicate ALTO id(s) disambiguated"
    assert line.occurrences == 78
    assert line.documents == 4
    # The spec's measured figure: the second integer of every message,
    # summed. 19 + 103 + 7 + 4000 = 4 129 ids actually disambiguated.
    assert line.totals[1] == 4129


def test_the_integers_in_a_folded_message_are_summed_not_lost():
    """`4 129 ids` is the reason the fold is not a loss of information."""
    digest = WarningDigest()
    digest.add("D1", "page 41: 19 duplicate ALTO id(s) disambiguated")
    digest.add("D1", "page 88: 103 duplicate ALTO id(s) disambiguated")

    line, = digest.lines()

    # 41 + 19 + 88 + 103 — every integer the messages carried.
    assert line.totals == [129, 122]


def test_two_shapes_stay_two_lines():
    digest = WarningDigest()
    digest.add("D1", "page 41: 19 duplicate ALTO id(s) disambiguated")
    digest.add("D1", "page 41 skipped, ALTO unusable")

    assert len(digest.lines()) == 2


def test_the_busiest_shape_is_listed_first():
    """The panel has room for a few lines; they should be the ones that
    are actually happening."""
    digest = WarningDigest()
    digest.add("D1", "rare thing")
    for _ in range(9):
        digest.add("D1", "page 1: 1 duplicate ALTO id(s) disambiguated")

    assert digest.lines()[0].occurrences == 9


# =============================================================================
# Nothing is thrown away, and the panel says so when it elides
# =============================================================================

def test_occurrences_are_never_capped():
    """Only the number of lines is bounded. Capping occurrences would
    turn a digest into a lie about how much happened."""
    digest = WarningDigest()
    for index in range(5000):
        digest.add("D1", f"page {index}: 1 duplicate ALTO id(s) disambiguated")

    line, = digest.lines()

    assert line.occurrences == 5000


def test_the_lines_are_capped_and_the_elision_is_stated():
    """The failure mode to watch: a warning whose variable part is
    alphabetic — a language code, an entity type — fragments into
    single-occurrence shapes. The cap keeps the panel readable; saying so
    keeps it honest."""
    digest = WarningDigest()
    for language in ("fra", "lat", "grc", "ita", "deu", "nld", "eng"):
        digest.add("D1", f"no model for {language}")

    lines = digest.lines(limit=3)

    assert len(lines) == 3
    assert digest.elided(limit=3) == 4


def test_nothing_is_elided_when_everything_fits():
    digest = WarningDigest()
    digest.add("D1", "one thing")

    assert digest.elided(limit=3) == 0


def test_an_empty_digest_renders_no_zone_at_all():
    """A "repeated warnings" heading over nothing would be worse than the
    silence it replaces."""
    assert WarningDigest().lines() == ()


# =============================================================================
# The digest is what the panel reads; the log still gets everything
# =============================================================================

def test_the_digest_keeps_one_verbatim_example_per_shape():
    """`-v` diffuses them unfolded, and a reader who wants the real
    message should not have to open the log for it."""
    digest = WarningDigest()
    digest.add("LIV0038_reconciled", "page 41: 19 duplicate ALTO id(s) disambiguated")
    digest.add("LIV0039a", "page 88: 103 duplicate ALTO id(s) disambiguated")

    line, = digest.lines()

    assert line.example == "page 41: 19 duplicate ALTO id(s) disambiguated"
    assert line.first_document == "LIV0038_reconciled"
