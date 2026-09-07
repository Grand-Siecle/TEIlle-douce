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


def test_a_file_with_no_page_number_is_named_even_when_it_is_alone(tmp_path):
    """The guide's third troubleshooting entry. `order_files` places such
    a file last and gives it the sentinel page number, so it takes the
    sentinel IIIF view — and one is enough for that, which is why this
    family is not folded into the duplicate-number one, whose smallest
    case is two."""
    ocr = a_corpus(tmp_path)
    pages = ocr / "LIV9001_reconciled" / "content" / "data" / "doc_1"
    (pages / "plate.xml").write_text(
        (pages / "f1.xml").read_text(encoding="utf-8"), encoding="utf-8")

    answer = inspect(settings_for(tmp_path, ocr), probe=False)

    named = [item for item in answer.attention if "no page number" in item.detail]
    assert [item.subject for item in named] == ["LIV9001_reconciled"]
    assert "plate.xml" in named[0].detail
    assert "sentinel" in named[0].consequence


def test_the_analyses_do_not_log_over_the_report_they_answer(tmp_path, caplog):
    """`find_metadata_row` says so when it finds nothing and `order_files`
    warns once per digit-less file: both are right during a run, and
    during a preflight they are the same diagnosis, said twice, above the
    report that is about to say it in its own words — and on stderr,
    where it lands in the middle of the rendering."""
    import logging

    ocr = a_corpus(tmp_path, ("LIV0326_v1_reconciled",))
    pages = ocr / "LIV0326_v1_reconciled" / "content" / "data" / "doc_1"
    (pages / "plate.xml").write_text(
        (pages / "f1.xml").read_text(encoding="utf-8"), encoding="utf-8")

    with caplog.at_level(logging.WARNING):
        answer = inspect(settings_for(tmp_path, ocr), probe=False)

    assert answer.attention, "the fixture no longer poses the diagnoses"
    assert not [record for record in caplog.records
                if record.name.startswith(("teille_douce.metadata",
                                           "teille_douce.utils.files"))]


