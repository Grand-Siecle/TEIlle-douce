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
    args = app.build_parser().parse_args(argv)
    return app.settings_from(args, env=env or {})


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
