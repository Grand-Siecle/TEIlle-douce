# -----------------------------------------------------------
# The options of `teille-douce run`, and what they resolve to.
#
# Every option here answers a question the pipeline could only be asked by
# editing config.py or by prefixing the command with environment
# variables. The three-variable prefix that turned the annotation phases
# off was the most copy-pasted string in the documentation; it is --fast
# now.
#
# Run: venv/bin/python -m pytest tests/test_cli_run_options.py -q
# -----------------------------------------------------------
from pathlib import Path

import pytest

from teille_douce.cli import app


def settings_for(argv, env=None):
    """Resolve *argv* the way the command line does, without running."""
    return app.settings_from(app.parse_args(argv), env=env or {})


# =============================================================================
# Paths
# =============================================================================

def test_the_path_options_reach_the_settings():
    settings = settings_for([
        "run", "-i", "/corpus", "-o", "/out", "--entities", "/ents",
        "--metadata", "/books.csv", "--persons", "/people.csv",
    ])

    assert settings.ocr_dir == Path("/corpus")
    assert settings.output_dir == Path("/out")
    assert settings.entities_dir == Path("/ents")
    assert settings.metadata_csv == Path("/books.csv")
    assert settings.persons_csv == Path("/people.csv")


def test_an_option_not_given_leaves_the_environment_in_charge():
    settings = settings_for(["run", "-o", "/out"],
                            env={"TDOUCE_OCR_DIR": "/from-env"})

    assert settings.ocr_dir == Path("/from-env")
    assert settings.output_dir == Path("/out")


# =============================================================================
# Phases
# =============================================================================

def test_every_phase_runs_by_default():
    settings = settings_for(["run"])

    assert (settings.enrich, settings.modernize, settings.ner) == (True, True, True)


def test_fast_turns_every_annotation_phase_off():
    """Replaces `TDOUCE_NER=0 TDOUCE_ENRICHMENT=0 TDOUCE_MODERNIZE=0`, the
    string copy-pasted into five documents."""
    settings = settings_for(["run", "--fast"])

    assert (settings.enrich, settings.modernize, settings.ner) == (False, False, False)


def test_a_single_phase_can_be_removed():
    settings = settings_for(["run", "--no-ner"])

    assert (settings.enrich, settings.modernize, settings.ner) == (True, True, False)


def test_phases_sets_the_whole_set():
    settings = settings_for(["run", "--phases", "enrich,ner"])

    assert (settings.enrich, settings.modernize, settings.ner) == (True, False, True)


def test_a_delta_applies_on_top_of_the_set_left_to_right():
    """`--fast --enrich` is the useful idiom: no service work except the
    one phase you want."""
    settings = settings_for(["run", "--fast", "--enrich"])

    assert (settings.enrich, settings.modernize, settings.ner) == (True, False, False)


def test_naming_a_phase_in_the_set_and_removing_it_is_a_usage_error():
    """`--phases ner --no-ner` has no reading that says what the user
    meant, so it is refused rather than silently resolved."""
    with pytest.raises(SystemExit) as excinfo:
        settings_for(["run", "--phases", "ner", "--no-ner"])

    assert excinfo.value.code == 2


def test_an_unknown_phase_is_a_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        settings_for(["run", "--phases", "enrich,translate"])

    assert excinfo.value.code == 2


# =============================================================================
# Services and resources
# =============================================================================

def test_the_service_options_reach_the_settings():
    settings = settings_for([
        "run", "--pyhellen", "http://h:9000", "--vieuxparler", "http://v:9011",
        "--health-timeout", "5",
    ])

    assert settings.pyhellen_url == "http://h:9000"
    assert settings.modernize_api["fra"] == "http://v:9011"
    assert settings.health_timeout == 5.0


def test_the_resource_options_reach_the_settings():
    settings = settings_for([
        "run", "-j", "2", "--batch-size", "8", "--concurrency", "3",
    ])

    assert settings.max_workers == 2
    assert settings.modernize_batch_size == 8
    assert settings.modernize_concurrency == 3
    assert settings.pyhellen_concurrency == 3


def test_a_resource_option_that_cannot_be_used_is_a_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        settings_for(["run", "-j", "0"])

    assert excinfo.value.code == 2


# =============================================================================
# Verbosity
# =============================================================================

