# -----------------------------------------------------------
# `teille-douce report`: coming back to a run that is over.
#
# It reads what `report/store.py` wrote and touches no corpus, so every
# shape here is a directory written by hand — including the shapes a real
# run produces only when it goes wrong: a manifest that was never written,
# a JSONL whose last line was cut in half by a Ctrl-C.
#
# What it can answer is bounded by what the run indexed. That bound is the
# subject of half these tests: an exhaustive-looking report of a block
# nobody indexed would be read as "the run lost nothing that way".
#
# Run: venv/bin/python -m pytest tests/test_cli_report.py -q
# -----------------------------------------------------------
import json

import pytest

from teille_douce.cli import app
from teille_douce.cli import report as command
from teille_douce.report.store import RUNS, read_run
from teille_douce.report.text import cells

INCIDENTS = [
    {"code": "phase_lost", "document": "LIV0044_reconciled", "step": "enrich",
     "locator": "LIV0044_reconciled", "kind": "doc", "count": 148,
     "total": 148, "detail": "PyHellen stopped answering"},
    {"code": "container_failed", "document": "LIV0044_reconciled",
     "step": "modernize", "locator": "LIV0044_reconciled/f12", "kind": "page",
     "count": 3, "total": 148, "detail": "the batch raised"},
    {"code": "document_failed", "document": "LIV0326_v1_reconciled",
     "step": "sourcedoc", "locator": "LIV0326_v1_reconciled", "kind": "doc",
     "count": 1, "total": 1, "detail": "two files claim one surface id"},
]


def a_run(tmp_path, stamp="20260903-180824-0031415", incidents=INCIDENTS,
          documents=None, exit_code=1, log="", manifest=True):
    """One run directory, as `RunStore` would have left it."""
    where = tmp_path / RUNS / stamp
    where.mkdir(parents=True)
    if manifest:
        (where / "run.json").write_text(json.dumps({
            "argv": ["teille-douce", "run"],
            "settings": {},
            "documents": documents if documents is not None else {
                "LIV0044_reconciled": "ok",
                "LIV0326_v1_reconciled": "failed"},
            "exit_code": exit_code}), encoding="utf-8")
    if incidents:
        (where / "incidents.jsonl").write_text(
            "".join(json.dumps(entry) + "\n" for entry in incidents),
            encoding="utf-8")
    if log:
        (where / "pipeline.log").write_text(log, encoding="utf-8")
    return where


def parse(argv):
    return app.build_parser().parse_args(["report", *argv])


def selected(where, **selectors):
    return command.select(read_run(where), **selectors)


# =============================================================================
# Which run, and what the selectors leave
# =============================================================================

def test_the_last_run_is_the_one_reported(tmp_path):
    a_run(tmp_path, stamp="20260903-180824-0031415")
    a_run(tmp_path, stamp="20260904-090000-0031415")

    chosen = command._chosen(tmp_path, None)

    assert chosen.name == "20260904-090000-0031415"


def test_a_run_can_be_named_by_a_prefix_without_its_pid(tmp_path):
    """The pid is in the name because two runs start inside one second.
    Nobody types it."""
    a_run(tmp_path, stamp="20260903-180824-0031415")
    a_run(tmp_path, stamp="20260904-090000-0031415")

    assert command._chosen(tmp_path, "20260903").name.startswith("20260903")
    assert command._chosen(tmp_path, "20260905") is None


def test_the_selectors_compose(tmp_path):
    where = a_run(tmp_path)

    both = selected(where, document="LIV0044_reconciled", code="container")

    assert [item.index for item in both.incidents] == ["I2"]


def test_a_code_may_be_given_as_a_prefix(tmp_path):
    """`--code document` reaching `document_failed` is the point: nobody
    remembers the exact spelling of fourteen closed codes."""
    where = a_run(tmp_path)

    assert [item.code for item in selected(where, code="doc").incidents] == [
        "document_failed"]
    assert selected(where, code="phase_lost").incidents


def test_an_incident_keeps_the_number_the_report_gave_it(tmp_path):
    """`--why I3` has to name the same line tomorrow. The file is
    append-only, so its position is stable."""
    where = a_run(tmp_path)

    assert [item.index for item in read_run(where).incidents] == \
        ["I1", "I2", "I3"]
    assert selected(where, why="I3").why.code == "document_failed"


# =============================================================================
# What is NOT in the record
# =============================================================================

