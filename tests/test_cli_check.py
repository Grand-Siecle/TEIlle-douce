# -----------------------------------------------------------
# The preflight: five families of attention, and what each would cost.
#
# The command that was missing. Four of the ten troubleshooting entries in
# the user guide are diagnoses this poses in two seconds, and the only way
# to pose them before was to convert the whole corpus — forty minutes to
# learn that a BDD column matches nothing.
#
# The rendering is a pure function of the answer, so every family is
# testable with no terminal and no corpus; the ANALYSES are driven against
# a real directory, because a preflight that disagreed with the run it
# precedes would be worse than none.
#
# Run: venv/bin/python -m pytest tests/test_cli_check.py -q
# -----------------------------------------------------------
from pathlib import Path

import pytest

from teille_douce.cli import app
from teille_douce.cli import check as command
from teille_douce.preflight import Attention, Preflight, Service, inspect
from teille_douce.report.text import cells

FIXTURES = Path(__file__).resolve().parent / "fixtures"
ALTO = FIXTURES / "alto_min" / "LIV9001_reconciled"


def a_corpus(tmp_path, volumes=("LIV9001_reconciled",)):
    """A corpus of copies of the one fixture volume, and its catalogues."""
    import shutil

    ocr = tmp_path / "ocr"
    ocr.mkdir()
    for name in volumes:
        shutil.copytree(ALTO, ocr / name)
    for csv in ("metadata_livre.csv", "metadata_personne.csv"):
        shutil.copy(FIXTURES / csv, tmp_path / csv)
    return ocr


def settings_for(tmp_path, ocr, **overrides):
    from teille_douce.settings import Settings

    return Settings.load(env={}, flags={
        "ocr_dir": ocr, "output_dir": tmp_path / "out",
        "metadata_csv": tmp_path / "metadata_livre.csv",
        "persons_csv": tmp_path / "metadata_personne.csv",
        "enrich": False, "modernize": False, "ner": False, **overrides})


# =============================================================================
# The five families, each against a real directory
# =============================================================================

def test_a_volume_with_no_catalogue_row_is_named(tmp_path):
    """The guide's first troubleshooting entry, and forty minutes of run
    to reach it: the header keeps every placeholder and the run exits 0."""
    ocr = a_corpus(tmp_path, ("LIV9001_reconciled", "LIV0326_v1_reconciled"))

    answer = inspect(settings_for(tmp_path, ocr), probe=False)

    named = [item for item in answer.attention if "catalogue row" in item.detail]
    assert [item.subject for item in named] == ["LIV0326_v1_reconciled"]
    assert "placeholder" in named[0].consequence


def test_two_files_claiming_one_page_number_are_named(tmp_path):
    """`f12.xml` and `f12-np.xml` both give 12. All the pages convert, and
    the numbering is ambiguous — which the run says at the end, per
    volume, and this says before."""
    import shutil

    ocr = a_corpus(tmp_path)
    pages = ocr / "LIV9001_reconciled" / "content" / "data" / "doc_1"
    shutil.copy(pages / "f2.xml", pages / "f2-np.xml")

    answer = inspect(settings_for(tmp_path, ocr), probe=False)

    named = [item for item in answer.attention if "claim page" in item.detail]
    assert named and "f2-np.xml" in named[0].detail
    assert "ambiguous" in named[0].consequence


def test_a_iiif_mapping_under_the_threshold_is_named(tmp_path):
    """It is refused silently: the volume converts with no @source at all,
    and an operator who supplied a mapping has no way to learn it was
    thrown away."""
    ocr = a_corpus(tmp_path)
    volume = ocr / "LIV9001_reconciled"
    (volume / "gallica-bnf-fr-iiif-manifest-json.csv").unlink()
    (volume / "gallica-iiif.csv").write_text(
        "http://x/a,b,nothing1.xml\nhttp://x/b,b,nothing2.xml\n",
        encoding="utf-8")

    answer = inspect(settings_for(tmp_path, ocr), probe=False)

    named = [item for item in answer.attention if "IIIF" in item.detail]
    assert named and "0%" in named[0].detail
    assert "no @source" in named[0].consequence


def test_a_directory_with_no_alto_is_named(tmp_path):
    ocr = a_corpus(tmp_path)
    (ocr / "LIV0099_empty").mkdir()

    answer = inspect(settings_for(tmp_path, ocr), probe=False)

    assert any(item.subject == "LIV0099_empty" for item in answer.attention)


