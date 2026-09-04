# -----------------------------------------------------------
# The settings object and its precedence chain.
#
# config.py used to read the environment in its module body, so a value was
# frozen at import time and `from teille_douce.config import X` bound it for
# good. Command-line flags are parsed after that import, which is why they
# could not override anything by mutating module globals -- that was the
# bug, not the fix. Settings.load() resolves the layers once, after
# parse_args, and nothing reads the environment behind its back.
#
# Run: venv/bin/python -m pytest tests/test_settings.py -q
# -----------------------------------------------------------
from pathlib import Path

import pytest

from teille_douce import config
from teille_douce.settings import Settings, get_settings, use_settings


# =============================================================================
# Precedence, resolved per setting
# =============================================================================

def test_an_unset_layer_falls_through_to_the_next():
    settings = Settings.load(env={}, flags={}, config_file=None)

    assert settings.ocr_dir == Path(config.DEFAULT_OCR_DIR)
    assert settings.origin("ocr_dir") == "default"


def test_the_environment_beats_the_default():
    settings = Settings.load(env={"TDOUCE_OCR_DIR": "/corpus"}, flags={})

    assert settings.ocr_dir == Path("/corpus")
    assert settings.origin("ocr_dir") == "env:TDOUCE_OCR_DIR"


def test_a_flag_beats_the_environment():
    settings = Settings.load(
        env={"TDOUCE_OCR_DIR": "/corpus"}, flags={"ocr_dir": "/elsewhere"}
    )

    assert settings.ocr_dir == Path("/elsewhere")
    assert settings.origin("ocr_dir") == "flag"


def test_a_flag_that_was_not_given_does_not_shadow_the_environment():
    """Precedence is resolved per setting, not per layer: passing --output
    must not silently reset an input directory the environment supplied."""
    settings = Settings.load(
        env={"TDOUCE_OCR_DIR": "/corpus"},
        flags={"ocr_dir": None, "output_dir": "/out"},
    )

    assert settings.ocr_dir == Path("/corpus")
    assert settings.origin("ocr_dir") == "env:TDOUCE_OCR_DIR"
    assert settings.output_dir == Path("/out")


def test_the_config_file_sits_between_the_environment_and_the_default(tmp_path):
    toml = tmp_path / "teille-douce.toml"
    toml.write_text(
        '[paths]\ninput = "/from-file"\noutput = "/out-from-file"\n',
        encoding="utf-8",
    )

    settings = Settings.load(
        env={"TDOUCE_OCR_DIR": "/from-env"}, flags={}, config_file=toml
    )

    assert settings.ocr_dir == Path("/from-env")
    assert settings.output_dir == Path("/out-from-file")
    assert settings.origin("output_dir") == f"config:{toml}"


def test_an_unknown_key_in_the_config_file_is_a_usage_error(tmp_path):
    """Silently ignoring a key is how a user spends an afternoon."""
    toml = tmp_path / "teille-douce.toml"
    toml.write_text('[paths]\ninpout = "/typo"\n', encoding="utf-8")

    with pytest.raises(ValueError) as excinfo:
        Settings.load(env={}, flags={}, config_file=toml)

    assert "inpout" in str(excinfo.value)


# =============================================================================
# Validation -- the guarantee config.py already made, applied to every layer
# =============================================================================

def test_a_value_set_but_empty_keeps_the_default_and_says_so():
    """`export TDOUCE_PYHELLEN_URL="${URL}"` with URL unset must not hand
    out an empty base URL that disables the service in silence."""
    with pytest.warns(RuntimeWarning, match="TDOUCE_PYHELLEN_URL"):
        settings = Settings.load(env={"TDOUCE_PYHELLEN_URL": "  "}, flags={})

    assert settings.pyhellen_url == config.DEFAULT_PYHELLEN_URL
    assert settings.origin("pyhellen_url") == "default"


@pytest.mark.parametrize("raw", ["nan", "inf", "-5", "not-a-number"])
def test_unusable_numbers_keep_the_default_and_say_why(raw):
    with pytest.warns(RuntimeWarning, match="TDOUCE_PYHELLEN_TIMEOUT"):
        settings = Settings.load(
            env={"TDOUCE_PYHELLEN_TIMEOUT": raw}, flags={}
        )

    assert settings.pyhellen_timeout == config.DEFAULT_PYHELLEN_TIMEOUT