def test_the_level_the_analyses_silenced_is_put_back(tmp_path):
    """It is set on the module's logger, not on a handler: leaving it at
    CRITICAL would silence the run that follows in the same process."""
    import logging

    ocr = a_corpus(tmp_path)
    before = logging.getLogger("teille_douce.utils.files").level

    inspect(settings_for(tmp_path, ocr), probe=False)

    assert logging.getLogger("teille_douce.utils.files").level == before


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
    wrong for three rounds running.

    Against the renderer's own `room`, and from two columns up. It swept
    `range(40, 141)` and compared to `min(width, MAX_WIDTH)`, which is
    two cells looser than the contract and starts above the widths where
    the only unclipped line in this file lived: `  needs attention` is
    seventeen cells and was appended as a literal, so it overran every
    terminal under nineteen columns. A sweep that starts above the
    defect is the first of the five hollow shapes.
    """
    wide = an_answer(
        input_dir=Path("/home/rayondemiel/univ_geneve/corpus/grand-siecle"),
        attention=tuple(
            Attention(name, "no catalogue row for " + name, "placeholders")
            for name in ("A", "LIV0326_v1_reconciled",
                         "BDD_1685_Felibien_Entretiens_tome_II")))

    for width in range(2, 141):
        room = min(width, command.MAX_WIDTH) - command._MARGIN
        for strict in (False, True):
            for line in command.render(wide, width=width, strict=strict):
                if "teille-douce " in line and "--strict" in line:
                    # The one exemption, as in `report/summary.py`: a
                    # command is printed whole or not at all, since
                    # `check -i /srv/oc…` is not a shorter command but
                    # one that does not run.
                    continue
                assert cells(line) <= room, (width, cells(line), room, line)
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


def test_a_path_that_cannot_be_read_names_the_layer_that_supplied_it(tmp_path):
    """The line said `-i /srv/ocr is not there` whatever had set the
    value, so it named a flag the operator had not typed. It now names
    the layer, as `settings.unreadable_inputs` reports it.

    Which layer it IS is decided in `settings._named_by` and guarded
    there; what this fixes is that the renderer passes it through whole
    rather than reaching into it.
    """
    answer = an_answer(unusable=(("ocr_dir", Path("/srv/ocr"), "is not there",
                                  "TDOUCE_OCR_DIR"),))

    said, = [line for line in command.render(answer, width=92)
             if "/srv/ocr" in line]

    assert "TDOUCE_OCR_DIR" in said
    assert " -i " not in said


def test_the_footer_says_what_strict_would_do():
    said = "\n".join(command.render(an_answer(), width=92))
    assert "--strict" in said
    assert "nothing to report" in command.render(
        an_answer(attention=()), width=92)[-1]


def test_the_command_the_footer_offers_means_this_check():
    """It said `teille-douce check --strict` and nothing else, so pasted
    from another working directory it answered "unusable — nothing to
    convert" about a corpus it had never been shown: a last line that
    exits 3 is a last line that teaches distrust, which is the sentence
    `summary._where` was written for one file over."""
    elsewhere = an_answer(
        input_dir=Path("/srv/ocr in"), output_dir=Path("/srv/tei out"),
        catalogue={"path": Path("/srv/cat alogue.csv"), "rows": 1,
                   "matched": 1, "unmatched": []},
        persons={"path": Path("/srv/per sons.csv"), "loaded": True,
                 "count": 1})

    offered, = [line for line in command.render(elsewhere, width=120)
                if "teille-douce check" in line]

    assert "-i '/srv/ocr in'" in offered
    assert "-o '/srv/tei out'" in offered
    assert "--strict" in offered

    # And it parses. `-i` on `report` — which has none — was an
    # `unrecognized arguments` usage error in the last line of a run,
    # and asserting flags by name is what let it through.
    import shlex

    from teille_douce.cli.app import build_parser, normalise

    words = shlex.split(offered.strip())
    parsed = build_parser().parse_args(normalise(words[1:]))
    assert parsed.command == "check" and parsed.strict is True

    # And the defaults are not repeated back: `check` resolves them.
    at_home, = [line for line in command.render(an_answer(), width=92)
                if "teille-douce check" in line]
    assert " -i " not in at_home and " -o " not in at_home
    assert "--metadata" not in at_home and "--persons" not in at_home
    # The catalogues this check was given, or it answers about others.
    assert "--metadata '/srv/cat alogue.csv'" in offered
    assert "--persons '/srv/per sons.csv'" in offered


def test_an_output_that_cannot_be_written_is_unusable_not_degraded():
    assert an_answer(output_writable=False).verdict == 3
    assert an_answer(volumes=()).verdict == 3
    assert an_answer(attention=()).verdict == 0


def test_the_preflight_can_be_asked_about_the_input_a_run_would_be_given(
        tmp_path, capsys):
    """`teille-douce run -i OCR_test` is the shape the guide teaches, and
    `check -i OCR_test` used to answer `unrecognized arguments` — so the
    only way to point the preflight anywhere was the environment, which
    is not how anyone asks the question."""
    from teille_douce.cli.app import build_parser, settings_from

    ocr = a_corpus(tmp_path)
    parsed = build_parser().parse_args(
        ["check", "-i", str(ocr), "-o", str(tmp_path / "out"),
         "--metadata", str(tmp_path / "metadata_livre.csv"),
         "--persons", str(tmp_path / "metadata_personne.csv"), "--no-probe"])
    settings = settings_from(parsed, env={}, parser=build_parser())

    assert settings.ocr_dir == ocr
    assert settings.origin("ocr_dir") == "flag"
    assert inspect(settings, probe=False).volumes


def test_a_volume_that_cannot_be_listed_is_not_a_volume_with_no_alto(tmp_path):
    """`rglob` swallows a permission error and yields nothing, so an
    unreadable volume is indistinguishable from an empty one by its
    contents alone — and calling it empty sends the operator to repack a
    volume whose only problem is its mode. `cli/run.py` has said this
    since PR #33 and has `_is_readable` for it; the preflight that must
    not disagree with the run did not use it."""
    import os
    import stat

    ocr = a_corpus(tmp_path, ("LIV9001_reconciled", "LIV0326_v1_reconciled"))
    os.chmod(ocr / "LIV0326_v1_reconciled", 0o000)
    if os.access(ocr / "LIV0326_v1_reconciled", os.R_OK):
        os.chmod(ocr / "LIV0326_v1_reconciled", stat.S_IRWXU)
        pytest.skip("this user can read a directory with mode 000")
    try:
        answer = inspect(settings_for(tmp_path, ocr), probe=False)
    finally:
        os.chmod(ocr / "LIV0326_v1_reconciled", stat.S_IRWXU)

    named = [item for item in answer.attention
             if item.subject == "LIV0326_v1_reconciled"]
    assert any("cannot be listed" in item.detail for item in named)
    assert not any("holds no ALTO" in item.detail for item in named)


def test_an_input_directory_that_cannot_be_listed_does_not_raise(tmp_path):
    """`inspect` walked the corpus before asking `unreadable_inputs()`,
    so `check` on a chmod-000 `OCR/` came out as a traceback with exit 1
    — "some volumes failed", from a command that converted nothing.
    `run` asks that question first; the ordering had been applied to one
    of the two callers."""
    import os
    import stat

    shut = tmp_path / "shut"
    shut.mkdir()
    os.chmod(shut, 0o000)
    if os.access(shut, os.R_OK):
        os.chmod(shut, stat.S_IRWXU)
        pytest.skip("this user can read a directory with mode 000")
    try:
        answer = inspect(settings_for(tmp_path, shut), probe=False)
    finally:
        os.chmod(shut, stat.S_IRWXU)

    assert answer.verdict == 3
    assert answer.unusable
    assert "not readable" in command.render(answer, width=92)[-1]


def test_a_catalogue_that_is_a_fifo_is_refused_rather_than_read(tmp_path):
    """`inspect` read the catalogues before asking which paths this run
    could read, so `check --metadata <fifo>` blocked in pandas waiting
    for a writer that never came — the command never returned and
    nothing reached the screen. `run` asks first; the ordering had been
    fixed for the corpus and not for the two catalogues, one line down.

    In a subprocess with a timeout: a regression HANGS, and a suite that
    hangs says nothing.
    """
    import os
    import subprocess
    import sys

    ocr = a_corpus(tmp_path)
    blocking = tmp_path / "cat.csv"
    os.mkfifo(blocking)

    finished = subprocess.run(
        [sys.executable, "-c",
         "import sys;"
         "from teille_douce.preflight import inspect;"
         "from teille_douce.settings import Settings;"
         f"s = Settings.load(env={{}}, flags={{'ocr_dir': {str(ocr)!r},"
         f" 'output_dir': {str(tmp_path / 'out')!r},"
         f" 'metadata_csv': {str(blocking)!r},"
         " 'enrich': False, 'modernize': False, 'ner': False});"
         "answer = inspect(s, probe=False);"
         "print(answer.verdict);"
         "print([name for name, _, _, _ in answer.unusable])"],
        capture_output=True, text=True, timeout=60,
        cwd=str(Path(__file__).resolve().parent.parent))

    assert finished.returncode == 0, finished.stderr
    verdict, refused = finished.stdout.splitlines()
    assert verdict == "3"
    assert "metadata_csv" in refused


def test_a_page_that_is_a_fifo_is_not_counted_as_a_page(tmp_path):
    """A FIFO named `f2.xml` blocks `etree.parse` in the run's main
    process, so counting it here made `check` answer "usable · N pages"
    about a corpus the run cannot finish — a preflight disagreeing with
    its run, which this module says is worse than none."""
    import os

    ocr = a_corpus(tmp_path)
    os.mkfifo(ocr / "LIV9001_reconciled" / "content" / "data" / "doc_1"
              / "zzz.xml")

    answer = inspect(settings_for(tmp_path, ocr), probe=False)

    assert answer.pages == 8, "the FIFO was counted as a ninth page"
