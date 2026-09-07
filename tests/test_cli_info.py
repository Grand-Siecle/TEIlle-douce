# -----------------------------------------------------------
# `teille-douce info`: which layer set this value.
#
# The four layers are a trap rather than a feature without a way to ask
# the chain where a value came from — and the config file is the sharpest
# edge of it, since it is found by walking up from the working directory.
#
# The rendering is a pure function of the resolved report, so every shape
# is testable with no terminal and no environment.
#
# Run: venv/bin/python -m pytest tests/test_cli_info.py -q
# -----------------------------------------------------------
import pytest

from teille_douce.cli import app
from teille_douce.cli import info as command
from teille_douce.report.text import cells
from teille_douce.settings import Settings


def parse(argv):
    return app.build_parser().parse_args(["info", *argv])


def a_config(tmp_path, body):
    written = tmp_path / "teille-douce.toml"
    written.write_text(body, encoding="utf-8")
    return written


def gathered(tmp_path=None, env=None, flags=None, body=None):
    """A report over settings resolved from the layers given."""
    path = a_config(tmp_path, body) if body is not None else None
    settings = Settings.load(flags=flags or {}, env=env or {},
                             config_file=path)
    return command.gather(settings, env=env or {}, config_path=path)


def only(report, name):
    one, = [item for item in report.settings if item.name == name]
    return one


# =============================================================================
# The chain
# =============================================================================

def test_every_layer_is_printed_whether_or_not_it_offered_anything():
    """The question is "why is my variable ignored", and the answer is
    always a layer ABOVE it: one that offered nothing has to be visible
    for the one that did to mean anything."""
    report = gathered(env={"TDOUCE_PYHELLEN_URL": "http://labo:9000"})
    item = only(report, "pyhellen_url")

    assert [offer.layer for offer in item.offers] == list(command.LAYERS)
    assert [offer.used for offer in item.offers] == [False, True, False, False]
    assert item.offers[0].value is None          # no flag was given
    assert item.offers[3].value == "http://localhost:8000"


def test_the_config_file_is_named_even_when_nothing_in_it_was_used(tmp_path):
    """A file found by walking up and forgotten is the least debuggable
    thing in the design. It is named whether or not it won anything."""
    report = gathered(tmp_path, body="[limits]\njobs = 4\n",
                      env={"TDOUCE_JOBS": "6"})

    assert report.config_used is False
    # The leaf survives at every width: a config file is identified by
    # its directory AND its name, and clipping from the right drops the
    # half that says what the line is about.
    for width in (60, 92, 200):
        printed = "\n".join(command.render(report, width=width))
        assert "teille-douce.toml" in printed
        assert "config file" in printed
    # The path in full is in the JSON, which no width bounds.
    assert command._as_json(report)["config_file"] == str(report.config_path)


def test_a_layer_whose_value_was_refused_says_so_against_that_layer(tmp_path):
    """`TDOUCE_JOBS=eight` is refused by its converter and the layer
    below takes effect. The run says that once, in a warning, four hours
    before anyone reads the log."""
    with pytest.warns(RuntimeWarning):
        report = gathered(tmp_path, body="[limits]\njobs = 4\n",
                          env={"TDOUCE_JOBS": "eight"})
    item = only(report, "max_workers")
    env, config = item.offers[1], item.offers[2]

    assert env.refused and "eight" in env.refused
    assert env.used is False
    assert config.used is True
    assert "refused" in "\n".join(command.render(report, name="max_workers"))


def test_a_value_nobody_set_is_held_apart_from_one_somebody_did(tmp_path):
    """In a report of twenty-nine settings, the two or three that were
    set are the whole content. Marked structurally, not by colour: the
    output is read in pipes and pasted into issues."""
    report = gathered(tmp_path, body="[limits]\njobs = 4\n")
    lines = command.render(report)

    jobs, = [line for line in lines if "max_workers" in line]
    ocr, = [line for line in lines if "ocr_dir" in line]
    assert jobs.startswith("•")
    assert not ocr.startswith("•")
    assert ocr.rstrip().endswith("default")


# =============================================================================
# Naming a setting
# =============================================================================

def test_a_setting_can_be_named_by_the_variable_the_report_just_printed():
    """A reader shown `env TDOUCE_PYHELLEN_URL` will type that. Refusing
    it because the attribute is spelled differently would make the output
    of this command unusable as its own input."""
    assert command._named("pyhellen_url") == "pyhellen_url"
    assert command._named("TDOUCE_PYHELLEN_URL") == "pyhellen_url"
    assert command._named("services.pyhellen") == "pyhellen_url"


def test_an_unknown_setting_is_a_usage_error_with_a_suggestion(capsys):
    """2, not 1: 1 is this CLI's code for "some volumes failed", and a
    wrapper reading exit codes would have been told the corpus was at
    fault by a typo in its own command line."""
    with pytest.raises(SystemExit) as raised:
        command._named("pyhelen_url")

    assert raised.value.code == 2
    said = capsys.readouterr().err
    assert "unknown setting" in said and "pyhellen_url" in said


def test_the_flag_column_is_read_off_the_parser_not_invented():
    """`option_for` invents `--<setting>` when there is none, because it
    exists to name a flag in a usage message. Here that would answer
    "which flag sets this" with one that does not exist."""
    assert command._flag_for("ocr_dir") == "-i/--input"
    assert command._flag_for("pyhellen_timeout") is None

    report = gathered()
    item = only(report, "pyhellen_timeout")
    assert item.offers[0].where == "(no flag)"


