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
from pathlib import Path

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


def test_no_record_at_all_says_where_it_would_have_been(tmp_path, capsys):
    from teille_douce.settings import use_settings

    with use_settings(output_dir=tmp_path):
        with pytest.raises(SystemExit) as raised:
            command.execute(parse([]))

    # 3, which the guide promises and the comment beside `--runs`
    # claims. `raise SystemExit(str)` exits 1 — "some volumes failed",
    # from a command that converted nothing.
    assert raised.value.code == 3
    assert ".teille-douce" in capsys.readouterr().err


def test_the_json_carries_the_incidents_and_what_could_not_be_read(tmp_path):
    where = a_run(tmp_path, manifest=False)

    document = command._as_json(command.select(read_run(where)))

    assert len(document["incidents"]) == 3
    assert document["incidents"][0]["index"] == "I1"
    assert document["unreadable"][0]["file"] == "run.json"


# =============================================================================
# The rendering, at every width
# =============================================================================

@pytest.mark.parametrize("width", [2, 6, 11, 12, 20, 30, 42, 56, 72, 92,
                                   120, 200])
def test_every_line_fits_the_terminal_it_was_given(tmp_path, width):
    """Against the renderer's own room — `min(width, MAX_WIDTH) - 2` —
    and starting at 20, not 56. Comparing to `min(width, MAX_WIDTH)` was
    two cells looser than the contract, and starting above 42 skipped
    the widths where the log-path budget goes negative."""
    where = a_run(tmp_path, log="12:00 LIV0044_reconciled: enrich failed\n")
    run = read_run(where)
    room = min(width, command.MAX_WIDTH) - 2
    rendered = (command.render(command.select(run), width=width)
                + command.render(command.select(run, why="I2"), width=width,
                                 context=command.log_context(run,
                                                             run.incidents[1]))
                + command.render(command.select(run, block="source"),
                                 width=width)
                + command.render_runs([run], width=width)
                + command.render_limits(width=width))

    for line in rendered:
        if line.strip().startswith("teille-douce "):
            # The one exemption, and the same one `report/summary.py`
            # states for its `next` block: a command is printed whole or
            # not at all, because `report --run … -o /srv/expo…` is not
            # a shorter command, it is one that does not run. A terminal
            # wraps it and nothing is lost.
            continue
        assert cells(line) <= room, f"{cells(line)} > {room}: {line!r}"
        # The check sweep asserts this and the report one did not, which
        # is how `--why --context` kept its trailing spaces at 6, 11 and
        # 12 columns: invisible on screen, and there the moment anyone
        # pastes the output into an issue.
        assert line == line.rstrip(), f"trailing space: {line!r}"


def _rows(run, lines):
    """The incident rows, found by the marker each one starts with."""
    return [line for line in lines
            if any(line.lstrip().startswith(item.index + " ")
                   for item in run.incidents)]


@pytest.mark.parametrize("width", [92, 120, 200])
def test_a_document_is_shown_whole_when_the_line_has_room_for_it(tmp_path,
                                                                 width):
    """The column is measured off the widest document, not written as a
    literal: `LIV0326_v1_reconciled` is twenty-one and
    `container_unanchored` twenty, and `{document:<14}` clips whichever
    is longer where there was room for both.

    Written first as "what follows the name starts with two spaces",
    which the mutation it condemns satisfied by CLIPPING the name — no
    line then contained it, none was examined, and the test passed
    having asserted nothing.
    """
    where = a_run(tmp_path)
    run = read_run(where)

    rows = _rows(run, command.render(command.select(run), width=width))

    assert len(rows) == len(run.incidents)
    for row, item in zip(rows, run.incidents):
        assert item.document in row, f"the name was clipped: {row!r}"


@pytest.mark.parametrize("width", [30, 44, 56, 72, 92])
def test_a_document_never_runs_into_its_own_code(tmp_path, width):
    """However narrow the terminal, the two are two fields."""
    where = a_run(tmp_path)
    run = read_run(where)

    rows = _rows(run, command.render(command.select(run), width=width))

    assert len(rows) == len(run.incidents)
    for row, item in zip(rows, run.incidents):
        if item.code not in row:
            continue            # the code itself did not fit; nothing to space
        before = row.split(item.code, 1)[0]
        assert before.endswith("  "), (
            f"nothing separates the document from its code: {row!r}")
        shown = before.split(item.index, 1)[1].strip()
        assert shown, f"the document is not on its row at all: {row!r}"
        assert item.document.startswith(shown.rstrip("… ")), (
            f"{shown!r} is not the start of {item.document!r}")


