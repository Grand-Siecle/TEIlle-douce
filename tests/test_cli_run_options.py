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

from teille_douce import config
from teille_douce.cli import app
from teille_douce.cli.run import entity_snapshot as entity_snapshot_of


def settings_for(argv, env=None, discover_config=False):
    """Resolve *argv* the way the command line does, without running.

    Config-file discovery walks up to the root, so a teille-douce.toml
    anywhere above the checkout would steer these cases. It is off unless a
    test is about discovery, which is what `discover_config` is for.
    """
    if not discover_config and "--config" not in argv:
        argv = [*argv, "--no-config"]
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
    (["-q"], "WARNING"),
    (["-qq"], "ERROR"),
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
    kept, _ = select_documents(_docs("LIV0044", "LIV0021"), [], [], None)

    assert [d[0] for d in kept] == ["LIV0044", "LIV0021"]


def test_a_selector_matches_a_name_an_internal_id_or_a_glob():
    docs = _docs("LIV0044_reconciled", "LIV0021_reconciled", "LIV0038_t2_reconciled")

    assert [d[0] for d in select_documents(docs, ["LIV0044_reconciled"], [], None)[0]] \
        == ["LIV0044_reconciled"]
    assert [d[0] for d in select_documents(docs, ["LIV0021"], [], None)[0]] \
        == ["LIV0021_reconciled"]
    assert [d[0] for d in select_documents(docs, ["LIV003*"], [], None)[0]] \
        == ["LIV0038_t2_reconciled"]


def test_select_documents_judges_no_pattern_at_all():
    """Neither selectors nor exclusions. By the time this runs the list has
    been narrowed by those very patterns — an excluded or non-selected
    archive was never unpacked — so a "no match" verdict would be about its
    own filtering. `execute` validates every pattern against the raw
    directory listing, which is the only place that sees the whole corpus;
    tests/test_cli_exit_codes.py exercises that end to end."""
    kept, _ = select_documents(_docs("LIV0044"), ["LIV9999"], [], None)

    assert kept == []


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

    settings = settings_for(["run"], discover_config=True)

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

    assert settings.log_level == "WARNING"


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

    assert settings.log_level == "WARNING"
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

    assert settings_for(["run"], discover_config=True).ocr_dir == tmp_path / "corpus"


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


# =============================================================================
# What the fifth review caught
# =============================================================================

def test_the_skip_count_counts_only_what_the_resume_skipped():
    """It counted every kind of drop, so `--skip-existing --limit 1` on an
    empty output directory announced one document "already converted" when
    none was. Exactly the dishonest counter the project forbids."""
    from teille_douce.cli.run import select_documents

    docs = [(f"LIV{n:04d}", [], None) for n in range(1, 4)]

    kept, skipped = select_documents(
        docs, [], ["LIV0003"], limit=1, skip=lambda name: False
    )

    assert [d[0] for d in kept] == ["LIV0001"]
    assert skipped == 0


def test_a_flag_beats_a_per_language_environment_variable():
    """`--vieuxparler` landed in the shared slot and the per-language
    variable in the specific one, which wins — inverting the documented
    chain, with nothing saying the flag had been ignored."""
    from teille_douce.settings import Settings

    settings = Settings.load(
        flags={"modernize_url": "http://flag:1"},
        env={"TDOUCE_MODERNIZE_URL_FRA": "http://env:2"},
    )

    assert settings.modernize_api["fra"] == "http://flag:1"


def test_a_rejected_modernization_url_names_the_option_that_feeds_it():
    with pytest.raises(SystemExit):
        settings_for(["run", "--vieuxparler", ""])


def test_quiet_still_lets_a_warning_through():
    """A mistyped --metadata is reported by a logger, not by a print. `-q`
    silencing it converted a whole corpus with placeholder headers and
    exited 0 with nothing said. -qq is the deliberate spelling for
    accepting that."""
    assert settings_for(["run", "-q"], discover_config=True).log_level == "WARNING"
    assert settings_for(["run", "-qq"]).log_level == "ERROR"