# =============================================================================
# The rendering, at every width
# =============================================================================

@pytest.mark.parametrize("width", [56, 72, 92, 120, 200])
def test_every_line_fits_the_terminal_it_was_given(width):
    report = gathered(env={"TDOUCE_OUTPUT_DIR": "/data/grand-siecle/tei_output"})
    room = min(width, command.MAX_WIDTH)
    for line in (command.render(report, width=width)
                 + command.render(report, width=width, name="output_dir")):
        assert cells(line) <= room, f"{cells(line)} > {room}: {line!r}"


def _row_for(lines, name):
    row, = [line for line in lines
            if line[2:].startswith(name + " ") or line[2:] == name]
    return row


def _starts_at(row, name):
    """Where the value begins, in cells from the left margin."""
    gap = row[2:][len(name):]
    return cells(name) + cells(gap) - cells(gap.lstrip())


@pytest.mark.parametrize("width", [56, 72, 92, 120])
def test_a_name_never_runs_into_its_own_value(width):
    """`pyhellen_max_consecutive_failures` is thirty-three characters,
    longer than any column a sane layout gives it, so the gap after a
    name is `max(2, column - name)` and not `column - name`: the second
    is empty for that one setting, and the line reads
    `pyhellen_max_consecutive_failures10`.

    Written twice before it held. "Some run of two spaces exists in the
    line" is satisfied by `pad`'s right-aligned origin whatever happens
    at the left edge; "the values all start in one column" is false by
    design, because the column is capped at a third of the line and that
    name overruns the cap. The floor is what there is to guard.
    """
    report = gathered()
    lines = command.render(report, width=width)

    for item in report.settings:
        row = _row_for(lines, item.name)
        after = row[2:][len(item.name):]
        assert after.startswith("  "), (
            f"nothing separates {item.name} from its value: {row!r}")
        assert item.value in row or "…" in row, (
            f"the value of {item.name} is not on its row: {row!r}")


@pytest.mark.parametrize("width", [56, 72, 92, 120])
def test_every_name_the_cap_has_room_for_shares_one_column(width):
    """The other half of measuring: a name is ragged only when it
    overruns the cap the layout puts on the column — a third of the
    line, so that one thirty-three-character setting cannot push every
    value on the screen to the right. A literal column of 24 leaves
    `modernize_similarity_min` starting at 26 with the rest at 24, and
    it is nowhere near the cap.
    """
    report = gathered()
    room = min(width, command.MAX_WIDTH) - 2
    cap = max(20, room // 3)
    lines = command.render(report, width=width)

    starts = {item.name: _starts_at(_row_for(lines, item.name), item.name)
              for item in report.settings}
    column = min(starts.values())

    ragged = {name: start for name, start in starts.items()
              if start != column and cells(name) + 2 <= cap}
    assert not ragged, (
        f"names that fit the {cap}-cell cap start their values "
        f"elsewhere: {ragged}")


def test_one_very_long_name_cannot_eat_the_line(width=92):
    """The column is capped at a third of the room. Unmeasured it would
    be thirty-five wide because of one setting, and every value on the
    screen would start there."""
    report = gathered()
    room = min(width, command.MAX_WIDTH) - 2

    starts = {_starts_at(_row_for(command.render(report, width=width),
                                  item.name), item.name)
              for item in report.settings if cells(item.name) <= 24}

    assert max(starts) <= room // 3 + 2


def test_the_versions_written_into_appinfo_are_reported():
    """`info` answers "what produced this file" as well as "what will
    this run do": the same versions land in <appInfo>."""
    report = gathered()
    labels = [label for label, _ in report.versions]

    assert "teille-douce" in labels
    assert any("YALTAi" in label for label in labels)
    assert "versions" in "\n".join(command.render(report))


def test_the_json_carries_the_layers_and_not_only_the_value(tmp_path):
    """A wrapper asking "where did this come from" needs the chain; the
    value alone is what it already had."""
    report = gathered(tmp_path, body="[limits]\njobs = 4\n")
    document = command._as_json(report, "max_workers")

    entry, = document["settings"]
    assert entry["origin"].startswith("config:")
    assert [layer["layer"] for layer in entry["layers"]] == list(command.LAYERS)
    assert document["config_file"].endswith("teille-douce.toml")


# =============================================================================
# The surface
# =============================================================================

def test_the_command_takes_the_same_config_flags_as_run():
    """`info --config other.toml` is how you ask what a wrapper's file
    would do, without running it."""
    parsed = parse(["--config", "other.toml", "pyhellen_url"])

    assert parsed.setting == "pyhellen_url"
    assert parsed.config_file == "other.toml"
    assert parse(["--no-config"]).no_config is True


def test_no_argument_means_every_setting():
    report = gathered()

    assert len(report.settings) > 20
    assert parse([]).setting is None


def test_the_command_prints_and_answers_zero(tmp_path, capsys, monkeypatch):
    """The wiring: `execute` resolves the config file the way `run` does,
    renders, and never fails on a machine with no corpus."""
    import json

    from teille_douce.settings import use_settings

    monkeypatch.chdir(tmp_path)
    a_config(tmp_path, "[limits]\njobs = 4\n")

    with use_settings():
        assert command.execute(parse([])) == 0
        printed = capsys.readouterr().out
        assert "config file" in printed and "teille-douce.toml" in printed

        assert command.execute(parse(["--json", "max_workers"])) == 0
        document = json.loads(capsys.readouterr().out)
    assert [item["name"] for item in document["settings"]] == ["max_workers"]