def test_a_rejected_value_is_recorded_not_only_warned():
    """A RuntimeWarning scrolls past. The run has to be able to say, later,
    which settings it refused."""
    with pytest.warns(RuntimeWarning):
        settings = Settings.load(env={"TDOUCE_HEALTH_TIMEOUT": "wat"}, flags={})

    assert [r.name for r in settings.rejected] == ["TDOUCE_HEALTH_TIMEOUT"]
    assert "is not a number" in settings.rejected[0].reason


@pytest.mark.parametrize(
    "raw,expected", [("1", True), ("yes", True), ("ON", True),
                     ("0", False), ("no", False), ("Off", False)]
)
def test_booleans_read_the_words_they_always_read(raw, expected):
    settings = Settings.load(env={"TDOUCE_NER": raw}, flags={})

    assert settings.ner is expected


# =============================================================================
# The 13 documented variables are a contract
# =============================================================================

DOCUMENTED = [
    "TDOUCE_OCR_DIR", "TDOUCE_OUTPUT_DIR", "TDOUCE_ENRICHMENT",
    "TDOUCE_MODERNIZE", "TDOUCE_NER", "TDOUCE_PYHELLEN_URL",
    "TDOUCE_PYHELLEN_TIMEOUT", "TDOUCE_MODERNIZE_URL",
    "TDOUCE_MODERNIZE_TIMEOUT", "TDOUCE_MODERNIZE_SIMILARITY_MIN",
    "TDOUCE_HEALTH_TIMEOUT",
]


@pytest.mark.parametrize("name", DOCUMENTED)
def test_every_documented_variable_still_reaches_a_setting(name):
    """docs/user-guide.md advertises these names and says the prefix is kept
    so existing wrapper scripts and CI configurations keep working."""
    assert name in Settings.environment_variables()


def test_the_per_language_modernization_override_still_works():
    settings = Settings.load(
        env={"TDOUCE_MODERNIZE_URL_FRA": "http://fra:9000"}, flags={}
    )

    assert settings.modernize_api["fra"] == "http://fra:9000"


def test_the_bare_modernization_url_moves_every_language_without_an_override():
    settings = Settings.load(
        env={"TDOUCE_MODERNIZE_URL": "http://all:9000"}, flags={}
    )

    assert settings.modernize_api["fra"] == "http://all:9000"


# =============================================================================
# Process-wide access
# =============================================================================

def test_settings_are_built_lazily_from_the_environment_when_none_is_installed(
    monkeypatch
):
    """Importing a module in a REPL, a notebook or a test that never touches
    the CLI must behave exactly as it did before this object existed."""
    monkeypatch.setenv("TDOUCE_OCR_DIR", "/lazy")

    with use_settings(None):
        assert get_settings().ocr_dir == Path("/lazy")


def test_use_settings_restores_what_it_replaced():
    before = get_settings()

    with use_settings(ocr_dir=Path("/temporary")):
        assert get_settings().ocr_dir == Path("/temporary")

    assert get_settings() is before


def test_settings_are_frozen():
    settings = Settings.load(env={}, flags={})

    with pytest.raises(Exception):
        settings.ocr_dir = Path("/nope")


# =============================================================================
# The worker boundary
# =============================================================================

def test_a_worker_is_given_the_parent_s_settings():
    """A forkserver/spawn child re-imports in a fresh interpreter, where
    get_settings() would rebuild from the environment alone and therefore
    ignore every command-line flag. The pool already ships one document's
    invariants through its initializer; the settings travel with them.

    Nothing inside a worker reads a runtime setting today. The plumbing is
    here so that the first one to do so is correct rather than silently
    reading the environment behind the CLI's back.
    """
    from teille_douce.sourcedoc.builder import _init_worker

    chosen = Settings.load(env={"TDOUCE_OCR_DIR": "/from-the-parent"}, flags={})

    with use_settings(None):
        _init_worker("DOC1", [], [], {}, None, chosen)
        assert get_settings() is chosen


def test_settings_survive_a_process_boundary():
    """Settings is an initarg of the multiprocessing pool, so it has to
    pickle. A mappingproxy field does not — the frozen dataclass is what
    protects the settings, not the type of the mappings inside it."""
    import pickle

    settings = Settings.load(env={"TDOUCE_MODERNIZE_URL": "http://x:1"}, flags={})

    assert pickle.loads(pickle.dumps(settings)) == settings