def test_a_run_whose_manifest_was_never_written_is_not_listed_as_empty(
        tmp_path):
    """`run.json` is written at the end, so a run killed in the fourth
    hour has none. `0/0 converted · exit None` is a count with a false
    denominator that reads as "this run converted nothing" — and the
    single-run view of the same directory says `no document recorded`
    and prints an `unreadable` line, so the two disagreed."""
    a_run(tmp_path, stamp="20260904-090000-0031415", manifest=False)
    from teille_douce.report.store import RunStore

    listed, = command.render_runs(
        [read_run(path) for path in RunStore.kept(tmp_path)], width=120)[2:]

    assert "0/0" not in listed
    assert "record never finished" in listed
    # `exit None` is the manifest that was never written, printed as
    # though it were an exit code.
    assert "exit None" not in listed
    assert "no exit code recorded" in listed


def test_the_json_answers_about_the_incident_that_was_asked_for(tmp_path):
    """`--why I2` narrows the prose to one incident and left the JSON
    carrying all three, with no field naming the one asked for — so a
    wrapper reading `incidents[0]` got whichever came first."""
    where = a_run(tmp_path)

    document = command._as_json(selected(where, why="I2"))

    assert [item["index"] for item in document["incidents"]] == ["I2"]
    assert document["why"] == "I2"
    assert command._as_json(selected(where))["why"] is None


@pytest.mark.parametrize("manifest", [
    {"argv": None, "documents": None, "settings": None},
    {"argv": "teille-douce run", "documents": [], "settings": 3},
    ["not", "a", "manifest"],
])
def test_a_manifest_of_the_wrong_shape_is_reported_not_raised(tmp_path,
                                                              manifest):
    """`read_run` promises never to raise on a damaged record, and
    `failed_last_time` says why the check is needed in as many words:
    json.loads is happy with `null`, `[]` or a bare string. The reader
    half did not inherit it, so `{"argv": null}` came out as a TypeError
    and `{"documents": []}` as an AttributeError inside the renderer."""
    where = a_run(tmp_path, manifest=False)
    (where / "run.json").write_text(json.dumps(manifest), encoding="utf-8")

    run = read_run(where)

    assert len(run.incidents) == 3, "the index survives a broken manifest"
    assert command.render(command.select(run))       # and both views render
    assert command.render_runs([run])


def test_a_field_of_the_wrong_type_is_named_rather_than_ignored(tmp_path):
    """A null field is a field the manifest does not carry; a string
    where a list belongs is a record that is wrong, and the difference
    is worth printing."""
    where = a_run(tmp_path, manifest=False)
    (where / "run.json").write_text(
        json.dumps({"argv": "teille-douce run", "documents": []}),
        encoding="utf-8")

    named = dict(read_run(where).unreadable)

    assert "run.json:argv" in named and "str" in named["run.json:argv"]
    assert "run.json:documents" in named

    quiet = a_run(tmp_path, stamp="20260905-090000-0031415", manifest=False)
    (quiet / "run.json").write_text(json.dumps({"argv": None}),
                                    encoding="utf-8")
    assert read_run(quiet).unreadable == ()


def test_a_jsonl_line_that_is_not_an_object_does_not_take_down_the_listing(
        tmp_path):
    """`null`, `3`, `[]` and `"x"` are all valid JSON and none of them
    has `.get`. One such line raised out of `read_run` — whose docstring
    promises it never does — and `--runs` reads every run kept here, so
    one damaged line in one old run took down the whole listing."""
    where = a_run(tmp_path)
    with open(where / "incidents.jsonl", "a", encoding="utf-8") as handle:
        handle.write("null\n3\n[]\n\"x\"\n")

    run = read_run(where)

    assert len(run.incidents) == 3
    assert len(run.unreadable) == 4
    assert all("not an incident" in reason for _, reason in run.unreadable)
    assert command.render_runs([run])              # and the listing renders


