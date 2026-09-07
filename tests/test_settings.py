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
from teille_douce.settings import (SHARED_KEYS, Settings, _index_by_key,
                                   get_settings, use_settings)


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


def test_a_similarity_written_as_a_percentage_is_refused():
    """0.95 typed as 95: readable, out of range, and a guard that accepted
    it would reject every modernization the pipeline produces."""
    with pytest.warns(RuntimeWarning, match=r"outside \[0.0, 1.0\]"):
        settings = Settings.load(
            env={"TDOUCE_MODERNIZE_SIMILARITY_MIN": "95"}, flags={}
        )

    assert settings.modernize_similarity_min == 0.8


@pytest.mark.parametrize("raw", ["oui", "2", "", "  "])
def test_a_boolean_it_cannot_read_keeps_the_default(raw):
    with pytest.warns(RuntimeWarning, match="TDOUCE_NER"):
        settings = Settings.load(env={"TDOUCE_NER": raw}, flags={})

    assert settings.ner is True


def test_the_documented_table_lists_every_variable_the_code_reads():
    """The table in the user guide went thirteen entries out of date in one
    pull request. It is checked against the code rather than trusted."""
    import re
    from pathlib import Path

    guide = (Path(__file__).resolve().parent.parent
             / "docs" / "user-guide.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"`(TDOUCE_[A-Z_]+)`", guide))

    missing = Settings.environment_variables() - documented
    assert not missing, f"undocumented: {', '.join(sorted(missing))}"

    # And the other way: a row for a variable no setting answers to sends a
    # reader to set something that does nothing. The per-language family is
    # documented by its shape, so its example is exempt.
    invented = {name for name in documented
                if name not in Settings.environment_variables()
                and not name.startswith("TDOUCE_MODERNIZE_URL_")}
    assert not invented, f"documented but unread: {', '.join(sorted(invented))}"


def test_the_documented_table_names_every_config_key_too():
    """A key the file does not know is refused, so the list has to be
    findable: nothing lets a reader guess that TDOUCE_OCR_DIR is
    paths.input rather than paths.ocr_dir. The guide carries the mapping in
    the same table as the variables, and this keeps the two in step."""
    import re
    from pathlib import Path

    guide = (Path(__file__).resolve().parent.parent
             / "docs" / "user-guide.md").read_text(encoding="utf-8")
    documented = set(re.findall(r"`([a-z_]+\.[a-z_]+)`", guide))

    missing = Settings.config_keys() - documented
    assert not missing, f"undocumented: {', '.join(sorted(missing))}"


def test_a_rejected_value_names_what_the_run_actually_uses(tmp_path):
    """The message promised "keeping the default" even when a lower layer
    supplied something else, misdirecting the reader it exists to inform."""
    toml = tmp_path / "teille-douce.toml"
    toml.write_text("[limits]\njobs = 4\n", encoding="utf-8")

    with pytest.warns(RuntimeWarning, match="using 4"):
        settings = Settings.load(env={"TDOUCE_JOBS": "zero"}, flags={},
                                 config_file=toml)

    assert settings.max_workers == 4


def test_the_config_file_that_was_read_is_recorded(tmp_path):
    toml = tmp_path / "teille-douce.toml"
    toml.write_text("[limits]\njobs = 4\n", encoding="utf-8")

    settings = Settings.load(env={}, flags={}, config_file=toml)

    assert settings.origin("__config__") == str(toml)
    assert Settings.load(env={}, flags={}).origin("__config__") == "default"


# =============================================================================
# The config-key index
# =============================================================================

def test_two_settings_may_not_quietly_share_a_config_key():
    """The index used to be a dict comprehension, so a duplicate key was a
    silent win for the last row. `_read_config_file`'s path anchoring and
    the did-you-mean suggestion both look a key up here, so the day two
    rows diverge in converter one setting's value would be validated
    against the other's rules with nothing to say so."""
    from teille_douce.settings import _Declaration, _as_int, _as_str

    clash = [_Declaration("first", "TDOUCE_FIRST", "a.same", _as_int(), 1),
             _Declaration("second", "TDOUCE_SECOND", "a.same", _as_str, "x")]

    with pytest.raises(RuntimeError, match="first and second"):
        _index_by_key(clash)


def test_a_key_declared_shared_is_allowed_and_keeps_the_first_row():
    """--concurrency drives both services through limits.concurrency on
    purpose. Sharing is a declaration, not an accident."""
    from teille_douce.settings import _Declaration, _as_int

    rows = [_Declaration("first", "TDOUCE_FIRST", "limits.concurrency",
                         _as_int(minimum=1), 8),
            _Declaration("second", "TDOUCE_SECOND", "limits.concurrency",
                         _as_int(minimum=1), 8)]

    assert _index_by_key(rows)["limits.concurrency"].name == "first"


def test_the_shipped_declarations_share_only_what_is_declared():
    from teille_douce.settings import _SETTINGS

    keys = [d.key for d in _SETTINGS if d.key]
    repeated = {k for k in keys if keys.count(k) > 1}

    assert repeated == set(SHARED_KEYS)


def test_both_sharers_of_a_key_convert_alike():
    """One row wins the index, so the loser's value is validated by the
    winner's converter. That is only safe while they agree."""
    from teille_douce.settings import _SETTINGS

    for key in SHARED_KEYS:
        sharers = [d for d in _SETTINGS if d.key == key]
        assert len({d.convert(4) for d in sharers}) == 1, key
        for declaration in sharers:
            with pytest.raises(ValueError):
                declaration.convert(0)


# =============================================================================
# What a path in a config file means
# =============================================================================

def test_a_relative_path_in_the_config_file_is_read_next_to_that_file(tmp_path):
    """The file is found by walking up, so anchoring on the working
    directory would make one line mean something different in every
    subdirectory it was written to serve."""
    toml = tmp_path / "teille-douce.toml"
    toml.write_text('[paths]\noutput = "tei"\n', encoding="utf-8")

    settings = Settings.load(env={}, flags={}, config_file=toml)

    assert settings.output_dir == tmp_path / "tei"


@pytest.mark.parametrize("written", ['""', '"   "'])
def test_an_empty_path_in_the_config_file_is_refused_like_any_other(tmp_path,
                                                                   written):
    """Anchoring ran ahead of the layer's own validation, and `Path("")` is
    `Path(".")`: joining it handed back the config file's own directory, a
    real non-empty path the "is empty" check could no longer see. So
    `[paths] output = ""` — the wrapper idiom refused everywhere else in
    this pipeline — quietly meant "write the corpus beside the config
    file", with no warning and nothing recorded."""
    toml = tmp_path / "teille-douce.toml"
    toml.write_text(f"[paths]\noutput = {written}\n", encoding="utf-8")

    with pytest.warns(RuntimeWarning, match="is empty"):
        settings = Settings.load(env={}, flags={}, config_file=toml)

    assert settings.output_dir == Path(config.DEFAULT_OUTPUT_DIR)
    assert settings.origin("output_dir") == "default"
    assert any(r.name == "paths.output" for r in settings.rejected)


def test_a_tilde_in_a_path_names_the_home_directory(tmp_path, monkeypatch):
    """A shell expands ~ before a variable is ever read, but a config file
    and a quoted flag do not: "~/tei" used to become a directory literally
    named "~", created under whatever the anchor was."""
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    toml = tmp_path / "teille-douce.toml"
    toml.write_text('[paths]\noutput = "~/tei"\n', encoding="utf-8")

    assert Settings.load(env={}, flags={},
                         config_file=toml).output_dir == home / "tei"
    assert Settings.load(env={"TDOUCE_OUTPUT_DIR": "~/tei"},
                         flags={}).output_dir == home / "tei"


def test_a_tilde_naming_nobody_is_refused_rather_than_raised(tmp_path):
    """`Path("~ghost/x").expanduser()` raises RuntimeError, which is not
    one of the two exceptions the layer catches: it escaped as a traceback
    instead of the refusal every other unusable value gets. The anchoring
    step meets it first and hands the value on untouched, so a single
    place answers for it."""
    toml = tmp_path / "teille-douce.toml"
    toml.write_text('[paths]\noutput = "~personnenexistepas4711/tei"\n',
                    encoding="utf-8")

    with pytest.warns(RuntimeWarning, match="home directory"):
        settings = Settings.load(env={}, flags={}, config_file=toml)

    assert settings.output_dir == Path(config.DEFAULT_OUTPUT_DIR)
    assert settings.origin("output_dir") == "default"


def test_a_tilde_naming_nobody_in_the_environment_is_refused_too():
    with pytest.warns(RuntimeWarning, match="home directory"):
        settings = Settings.load(
            env={"TDOUCE_OUTPUT_DIR": "~personnenexistepas4711/tei"}, flags={})

    assert settings.output_dir == Path(config.DEFAULT_OUTPUT_DIR)


# =============================================================================
# What the run manifest records
# =============================================================================

def test_the_manifest_records_each_value_with_where_it_came_from():
    """Three days later, "why did it write there" is answered by the
    origin, not by the value."""
    settings = Settings.load(flags={"output_dir": "/tmp/out"}, env={})

    manifest = settings.as_manifest()

    assert manifest["paths.output"] == {"value": "/tmp/out", "origin": "flag"}
    assert manifest["paths.input"]["origin"] == "default"


def test_the_manifest_is_json_serialisable():
    """It is written to run.json, so a Path or an Enum in it would only
    be discovered at the end of a four-hour run."""
    import json

    json.dumps(Settings.load(flags={}, env={}).as_manifest())


def test_the_manifest_covers_every_declared_setting():
    """A setting missing from it is a setting nobody can account for
    afterwards."""
    from teille_douce.settings import Settings as S

    manifest = S.load(flags={}, env={}).as_manifest()

    assert set(manifest) >= S.config_keys()


def test_le_schema_de_test_ne_bloque_pas_une_conversion():
    """TDOUCE_TEI_RNG ne sert qu'a tests/test_e2e_pipeline.py, et
    CLAUDE.md dit aux contributeurs de le poser. Declare dans READS, une
    valeur perimee restee dans un profil de shell refusait TOUTE
    conversion — sortie 3, « un fichier que ce run doit lire » — a propos
    d'un fichier qu'aucun run n'ouvre."""
    from pathlib import Path

    from teille_douce.settings import Settings

    reglages = Settings.load(flags={}, env={"TDOUCE_TEI_RNG": "/nulle/part.rng"},
                             config_file=None)

    assert reglages.tei_rng == Path("/nulle/part.rng")
    assert "tei_rng" not in {nom for nom, _, _, _ in Settings.READS}
    assert reglages.unreadable_inputs() == ()


def test_un_chemin_illisible_nomme_la_couche_qui_l_a_fourni(tmp_path):
    """Le message disait toujours `-i:`, meme quand la valeur venait de
    TDOUCE_OCR_DIR ou du fichier de configuration : il nommait un drapeau
    que l'operateur n'avait pas tape."""
    from teille_douce.settings import Settings

    par_env = Settings.load(flags={}, env={"TDOUCE_OCR_DIR": "/nulle/part"},
                            config_file=None)
    (_, _, _, ou_env), = par_env.unreadable_inputs()
    assert ou_env == "TDOUCE_OCR_DIR"

    fichier = tmp_path / "teille-douce.toml"
    fichier.write_text('[paths]\ninput = "/nulle/part"\n', encoding="utf-8")
    par_toml = Settings.load(flags={}, env={}, config_file=fichier)
    (_, _, _, ou_toml), = par_toml.unreadable_inputs()
    assert ou_toml.startswith("paths.input in ") and str(fichier) in ou_toml

    par_drapeau = Settings.load(flags={"ocr_dir": "/nulle/part"}, env={},
                                config_file=None)
    (_, _, _, ou_drapeau), = par_drapeau.unreadable_inputs()
    assert ou_drapeau == "-i"