@pytest.mark.parametrize("argv,expected", [
    ([], "WARNING"),
    (["-v"], "INFO"),
    (["-vv"], "DEBUG"),
    (["-q"], "ERROR"),
    (["--log-level", "CRITICAL"], "CRITICAL"),
])
def test_verbosity_resolves_to_a_console_level(argv, expected):
    settings = settings_for(["run", *argv])

    assert settings.log_level == expected


def test_the_log_file_can_be_moved_or_switched_off():
    assert settings_for(["run", "--log-file", "/tmp/x.log"]).log_file == Path("/tmp/x.log")
    assert settings_for(["run", "--no-log-file"]).log_file is None


# =============================================================================
# Document selection
# =============================================================================

def test_no_selector_means_every_volume():
    args = app.build_parser().parse_args(["run"])

    assert args.documents == []


def test_selectors_are_kept_in_order():
    args = app.build_parser().parse_args(["run", "LIV0044", "LIV003*"])

    assert args.documents == ["LIV0044", "LIV003*"]


def test_exclusions_are_repeatable():
    args = app.build_parser().parse_args(["run", "-x", "LIV0021", "-x", "LIV003*"])

    assert args.exclude == ["LIV0021", "LIV003*"]


def test_skip_existing_and_force_cannot_both_be_asked_for():
    with pytest.raises(SystemExit) as excinfo:
        app.build_parser().parse_args(["run", "--skip-existing", "--force"])

    assert excinfo.value.code == 2


# =============================================================================
# What the selection does to the work list
# =============================================================================

from teille_douce.cli.run import select_documents


def _docs(*names):
    return [(name, [], None) for name in names]


def test_no_selector_keeps_every_document():
    kept, missed = select_documents(_docs("LIV0044", "LIV0021"), [], [], None)

    assert [d[0] for d in kept] == ["LIV0044", "LIV0021"]
    assert missed == []


def test_a_selector_matches_a_name_an_internal_id_or_a_glob():
    docs = _docs("LIV0044_reconciled", "LIV0021_reconciled", "LIV0038_t2_reconciled")

    assert [d[0] for d in select_documents(docs, ["LIV0044_reconciled"], [], None)[0]] \
        == ["LIV0044_reconciled"]
    assert [d[0] for d in select_documents(docs, ["LIV0021"], [], None)[0]] \
        == ["LIV0021_reconciled"]
    assert [d[0] for d in select_documents(docs, ["LIV003*"], [], None)[0]] \
        == ["LIV0038_t2_reconciled"]


def test_a_selector_that_matches_nothing_is_reported():
    """A typo must not look like an empty corpus."""
    kept, missed = select_documents(_docs("LIV0044"), ["LIV9999"], [], None)

    assert kept == []
    assert missed == ["LIV9999"]


def test_exclusions_apply_after_selection():
    docs = _docs("LIV0044", "LIV0021", "LIV0038")
    kept, _ = select_documents(docs, [], ["LIV0021"], None)

    assert [d[0] for d in kept] == ["LIV0044", "LIV0038"]


def test_limit_keeps_the_first_n_in_order():
    docs = _docs("LIV0044", "LIV0021", "LIV0038")
    kept, _ = select_documents(docs, [], [], 2)

    assert [d[0] for d in kept] == ["LIV0044", "LIV0021"]


# =============================================================================
# What the review caught
# =============================================================================

def test_a_phase_delta_leaves_the_other_phases_to_the_lower_layers():
    """`--no-modernize` says nothing about enrichment or NER, so it must
    not switch them back on. Precedence is per setting — the contract this
    surface documents."""
    settings = settings_for(["run", "--no-modernize"],
                            env={"TDOUCE_NER": "0", "TDOUCE_ENRICHMENT": "0"})

    assert (settings.enrich, settings.modernize, settings.ner) == (False, False, False)
    assert settings.origin("ner") == "env:TDOUCE_NER"
    assert settings.origin("modernize") == "flag"


def test_the_all_alias_does_not_forbid_a_later_delta():
    """`--phases all --no-ner` is the same idiom as `--fast --enrich`. The
    refusal is meant for a phase the user typed in the list."""
    settings = settings_for(["run", "--phases", "all", "--no-ner"])

    assert (settings.enrich, settings.modernize, settings.ner) == (True, True, False)


@pytest.mark.parametrize("raw", ["info", "INFO", "Debug"])
def test_a_log_level_is_read_whatever_its_case(raw):
    """getattr(logging, "info") is a function, not a level: configuring a
    handler with it raised TypeError and killed the run before any work."""
    assert settings_for(["run", "--log-level", raw]).log_level == raw.upper()