def test_json_is_honoured_on_every_branch(tmp_path, capsys):
    """It is documented as "the same lines, as one JSON object", and
    `--limits --json` and `--runs --json` printed prose into the stdout
    a wrapper was parsing."""
    a_run(tmp_path)
    from teille_douce.settings import use_settings

    with use_settings(output_dir=tmp_path):
        assert command.execute(parse(["--limits", "--json"])) == 0
        limits = json.loads(capsys.readouterr().out)
        assert {row["kind"] for row in limits["limits"]} == {"impossible",
                                                             "not yet"}

        assert command.execute(parse(["--runs", "--json"])) == 0
        runs = json.loads(capsys.readouterr().out)
    assert runs["runs"][0]["incidents"] == 3


def test_a_block_that_is_not_indexed_is_blamed_rather_than_the_number(
        tmp_path, capsys):
    """`--block source` empties the list before `--why` is resolved, so
    the refusal blamed `I1` — a number the reader had just been shown —
    for a filter typed on the other half of the same command line."""
    a_run(tmp_path)
    from teille_douce.settings import use_settings

    with use_settings(output_dir=tmp_path):
        with pytest.raises(SystemExit) as raised:
            command.execute(parse(["--block", "source", "--why", "I1"]))

    assert raised.value.code == 2
    said = capsys.readouterr().err
    assert "not indexed" in said and "--block source" in said
    assert "no incident" not in said


def test_a_line_cut_through_a_character_does_not_take_down_the_listing(
        tmp_path):
    """The canonical damage this format exists for — a Ctrl-C or a full
    disk cutting a line — lands in the middle of a multibyte character,
    and `UnicodeDecodeError` IS a `ValueError`. The read of `run.json`
    eleven lines above already caught it; this one did not inherit the
    clause, so `report --runs`, which reads every run kept here, died
    with a traceback and exit 1."""
    where = a_run(tmp_path)
    with open(where / "incidents.jsonl", "ab") as handle:
        handle.write('{"code": "phase_lost", "document": "LIV0044_r'
                     .encode("utf-8") + "é".encode("utf-8")[:1] + b"\n")

    run = read_run(where)

    assert len(run.incidents) == 3
    assert run.unreadable
    assert command.render_runs([run])


def test_a_field_of_the_wrong_type_inside_an_incident_is_read_as_text(
        tmp_path):
    """The guard covered the container and not its fields, so a
    `document` that is a dict passed it and crashed one frame later
    inside a regular expression. `run.json`'s half of this reader
    already checks per field."""
    where = a_run(tmp_path)
    with open(where / "incidents.jsonl", "a", encoding="utf-8") as handle:
        handle.write(json.dumps({"code": {"a": 1}, "document": 3,
                                 "kind": None, "step": None}) + "\n")

    run = read_run(where)
    odd = run.incidents[-1]

    assert isinstance(odd.code, str) and isinstance(odd.document, str)
    assert odd.kind == "" and odd.step == ""
    assert command.render(command.select(run))
    assert command.render(command.select(run, code="x"))
    assert command._as_json(command.select(run))


@pytest.mark.parametrize("selector,named", [
    (["--code", "phase"], "--code phase"),
    (["LIV0044_reconciled"], "the document selector"),
    (["--block", "source"], "--block source"),
])
def test_why_blames_the_selector_that_removed_it_not_the_number(
        tmp_path, capsys, selector, named):
    """`--why` is resolved over what the other selectors leave, so
    "no incident 'I3'" was false whenever one of them had removed it —
    and `report LIV0044 --code phase_lost` is this module's own example
    of composing selectors. Only `--block` was special-cased, which left
    the two commoner ones saying something untrue."""
    a_run(tmp_path)
    from teille_douce.settings import use_settings

    with use_settings(output_dir=tmp_path):
        with pytest.raises(SystemExit) as raised:
            command.execute(parse([*selector, "--why", "I3"]))

    assert raised.value.code == 2
    said = capsys.readouterr().err
    assert named in said
    assert "no incident" not in said


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
        with pytest.raises(SystemExit) as raised:
            command.execute(parse(["--why", "I9"]))

    # 2: a value typed on the command line that names nothing.
    assert raised.value.code == 2
    assert "I9" in capsys.readouterr().err