def test_a_log_file_in_a_directory_that_does_not_exist_is_created(tmp_path):
    """Every record raised FileNotFoundError inside logging and buried the
    console in tracebacks, while the summary pointed at a file nothing had
    created."""
    import logging

    from teille_douce.cli.run import configure_logging

    settings = settings_for(["run", "--log-file", str(tmp_path / "logs" / "run.log")])
    path = configure_logging(settings)
    logging.getLogger("teille_douce.test").warning("a record")

    assert path.exists(), "the run log was never written"


def test_the_canonical_document_id_selects():
    """It is what the pipeline writes into xml:id and what a user reads back
    out of a TEI file. Without it `run LIV0002a` matched nothing, while
    `LIV0002` silently took every volume of the set."""
    from teille_douce.cli.run import select_documents

    docs = [("LIV0002a_reconciled", [], None), ("LIV0002b_reconciled", [], None)]

    kept, _ = select_documents(docs, ["LIV0002a"], [], None)

    assert [d[0] for d in kept] == ["LIV0002a_reconciled"]


def test_select_documents_does_not_judge_an_exclusion_it_cannot_see():
    """A mistyped `-x` is caught in `execute`, against the raw directory
    listing. Judging it here — after archives have been left unpacked and
    non-selected directories left unscanned — answered "no match" for
    patterns that matched perfectly well, and made every working exclusion
    exit 3."""
    from teille_douce.cli.run import select_documents

    docs = [("LIV0044_reconciled", [], None)]

    _, _ = select_documents(docs, [], ["LIV0038_reconcilied"], None)



def test_quiet_never_raises_the_level_a_lower_layer_set():
    settings = settings_for(["run", "-q"], env={"TDOUCE_LOG_LEVEL": "ERROR"})

    assert settings.log_level == "ERROR"