def test_a_persons_table_that_did_not_load_is_named(tmp_path):
    ocr = a_corpus(tmp_path)
    (tmp_path / "metadata_personne.csv").write_text("", encoding="utf-8")

    answer = inspect(settings_for(tmp_path, ocr), probe=False)

    assert any("persons table" in item.detail for item in answer.attention)


# =============================================================================
# The verdict, and what --strict changes
# =============================================================================

def test_an_imperfect_corpus_is_usable_and_says_so(tmp_path):
    """The decision this command turns on: on seventeenth-century OCR
    imperfection is the normal state, and a preflight that exited
    non-zero on the normal state would be run with `|| true` within a
    week and stop being read."""
    ocr = a_corpus(tmp_path, ("LIV9001_reconciled", "LIV0326_v1_reconciled"))
    args = app.build_parser().parse_args(["check"])
    _use(tmp_path, ocr)

    assert command.execute(args) == 0


def test_strict_turns_every_one_of_them_into_a_failure(tmp_path):
    ocr = a_corpus(tmp_path, ("LIV9001_reconciled", "LIV0326_v1_reconciled"))
    args = app.build_parser().parse_args(["check", "--strict"])
    _use(tmp_path, ocr)

    assert command.execute(args) == 1


def test_a_clean_corpus_is_zero_under_strict_too(tmp_path):
    ocr = a_corpus(tmp_path)
    _use(tmp_path, ocr)

    assert command.execute(app.build_parser().parse_args(
        ["check", "--strict"])) == 0


def test_an_input_that_is_not_there_is_unusable(tmp_path):
    _use(tmp_path, tmp_path / "absent")

    assert command.execute(app.build_parser().parse_args(["check"])) == 3


def _use(tmp_path, ocr):
    from teille_douce.settings import set_settings

    set_settings(settings_for(tmp_path, ocr))


# =============================================================================
# The rendering, as a pure function
# =============================================================================

def an_answer(**overrides):
    base = dict(
        input_dir=Path("OCR"), output_dir=Path("tei_output"),
        volumes=(("LIV0038_reconciled", 754), ("LIV0039a_reconciled", 850)),
        archives=1, already_converted=24, output_writable=True,
        catalogue={"path": Path("metadata_livre.csv"), "rows": 27,
                   "matched": 26, "unmatched": ["LIV0326"]},
        persons={"path": Path("metadata_personne.csv"), "loaded": True,
                 "count": 3412},
        services=(Service("PyHellen", "enrichment", "up"),
                  Service("VieuxParler", "modernization", "refused")),
        attention=(Attention("LIV0326", "no catalogue row", "placeholders"),),
    )
    base.update(overrides)
    return Preflight(**base)


def test_every_line_fits_the_terminal_it_was_given():
    """The rule the report package spent fourteen review rounds on, and
    the reason this renderer borrows its `pad`: a subject wider than its
    column runs into its own detail, which is where `summary._entry` was
    wrong for three rounds running."""
    wide = an_answer(
        input_dir=Path("/home/rayondemiel/univ_geneve/corpus/grand-siecle"),
        attention=tuple(
            Attention(name, "no catalogue row for " + name, "placeholders")
            for name in ("A", "LIV0326_v1_reconciled",
                         "BDD_1685_Felibien_Entretiens_tome_II")))

    for width in range(40, 141):
        for line in command.render(wide, width=width):
            assert cells(line) <= min(width, command.MAX_WIDTH), (width, line)
            assert line == line.rstrip(), (width, repr(line))


def test_a_subject_never_runs_into_its_own_detail():
    wide = an_answer(attention=(
        Attention("LIV0326_v1_reconciled", "no catalogue row", "placeholders"),
        Attention("A", "no catalogue row", "placeholders")))

    lines = command.render(wide, width=92)
    said = [line for line in lines if "no catalogue row" in line]

    assert len(said) == 2
    for line in said:
        assert "  no catalogue row" in line, line


def test_the_footer_says_what_strict_would_do():
    assert "--strict" in command.render(an_answer(), width=92)[-1]
    assert "nothing to report" in command.render(
        an_answer(attention=()), width=92)[-1]


def test_an_output_that_cannot_be_written_is_unusable_not_degraded():
    assert an_answer(output_writable=False).verdict == 3
    assert an_answer(volumes=()).verdict == 3
    assert an_answer(attention=()).verdict == 0