def test_a_block_that_is_not_indexed_says_so_rather_than_nothing(tmp_path):
    """`store.incident` writes block 3 alone — an index of everything is
    an index of nothing. Reporting "no incident" for block 1 would be
    read as "the run lost nothing that way", which is the one sentence
    this package exists to make impossible."""
    where = a_run(tmp_path, log="a line\n")

    printed = "\n".join(command.render(selected(where, block="source")))

    assert "not indexed" in printed
    assert "no incident" not in printed
    assert "pipeline.log" in printed


def test_a_block_that_is_not_indexed_is_empty_in_the_json_too(tmp_path):
    """The prose says "not indexed" in a sentence. JSON has no sentence
    to say it in, so it must neither hand a wrapper block-3 incidents
    under the name of block 1, nor let an empty list read as "the run
    lost nothing that way"."""
    where = a_run(tmp_path)

    document = command._as_json(selected(where, block="source"))

    assert document["incidents"] == []
    assert document["indexed"] is False
    assert document["block"] == "source"
    assert command._as_json(selected(where))["indexed"] is True


def test_the_limits_tell_impossible_apart_from_not_yet():
    """The distinction is the whole value of the list: impossible is a
    fact about the method, not yet is a decision nobody has taken."""
    kinds = {kind for kind, _, _, _ in command.LIMITS}
    assert kinds == {"impossible", "not yet"}

    for kind, subject, why, price in command.LIMITS:
        if kind == "not yet":
            # A limit with no price is an excuse.
            assert price and price != "none", subject
        assert why.strip().endswith("."), subject

    printed = "\n".join(command.render_limits())
    assert "impossible" in printed and "not yet" in printed
    assert "price" in printed


def test_a_manifest_that_was_never_written_does_not_lose_the_incidents(
        tmp_path):
    """A report of a run that went wrong is exactly where a half-written
    record turns up: `run.json` is written at the end, `incidents.jsonl`
    as it goes."""
    where = a_run(tmp_path, manifest=False)

    run = read_run(where)

    assert len(run.incidents) == 3
    assert run.unreadable and run.unreadable[0][0] == "run.json"
    assert "unreadable" in "\n".join(command.render(command.select(run)))


def test_a_jsonl_line_cut_in_half_keeps_everything_above_it(tmp_path):
    """The format is append-only and flushed as it happens, so that a
    Ctrl-C in the fourth hour keeps what came before. Reading it must
    hold that end of the bargain."""
    where = a_run(tmp_path)
    with open(where / "incidents.jsonl", "a", encoding="utf-8") as handle:
        handle.write('{"code": "phase_lo')

    run = read_run(where)

    assert len(run.incidents) == 3
    assert any("incidents.jsonl:4" in name for name, _ in run.unreadable)


# =============================================================================
# One incident, in full
# =============================================================================

def test_why_prints_everything_the_record_holds(tmp_path):
    where = a_run(tmp_path)

    printed = "\n".join(command.render(selected(where, why="I2")))

    for said in ("container_failed", "modernize", "LIV0044_reconciled/f12",
                 "3 of 148", "the batch raised"):
        assert said in printed


def test_a_locator_that_is_the_document_is_not_printed_twice(tmp_path):
    """A `doc`-kind locator IS the document. Under two labels it reads as
    two facts."""
    where = a_run(tmp_path)

    printed = command.render(selected(where, why="I1"))
    document_lines = [line for line in printed
                      if "LIV0044_reconciled" in line and "run " not in line]

    assert len(document_lines) == 1


def test_context_quotes_the_log_and_prefers_the_step(tmp_path):
    """A document's log is everything that happened to it; the last six
    lines of that are whatever ran last, not what this incident is
    about."""
    where = a_run(tmp_path, log=(
        "12:00 LIV0044_reconciled: sourcedoc built\n"
        "12:01 LIV0044_reconciled: enrich — PyHellen stopped answering\n"
        "12:02 LIV0326_v1_reconciled: something else\n"))
    run = read_run(where)
    item, = [entry for entry in run.incidents if entry.index == "I1"]

    quoted = command.log_context(run, item)

    assert len(quoted) == 1 and "enrich" in quoted[0]


def test_context_falls_back_to_the_document_when_the_step_says_nothing(
        tmp_path):
    where = a_run(tmp_path, log="12:00 LIV0044_reconciled: sourcedoc built\n")
    run = read_run(where)
    item, = [entry for entry in run.incidents if entry.index == "I1"]

    assert command.log_context(run, item) == (
        "12:00 LIV0044_reconciled: sourcedoc built",)


# =============================================================================
# Exit codes and the JSON
# =============================================================================

def test_a_run_with_incidents_exits_one_whatever_the_selectors_left(tmp_path,
                                                                    capsys):
    """The question a wrapper asks this command is "did that run need a
    human". Narrowing the view must not change the answer."""
    a_run(tmp_path)
    from teille_douce.settings import use_settings

    with use_settings(output_dir=tmp_path):
        assert command.execute(parse([])) == 1
        # A selector that leaves nothing still reports the run's verdict.
        assert command.execute(parse(["--code", "breaker"])) == 1
    capsys.readouterr()