def test_a_log_level_that_is_not_one_is_a_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        settings_for(["run", "--log-level", "chatty"])

    assert excinfo.value.code == 2


def test_a_usage_error_names_the_option_the_user_typed():
    """Reporting `--max-workers` for `-j` sends the reader looking for a
    flag that does not exist."""
    parser = app.build_parser()
    with pytest.raises(SystemExit):
        app.settings_from(parser.parse_args(["run", "-j", "0"]), env={})


def test_force_cancels_a_resume_asked_for_by_a_lower_layer():
    """--force had nothing to cancel: skip_existing was not a setting, so
    no environment or config file could ask for it."""
    assert settings_for(["run"], env={"TDOUCE_SKIP_EXISTING": "1"}).skip_existing is True
    assert settings_for(["run", "--force"],
                        env={"TDOUCE_SKIP_EXISTING": "1"}).skip_existing is False
    assert settings_for(["run", "--skip-existing"]).skip_existing is True


def test_a_config_file_is_found_by_walking_up_from_the_working_directory(tmp_path, monkeypatch):
    """The chain the documentation advertises has to exist. A file found by
    walking up is also the least debuggable thing in the design, so `info`
    names the one that was read."""
    (tmp_path / "teille-douce.toml").write_text(
        '[paths]\noutput = "from-the-file"\n', encoding="utf-8"
    )
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)

    settings = settings_for(["run"])

    # Anchored on the file, not on the working directory — see
    # test_a_config_file_anchors_its_relative_paths_to_itself.
    assert settings.output_dir == tmp_path / "from-the-file"
    assert "teille-douce.toml" in settings.origin("output_dir")