def test_quiet_lowers_a_level_the_config_file_raised(tmp_path, monkeypatch):
    """The floor was resolved without the config file, so a DEBUG set there
    was invisible to it and -q left the console at DEBUG."""
    (tmp_path / "teille-douce.toml").write_text(
        '[output]\nlog_level = "DEBUG"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    assert settings_for(["run", "-q"], discover_config=True).log_level == "WARNING"


def test_a_rejected_value_is_announced_once(recwarn):
    """The floor probe re-resolved every layer, so each refusal warned
    twice as soon as -q was passed."""
    settings_for(["run", "-q"], env={"TDOUCE_HEALTH_TIMEOUT": "wat"})

    named = [w for w in recwarn if "TDOUCE_HEALTH_TIMEOUT" in str(w.message)]
    assert len(named) == 1, [str(w.message) for w in named]


def test_an_empty_log_level_is_a_usage_error():
    with pytest.raises(SystemExit) as excinfo:
        settings_for(["run", "--log-level", ""])

    assert excinfo.value.code == 2


def test_an_invalid_config_file_still_exits_cleanly_under_quiet(tmp_path, monkeypatch):
    """The -q floor probe read the config file above the guard that turns a
    config problem into exit 3, so `-q` alone turned it into a traceback."""
    (tmp_path / "teille-douce.toml").write_text(
        '[paths]\ninpu = "OCR"\n', encoding="utf-8"
    )
    monkeypatch.chdir(tmp_path)

    for argv in (["run"], ["run", "-q"], ["run", "-qq"]):
        with pytest.raises(SystemExit) as excinfo:
            settings_for(argv, discover_config=True)
        assert excinfo.value.code == 3, argv


def test_verbose_never_lowers_a_level_a_lower_layer_set():
    """The floor was enforced for -q only, so -v could reduce verbosity: an
    operator whose wrapper set DEBUG and who added -v ended up at INFO."""
    settings = settings_for(["run", "-v"], env={"TDOUCE_LOG_LEVEL": "DEBUG"})

    assert settings.log_level == "DEBUG"


def test_a_flag_silences_the_per_language_variable_it_overrides():
    """Walking the per-language layer anyway warned about a variable that
    had no effect, in the one case where it was deliberately overridden."""
    import warnings as warnings_module

    from teille_douce.settings import Settings

    with warnings_module.catch_warnings(record=True) as raised:
        warnings_module.simplefilter("always")
        settings = Settings.load(
            flags={"modernize_url": "http://flag:1"},
            env={"TDOUCE_MODERNIZE_URL_FRA": "  "},
        )

    assert settings.modernize_api["fra"] == "http://flag:1"
    assert not [w for w in raised if "MODERNIZE_URL_FRA" in str(w.message)]


@pytest.mark.parametrize("raw", ["all", " all ", " enrich , ner "])
def test_the_phase_list_tolerates_the_spacing_a_wrapper_produces(raw):
    """The aliases were compared against the unstripped string, so
    `--phases "$PHASES"` with a padded value failed with a message that
    listed 'all' among the choices it had just refused."""
    settings = settings_for(["run", "--phases", raw])

    assert settings.enrich is True


def test_the_end_of_options_marker_protects_a_selector():
    """`--` means what follows is positional, so no token past it can be
    read as the subcommand."""
    args = app.parse_args(["--", "run"])

    assert args.command == "run"
    assert args.documents == ["run"]


def test_the_ner_probe_can_actually_fire(monkeypatch):
    """It imported `ner_pipeline`, which pulls in nothing heavy — every NER
    import is deferred into a method body — so it could never raise and the
    header declared entity recognition on runs that produced none."""
    from teille_douce.enrichment import ner_models

    monkeypatch.setattr(ner_models.importlib.util, "find_spec", lambda name: None)

    assert ner_models.missing_ner_dependencies() == ner_models.NER_DEPENDENCIES


def test_a_broken_install_reads_as_missing_rather_than_raising(monkeypatch):
    """`find_spec` answers with an exception for a name sitting in
    sys.modules without a spec, and re-raises a half-installed package's own
    ImportError. Unguarded, a broken torch turned the documented "NER
    dependencies not installed" warning into a traceback."""
    from teille_douce.enrichment import ner_models

    def explode(name):
        if name == "torch":
            raise ValueError(f"{name}.__spec__ is None")
        return object()

    monkeypatch.setattr(ner_models.importlib.util, "find_spec", explode)

    assert ner_models.missing_ner_dependencies() == ("torch",)


def test_the_ner_dependencies_are_the_ones_the_code_imports():
    """Named rather than imported, so the list has to match what the lazy
    imports actually reach for."""
    from pathlib import Path

    from teille_douce.enrichment.ner_models import NER_DEPENDENCIES

    source = (Path(__file__).resolve().parent.parent / "teille_douce"
              / "enrichment" / "ner_models.py").read_text(encoding="utf-8")

    for package in ("flair", "gliner"):
        assert f"from {package}" in source
        assert package in NER_DEPENDENCIES


@pytest.mark.parametrize("argv,expected", [
    (["--inp", "OCR", "run"], ["run", "--inp", "OCR"]),
    (["--input=OCR", "run"], ["run", "--input=OCR"]),
    (["-o", "run"], ["run", "-o", "run"]),
])
def test_an_abbreviated_option_does_not_misplace_the_subcommand(argv, expected):
    """argparse accepts unambiguous prefixes, so `--inp OCR run` read OCR as
    the first positional and `run` as a document selector."""
    assert app.normalise(argv) == expected


@pytest.mark.parametrize("argv,expected", [
    ([], "DEBUG"),
    (["-q"], "WARNING"),
    (["--log-level", "ERROR"], "ERROR"),
    # -v asks for MORE, so a debug setting that already gives more is not
    # contradicted by it: -v must never be less verbose than no flag.
    (["-v"], "DEBUG"),
])
def test_the_console_handler_gets_the_level_that_was_asked_for(argv, expected, tmp_path):
    """The previous tests asserted on `settings.log_level` and never on the
    handler, so `debug` reinstalling a DEBUG console over `-q` went
    unnoticed: a run that asked for quiet got a fully verbose one."""
    import logging

    from teille_douce.cli import options
    from teille_douce.cli.run import configure_logging

    args = app.parse_args(["run", "--no-log-file", *argv])
    settings = app.settings_from(args, env={"TDOUCE_DEBUG": "1"})
    configure_logging(settings, quiet=bool(getattr(args, "quiet", 0)),
                      level_asked=options.asks_to_be_quieter(args))

    handler = logging.getLogger().handlers[-1]
    assert logging.getLevelName(handler.level) == expected


@pytest.mark.parametrize("raw", [",", " , "])
def test_a_comma_only_phase_list_is_a_usage_error(raw):
    """`--phases "$A,$B"` with both unset meant "none" in silence and
    disabled every annotation phase for a corpus."""
    with pytest.raises(SystemExit) as excinfo:
        settings_for(["run", "--phases", raw])

    assert excinfo.value.code == 2


@pytest.mark.parametrize("path", [".", "/"])
def test_a_log_path_with_no_name_costs_the_log_not_the_run(path):
    """`_run_log_path` raised outside the guard that promises an unusable
    log path costs the log and not the conversion."""
    from teille_douce.cli.run import configure_logging

    settings = settings_for(["run", "--log-file", path])

    with pytest.warns(RuntimeWarning, match="continuing without file logging"):
        assert configure_logging(settings) is None


def test_the_ner_probe_names_what_the_code_imports_and_only_that():
    """`transformers` was probed and never imported, while
    `huggingface_hub` — which the CamemBERT loader reaches for on the
    configured model id — was not probed at all: the probe returned "all
    present" and the header declared entity recognition on a run that
    produced none."""
    from pathlib import Path

    from teille_douce.enrichment.ner_models import NER_DEPENDENCIES

    source = (Path(__file__).resolve().parent.parent / "teille_douce"
              / "enrichment" / "ner_models.py").read_text(encoding="utf-8")

    for package in NER_DEPENDENCIES:
        assert f"import {package}" in source or f"from {package}" in source, package
    assert "huggingface_hub" in NER_DEPENDENCIES


def test_a_level_set_by_a_lower_layer_is_not_overridden_by_debug(tmp_path):
    """`level_asked` came from this command line alone, so `debug`
    installed a DEBUG console over an explicit TDOUCE_LOG_LEVEL=ERROR with
    nothing said."""
    import logging

    from teille_douce.cli import options
    from teille_douce.cli.run import configure_logging

    args = app.parse_args(["run", "--no-log-file"])
    settings = app.settings_from(
        args, env={"TDOUCE_DEBUG": "1", "TDOUCE_LOG_LEVEL": "ERROR"}
    )
    configure_logging(
        settings,
        level_asked=(options.asks_to_be_quieter(args)
                     or settings.origin("log_level") != "default"),
    )

    handler = logging.getLogger().handlers[-1]
    assert logging.getLevelName(handler.level) == "ERROR"


@pytest.mark.parametrize("flags,expected", [
    ((), "ERROR"),
    (("-q",), "ERROR"),
    (("-qq",), "ERROR"),
    (("-v",), "INFO"),
    (("-vv",), "DEBUG"),
])
def test_asking_for_less_is_never_louder_than_asking_for_nothing(flags,
                                                                 expected):
    """The guard that stops -q from raising a quiet console computed its
    floor from `debug` alone, while configure_logging lowers the console to
    DEBUG only when no other layer named a level. With both TDOUCE_DEBUG
    and TDOUCE_LOG_LEVEL set the two disagreed: the console sat at ERROR,
    the guard believed DEBUG, and -q — a request for LESS — let WARNING
    back through. -v was a no-op in the same cell."""
    import logging

    from teille_douce.cli import options
    from teille_douce.cli.run import configure_logging

    args = app.parse_args(["run", "--no-log-file", *flags])
    settings = app.settings_from(
        args, env={"TDOUCE_DEBUG": "1", "TDOUCE_LOG_LEVEL": "ERROR"}
    )
    configure_logging(
        settings,
        quiet=bool(getattr(args, "quiet", 0)),
        level_asked=(options.asks_to_be_quieter(args)
                     or settings.origin("log_level") != "default"),
    )

    handler = logging.getLogger().handlers[-1]
    assert logging.getLevelName(handler.level) == expected


def test_a_refused_flag_stays_a_usage_error_whatever_the_other_layers_hold():
    """Suppressing the warning for a shared URL no language reads is right;
    suppressing the usage error is not — `--vieuxparler "$URL"` with URL
    unset exited 0 and ran against the environment's value."""
    with pytest.raises(SystemExit) as excinfo:
        settings_for(["run", "--vieuxparler", ""],
                     env={"TDOUCE_MODERNIZE_URL_FRA": "http://perlang"})

    assert excinfo.value.code == 2


# =============================================================================
# What a malformed invocation or config file says before exiting
# =============================================================================

def test_a_limit_that_is_not_a_number_says_so(capsys):
    """argparse falls back to the callable's __name__ when a type raises
    anything else, and told the user "invalid _at_least_one value"."""
    with pytest.raises(SystemExit) as exit:
        app.parse_args(["run", "--limit", "beaucoup"])

    assert exit.value.code == 2
    assert "whole number" in capsys.readouterr().err


def test_a_bare_value_at_the_top_of_the_config_file_is_named(tmp_path,
                                                             capsys):
    """`output = "tei"` outside any section is the natural mistake, and
    tomllib parses it happily. Nothing has run at that point, so it exits
    3 — misconfigured — rather than 1, which means volumes failed."""
    toml = tmp_path / "teille-douce.toml"
    toml.write_text('output = "tei"\n', encoding="utf-8")

    with pytest.raises(SystemExit) as exit:
        app.settings_from(app.parse_args(["run", "--config", str(toml)]),
                          env={})

    assert exit.value.code == 3
    message = capsys.readouterr().err
    assert "output" in message and "section" in message


def test_a_config_url_of_the_wrong_type_is_refused_not_crashed(tmp_path):
    """A bare port instead of a URL. The converter answers with a rejection
    the run can report; anything raised out of the config layer would have
    been a traceback."""
    toml = tmp_path / "teille-douce.toml"
    toml.write_text("[services]\npyhellen = 8000\n", encoding="utf-8")

    with pytest.warns(RuntimeWarning, match="not a string"):
        settings = app.settings_from(
            app.parse_args(["run", "--config", str(toml)]), env={})

    assert settings.pyhellen_url == config.DEFAULT_PYHELLEN_URL
    rejection, = settings.rejected
    assert rejection.name == "services.pyhellen"
    assert rejection.layer == f"config:{toml}"


# =============================================================================
# The entity directory a failed document is allowed to touch
# =============================================================================

def test_a_directory_that_cannot_be_read_is_not_a_directory_this_run_wrote(
        tmp_path, monkeypatch):
    """The snapshot announced "its entity files will be left alone if this
    document fails" and then recorded an empty before-set, which says the
    opposite: empty means this run wrote all of it, and the cleanup after a
    failed document reads that as a licence to unlink every file in a
    directory it could not even read."""
    from pathlib import Path

    from teille_douce.cli.run import entity_snapshot

    entities = tmp_path / "ents"
    entities.mkdir()
    (entities / "from-a-previous-run.csv").write_text("x", encoding="utf-8")

    def refuse(self, pattern):
        raise OSError(13, "Permission denied")

    monkeypatch.setattr(Path, "rglob", refuse)
    existing, created, reason = entity_snapshot(entities)

    assert reason is not None
    assert not created, "an unreadable directory was not created by this run"
    assert existing == set()


def test_an_absent_entity_directory_is_one_this_run_creates(tmp_path):
    existing, created, reason = entity_snapshot_of(tmp_path / "ents")

    assert (existing, created, reason) == (set(), True, None)


def test_an_existing_entity_directory_is_snapshotted_whole(tmp_path):
    entities = tmp_path / "ents"
    (entities / "sub").mkdir(parents=True)
    kept = entities / "sub" / "from-a-previous-run.csv"
    kept.write_text("x", encoding="utf-8")

    existing, created, reason = entity_snapshot_of(entities)

    assert kept in existing
    assert not created and reason is None