def test_a_clean_run_exits_zero(tmp_path, capsys):
    a_run(tmp_path, incidents=(), exit_code=0)
    from teille_douce.settings import use_settings

    with use_settings(output_dir=tmp_path):
        assert command.execute(parse([])) == 0
    assert "no incident" in capsys.readouterr().out


def test_no_record_at_all_says_where_it_would_have_been(tmp_path):
    from teille_douce.settings import use_settings

    with use_settings(output_dir=tmp_path):
        with pytest.raises(SystemExit) as raised:
            command.execute(parse([]))
    assert ".teille-douce" in str(raised.value)


def test_the_json_carries_the_incidents_and_what_could_not_be_read(tmp_path):
    where = a_run(tmp_path, manifest=False)

    document = command._as_json(command.select(read_run(where)))

    assert len(document["incidents"]) == 3
    assert document["incidents"][0]["index"] == "I1"
    assert document["unreadable"][0]["file"] == "run.json"


# =============================================================================
# The rendering, at every width
# =============================================================================

@pytest.mark.parametrize("width", [56, 72, 92, 120, 200])
def test_every_line_fits_the_terminal_it_was_given(tmp_path, width):
    where = a_run(tmp_path, log="12:00 LIV0044_reconciled: enrich failed\n")
    run = read_run(where)
    room = min(width, command.MAX_WIDTH)
    rendered = (command.render(command.select(run), width=width)
                + command.render(command.select(run, why="I2"), width=width,
                                 context=command.log_context(run,
                                                             run.incidents[1]))
                + command.render_runs([run], width=width)
                + command.render_limits(width=width))

    for line in rendered:
        assert cells(line) <= room, f"{cells(line)} > {room}: {line!r}"


@pytest.mark.parametrize("width", [56, 72, 92])
def test_a_document_never_runs_into_its_own_code(tmp_path, width):
    """`LIV0326_v1_reconciled` is twenty-one and `container_unanchored`
    twenty; a literal column loses whichever is longer."""
    where = a_run(tmp_path)
    lines = command.render(command.select(read_run(where)), width=width)

    for line in lines:
        if "LIV0326_v1_reconciled" not in line:
            continue
        after = line.split("LIV0326_v1_reconciled", 1)[1]
        assert after.startswith("  ") or after.startswith("…"), line


def test_the_runs_are_listed_newest_first(tmp_path):
    a_run(tmp_path, stamp="20260903-180824-0031415")
    a_run(tmp_path, stamp="20260904-090000-0031415", incidents=())
    from teille_douce.report.store import RunStore

    runs = [read_run(path) for path in reversed(RunStore.kept(tmp_path))]
    printed = command.render_runs(runs)

    names = [line for line in printed if "2026090" in line]
    assert names[0].strip().startswith("20260904")
    assert "3 incidents" in names[1]


def test_listing_the_runs_answers_three_when_there_are_none(tmp_path, capsys):
    from teille_douce.settings import use_settings

    with use_settings(output_dir=tmp_path):
        assert command.execute(parse(["--runs"])) == 3
        assert "no run" in capsys.readouterr().out
        a_run(tmp_path, incidents=())
        assert command.execute(parse(["--runs"])) == 0
    assert "1 run kept" in capsys.readouterr().out


def test_the_limits_need_no_run_at_all(tmp_path, capsys):
    """The question "what does this pipeline not measure" is about the
    pipeline, not about a run — and is asked most often by someone who
    has not launched one yet."""
    from teille_douce.settings import use_settings

    with use_settings(output_dir=tmp_path / "nothing here"):
        assert command.execute(parse(["--limits"])) == 0
    assert "impossible" in capsys.readouterr().out


def test_an_incident_number_that_is_not_there_says_so(tmp_path, capsys):
    a_run(tmp_path)
    from teille_douce.settings import use_settings

    with use_settings(output_dir=tmp_path):
        with pytest.raises(SystemExit, match="I9"):
            command.execute(parse(["--why", "I9"]))
    capsys.readouterr()


def test_the_json_goes_through_the_command(tmp_path, capsys):
    a_run(tmp_path)
    from teille_douce.settings import use_settings

    with use_settings(output_dir=tmp_path):
        assert command.execute(parse(["--json", "--why", "I1",
                                      "--context"])) == 1
    document = json.loads(capsys.readouterr().out)
    assert document["incidents"][0]["index"] == "I1"