def test_no_config_skips_discovery(tmp_path, monkeypatch):
    (tmp_path / "teille-douce.toml").write_text(
        '[paths]\noutput = "from-the-file"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    assert settings_for(["run", "--no-config"]).output_dir == Path("tei_output")


def test_a_config_file_named_and_missing_is_an_error(tmp_path):
    with pytest.raises(SystemExit) as excinfo:
        settings_for(["run", "--config", str(tmp_path / "absent.toml")])

    assert excinfo.value.code == 3


def test_an_explicit_console_level_beats_the_debug_setting():
    """-q asks for quiet. A debug flag left on in a config file must not
    override what the operator just typed."""
    settings = settings_for(["run", "-q"], env={"TDOUCE_DEBUG": "1"})

    assert settings.log_level == "ERROR"


# =============================================================================
# What the second review caught
# =============================================================================

@pytest.mark.parametrize("argv,expected", [
    (["--fast", "run", "--enrich"], {"enrich": True, "modernize": False, "ner": False}),
    (["run", "--fast", "--enrich"], {"enrich": True, "modernize": False, "ner": False}),
    (["--fast", "--enrich"], {"enrich": True, "modernize": False, "ner": False}),
])
def test_options_survive_the_subcommand_wherever_they_are_typed(argv, expected):
    """argparse's subparser copies its whole namespace over the parent's, so
    an accumulating option given on both sides replaced rather than merged:
    `--fast run --enrich` silently dropped the --fast and ran every phase."""
    settings = settings_for(argv)

    assert {p: getattr(settings, p) for p in expected} == expected


def test_repeatable_options_before_the_subcommand_are_not_dropped():
    args = app.parse_args(["-x", "A", "run", "-x", "B"])

    assert args.exclude == ["A", "B"]


def test_max_failures_must_be_a_number_of_failures():
    """`--max-failures 0` stopped the run after the first document, having
    converted it and failed nothing."""
    with pytest.raises(SystemExit) as excinfo:
        settings_for(["run", "--max-failures", "0"])

    assert excinfo.value.code == 2


def test_quiet_does_not_switch_the_debug_diagnostics_off():
    """-q asks for a quiet console. The debug setting also gates diagnostics
    written to the run log, which console verbosity has no business
    touching."""
    settings = settings_for(["run", "-q"], env={"TDOUCE_DEBUG": "1"})

    assert settings.log_level == "ERROR"
    assert settings.debug is True


def test_an_empty_modernization_url_keeps_the_default_and_says_so():
    """The one setting that skipped the shared validation — and the exact
    variable the user guide uses as its example of the guarantee."""
    from teille_douce.settings import Settings

    with pytest.warns(RuntimeWarning, match="TDOUCE_MODERNIZE_URL"):
        settings = Settings.load(env={"TDOUCE_MODERNIZE_URL": "  "}, flags={})

    assert settings.modernize_api["fra"] == "http://localhost:8011"


def test_a_config_file_value_of_the_wrong_type_is_refused_not_crashed(tmp_path):
    """TOML supplies integers; _resolve only caught ValueError, so
    `input = 42` raised TypeError out of Settings.load."""
    from teille_douce.settings import Settings

    toml = tmp_path / "teille-douce.toml"
    toml.write_text("[paths]\ninput = 42\n", encoding="utf-8")

    settings = Settings.load(env={}, flags={}, config_file=toml)

    assert settings.ocr_dir == Path("OCR")


def test_a_malformed_config_file_is_a_misconfiguration(tmp_path):
    toml = tmp_path / "teille-douce.toml"
    toml.write_text("[paths\ninput = 'x'\n", encoding="utf-8")

    with pytest.raises(SystemExit) as excinfo:
        settings_for(["run", "--config", str(toml)])

    assert excinfo.value.code == 3


# =============================================================================
# What the third review caught
# =============================================================================

def test_phases_given_empty_is_a_usage_error():
    """`--phases ""` used to mean "all", so a wrapper doing
    `--phases "$PHASES"` with PHASES unset turned every phase back on. A
    value typed and unusable is a usage error, like every other flag."""
    with pytest.raises(SystemExit) as excinfo:
        settings_for(["run", "--phases", ""])

    assert excinfo.value.code == 2


def test_a_lowercase_debug_level_still_turns_the_diagnostics_on():
    settings = settings_for(["run", "--log-level", "debug"])

    assert settings.log_level == "DEBUG"
    assert settings.debug is True


def test_limit_must_be_a_number_of_volumes():
    """`--limit -1` silently dropped the last volume and exited 0: a partial
    conversion reported as a success."""
    with pytest.raises(SystemExit) as excinfo:
        settings_for(["run", "--limit", "-1"])

    assert excinfo.value.code == 2


def test_a_per_language_url_is_validated_like_every_other_value():
    from teille_douce.settings import Settings

    settings = Settings.load(
        env={"TDOUCE_MODERNIZE_URL_FRA": "  http://host:8011  "}, flags={}
    )

    assert settings.modernize_api["fra"] == "http://host:8011"


def test_the_pyhellen_timeout_default_is_the_one_config_declares():
    from teille_douce import config
    from teille_douce.settings import Settings

    assert Settings.load(env={}, flags={}).pyhellen_timeout == \
        config.DEFAULT_PYHELLEN_TIMEOUT


def test_an_option_feeding_two_settings_is_reported_once():
    with pytest.raises(SystemExit):
        settings_for(["run", "--concurrency", "zero"])


# =============================================================================
# What the fourth review caught
# =============================================================================

def test_a_config_file_anchors_its_relative_paths_to_itself(tmp_path, monkeypatch):
    """Discovery walks up, so the file is found from a subdirectory — and a
    relative path in it then has to mean the same thing from there."""
    (tmp_path / "teille-douce.toml").write_text(
        '[paths]\ninput = "corpus"\n', encoding="utf-8"
    )
    deep = tmp_path / "a" / "b"
    deep.mkdir(parents=True)
    monkeypatch.chdir(deep)

    assert settings_for(["run"]).ocr_dir == tmp_path / "corpus"


def test_the_modernization_url_flag_is_validated_like_its_sibling():
    """--pyhellen strips; --vieuxparler bypassed _resolve entirely and let a
    stray space into the URL, which the service then refuses."""
    settings = settings_for(["run", "--vieuxparler", "  http://x:1  "])

    assert settings.modernize_api["fra"] == "http://x:1"


def test_limit_counts_what_will_actually_be_converted():
    """Applied before --skip-existing, `--skip-existing --limit N` stalled:
    every run took the same first N, skipped them all, and reported
    nothing to do while the rest of the corpus went untouched."""
    from teille_douce.cli.run import select_documents

    docs = [(f"LIV{n:04d}", [], None) for n in range(1, 5)]
    already_done = {"LIV0001", "LIV0002"}

    kept, _ = select_documents(
        docs, [], [], limit=1, skip=lambda name: name in already_done
    )

    assert [d[0] for d in kept] == ["LIV0003"]