def test_the_json_goes_through_the_command(tmp_path, capsys):
    a_run(tmp_path)
    from teille_douce.settings import use_settings

    with use_settings(output_dir=tmp_path):
        assert command.execute(parse(["--json", "--why", "I1",
                                      "--context"])) == 1
    document = json.loads(capsys.readouterr().out)
    assert document["incidents"][0]["index"] == "I1"


def test_a_runs_directory_that_cannot_be_read_is_not_a_directory_with_no_runs(
        tmp_path, capsys):
    """`_runs` swallows OSError and answers "no runs", which is right
    for `prune` — whose caller is a run that must not die over its own
    housekeeping — and wrong for anyone asking. `failed_last_time`
    guards this and its comment names the defect; the guard had reached
    one of the three callers, so `report` said "no run recorded here"
    over records sitting right there."""
    import os
    import stat

    from teille_douce.report.store import RUNS
    from teille_douce.settings import use_settings

    a_run(tmp_path)
    os.chmod(tmp_path / RUNS, 0o000)
    if os.access(tmp_path / RUNS, os.R_OK):
        os.chmod(tmp_path / RUNS, stat.S_IRWXU)
        pytest.skip("this user can read a directory with mode 000")
    try:
        with use_settings(output_dir=tmp_path):
            for argv in ([], ["--runs"]):
                with pytest.raises(SystemExit) as raised:
                    command.execute(parse(argv))
                assert raised.value.code == 3
                said = capsys.readouterr().err
                assert "cannot be read" in said
                assert "no run" not in said
    finally:
        os.chmod(tmp_path / RUNS, stat.S_IRWXU)


def test_a_manifest_nested_past_the_recursion_limit_is_reported(tmp_path):
    """`json.loads` raises `RecursionError`, which is neither an
    `OSError` nor a `ValueError`. The widening reached `read_run` and
    not `failed_last_time`, twelve lines away, reading the same file —
    so `run --retry-failed` came out as a traceback."""
    from teille_douce.report.store import RunStore

    where = a_run(tmp_path, manifest=False)
    (where / "run.json").write_text("[" * 60000 + "]" * 60000,
                                    encoding="utf-8")

    run = read_run(where)
    assert run.unreadable and len(run.incidents) == 3

    with pytest.raises(ValueError, match="could not be read"):
        RunStore.failed_last_time(tmp_path)


def test_the_command_the_footer_offers_means_this_run(tmp_path):
    """It carried neither `--run` nor `-o`, so from another working
    directory it exited 3, and from the same one it silently answered
    about the NEWEST run rather than the one on screen. A command that
    quietly means something else is worse than one that fails."""
    where = a_run(tmp_path, stamp="20260903-180824-0031415")
    run = read_run(where)

    offered, = [line for line in command.render(command.select(run), width=92)
                if "--why" in line]

    assert "--run 20260903-180824-0031415" in offered
    assert f"-o {tmp_path}" in offered or f"-o '{tmp_path}'" in offered


def test_a_manifest_that_is_a_fifo_does_not_block_the_reader(tmp_path):
    """The index beside it is guarded with `is_file`; the manifest was
    not, and `--runs` reads every run kept here — one such directory
    took the whole listing with it, for ever."""
    import os
    import subprocess
    import sys

    where = a_run(tmp_path, manifest=False)
    os.mkfifo(where / "run.json")

    finished = subprocess.run(
        [sys.executable, "-c",
         "from teille_douce.report.store import read_run;"
         f"run = read_run({str(where)!r});"
         "print(len(run.incidents)); print(bool(run.unreadable))"],
        capture_output=True, text=True, timeout=60,
        cwd=str(Path(__file__).resolve().parent.parent))

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.split() == ["3", "True"]


def test_a_record_that_could_not_be_read_is_not_a_clean_run(tmp_path, capsys):
    """The screen said `unreadable run.json` and the exit code told the
    wrapper the run was clean. The honest answer to "was it clean" is "I
    cannot tell you that"."""
    from teille_douce.settings import use_settings

    a_run(tmp_path, incidents=(), manifest=False)

    with use_settings(output_dir=tmp_path):
        code = command.execute(parse([]))

    assert code == 1
    assert "unreadable" in capsys.readouterr().out
