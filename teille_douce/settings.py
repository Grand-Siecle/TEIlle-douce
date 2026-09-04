"""
The settings of one run, resolved once and then read-only.

`config.py` used to read the environment in its own module body. A value
was therefore frozen at import time, and `from teille_douce.config import X`
bound it for good — which is why command-line flags, parsed later, could
not override anything by mutating module globals. That is the bug, not the
fix.

Here the layers are resolved once, after the arguments are parsed:

    flag  >  environment (TDOUCE_*)  >  config file  >  config.py default

**Per setting, not per layer**: a flag that was not given does not shadow
what the environment supplied.

The layers do not overlap in scope. `config.py` describes *the project* —
the responsibility statement, the software versions, the certainty
thresholds, the taxonomies — and those become claims inside the TEI
header, so no per-machine layer may touch them. Only the runtime settings
declared in `_SETTINGS` below are overridable.
"""

import contextlib
import math
import os
import tomllib
import warnings
from dataclasses import dataclass, field, replace
from pathlib import Path

from teille_douce import config


# =============================================================================
# Converters
#
# Moved verbatim from config.py, where they only ever saw the environment.
# They now gate three layers, so a bad value in a TOML file is refused
# exactly as a bad environment value is.
# =============================================================================

def _as_path(raw):
    # A TOML file supplies integers and booleans too, and Path(42) raises
    # TypeError, which is not what the layers promise to do with a value
    # they cannot use.
    if not isinstance(raw, (str, Path)):
        raise ValueError("is not a path")
    return Path(raw)


def _as_str(raw):
    if not isinstance(raw, str):
        raise ValueError("is not a string")
    return raw


def _as_bool(raw):
    if isinstance(raw, bool):
        return raw
    value = str(raw).lower()
    if value in ("1", "true", "yes", "on"):
        return True
    if value in ("0", "false", "no", "off"):
        return False
    raise ValueError("is not a boolean")


# The names logging understands. `getattr(logging, "info")` resolves to a
# *function*, which a handler then rejects with an unreadable TypeError, so
# the level is validated here rather than trusted downstream.
_LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")


def _as_level(raw):
    value = str(raw).strip().upper()
    if value not in _LOG_LEVELS:
        raise ValueError(f"is not one of {', '.join(_LOG_LEVELS)}")
    return value


def _as_int(minimum=None):
    def convert(raw):
        try:
            value = int(str(raw))
        except ValueError:
            raise ValueError("is not a whole number") from None
        if minimum is not None and value < minimum:
            raise ValueError(f"is below {minimum}")
        return value
    return convert


def _as_number(minimum=None, maximum=None):
    """A finite float within [minimum, maximum].

    Parsing alone is not enough: float("nan") and float("inf") both
    succeed, and a NaN threshold turns every comparison against it into
    False — the guard reading it would stop rejecting anything.
    """
    def convert(raw):
        try:
            value = float(str(raw))
        except ValueError:
            raise ValueError("is not a number") from None
        if not math.isfinite(value):
            raise ValueError("is not a finite number")
        if (minimum is not None and value < minimum) or (
            maximum is not None and value > maximum
        ):
            low = "-inf" if minimum is None else minimum
            high = "+inf" if maximum is None else maximum
            raise ValueError(f"is outside [{low}, {high}]")
        return value
    return convert


# =============================================================================
# The declaration table
#
# One row per runtime setting: the attribute, the environment variable, the
# "section.key" it answers to in the config file, its converter and its
# default. Declaring a setting once is what keeps the four layers, the
# documentation table and the CLI from drifting apart.
# =============================================================================

@dataclass(frozen=True, slots=True)
class _Declaration:
    name: str
    env: str | None
    key: str | None
    convert: object
    default: object


_SETTINGS = (
    # paths
    _Declaration("ocr_dir", "TDOUCE_OCR_DIR", "paths.input",
                 _as_path, config.DEFAULT_OCR_DIR),
    _Declaration("output_dir", "TDOUCE_OUTPUT_DIR", "paths.output",
                 _as_path, config.DEFAULT_OUTPUT_DIR),
    _Declaration("entities_dir", "TDOUCE_ENTITIES_DIR", "paths.entities",
                 _as_path, config.DEFAULT_ENTITIES_DIR),
    _Declaration("metadata_csv", "TDOUCE_METADATA_CSV", "paths.metadata",
                 _as_path, config.DEFAULT_METADATA_CSV),
    _Declaration("persons_csv", "TDOUCE_PERSONS_CSV", "paths.persons",
                 _as_path, config.DEFAULT_PERSONS_CSV),
    # phases
    _Declaration("enrich", "TDOUCE_ENRICHMENT", "phases.enrich",
                 _as_bool, True),
    _Declaration("modernize", "TDOUCE_MODERNIZE", "phases.modernize",
                 _as_bool, True),
    _Declaration("ner", "TDOUCE_NER", "phases.ner", _as_bool, True),
    # services
    _Declaration("pyhellen_url", "TDOUCE_PYHELLEN_URL", "services.pyhellen",
                 _as_str, config.DEFAULT_PYHELLEN_URL),
    _Declaration("pyhellen_timeout", "TDOUCE_PYHELLEN_TIMEOUT",
                 "services.pyhellen_timeout", _as_number(minimum=0.1),
                 config.DEFAULT_PYHELLEN_TIMEOUT),
    _Declaration("modernize_timeout", "TDOUCE_MODERNIZE_TIMEOUT",
                 "services.modernize_timeout", _as_number(minimum=0.1), 300.0),
    _Declaration("health_timeout", "TDOUCE_HEALTH_TIMEOUT",
                 "services.health_timeout", _as_number(minimum=0.1), 30.0),
    # limits
    _Declaration("max_workers", "TDOUCE_JOBS", "limits.jobs",
                 _as_int(minimum=1), 8),
    _Declaration("modernize_batch_size", "TDOUCE_MODERNIZE_BATCH_SIZE",
                 "limits.batch_size", _as_int(minimum=1), 64),
    _Declaration("modernize_concurrency", "TDOUCE_MODERNIZE_CONCURRENCY",
                 "limits.concurrency", _as_int(minimum=1), 8),
    _Declaration("pyhellen_concurrency", "TDOUCE_PYHELLEN_CONCURRENCY",
                 "limits.concurrency", _as_int(minimum=1), 8),
    _Declaration("pyhellen_max_consecutive_failures",
                 "TDOUCE_PYHELLEN_MAX_CONSECUTIVE_FAILURES",
                 "limits.max_consecutive_failures", _as_int(minimum=1), 10),
    _Declaration("modernize_similarity_min", "TDOUCE_MODERNIZE_SIMILARITY_MIN",
                 "limits.similarity_min", _as_number(minimum=0.0, maximum=1.0),
                 0.8),
    _Declaration("ner_confidence_threshold", "TDOUCE_NER_CONFIDENCE",
                 "limits.ner_confidence", _as_number(minimum=0.0, maximum=1.0),
                 0.6),
    # logging
    _Declaration("debug", "TDOUCE_DEBUG", "output.debug", _as_bool, False),
    _Declaration("log_file", "TDOUCE_LOG_FILE", "output.log_file",
                 _as_path, config.DEFAULT_LOG_FILE),
    _Declaration("log_level", "TDOUCE_LOG_LEVEL", "output.log_level",
                 _as_level, "WARNING"),
    # A resume asked for by a config file or the environment is what
    # --force cancels; without a layer of its own it had nothing to cancel.
    _Declaration("skip_existing", "TDOUCE_SKIP_EXISTING", "output.skip_existing",
                 _as_bool, False),
    # tests
    _Declaration("tei_rng", "TDOUCE_TEI_RNG", None, _as_path, None),
)

_BY_NAME = {d.name: d for d in _SETTINGS}
_BY_KEY = {d.key: d for d in _SETTINGS if d.key}
# declared after _SETTINGS: see _MODERNIZE_URL below


# The modernization base URLs are a mapping, not a scalar, so they are
# resolved on their own: TDOUCE_MODERNIZE_URL moves every language that has
# no TDOUCE_MODERNIZE_URL_<IDENT> of its own.
_MODERNIZE_URL_ENV = "TDOUCE_MODERNIZE_URL"

# The base URL shared by every language that has no override of its own.
# It is not a Settings field — modernize_api is — but it is a layer like
# any other, so it is declared here and validated like any other rather
# than read raw out of the environment.
_MODERNIZE_URL = _Declaration(
    "modernize_url", _MODERNIZE_URL_ENV, "services.modernize", _as_str, None,
)
_BY_KEY[_MODERNIZE_URL.key] = _MODERNIZE_URL

CONFIG_FILENAME = "teille-douce.toml"


def find_config_file(start=None):
    """The nearest teille-douce.toml, walking up from *start*.

    Returns None when there is none. A file found this way and forgotten is
    the least debuggable thing in the design, which is why the origin
    records its path and `info` names it.
    """
    directory = Path(start or Path.cwd()).resolve()
    for candidate in (directory, *directory.parents):
        found = candidate / CONFIG_FILENAME
        if found.is_file():
            return found
    return None


@dataclass(frozen=True, slots=True)
class Rejection:
    """A value a layer offered and the converter refused."""
    name: str
    layer: str
    raw: str
    reason: str


@dataclass(frozen=True, slots=True)
class Settings:
    """Every setting of one run, with where each value came from."""

    ocr_dir: Path
    output_dir: Path
    entities_dir: Path
    metadata_csv: Path
    persons_csv: Path
    enrich: bool
    modernize: bool
    ner: bool
    pyhellen_url: str
    pyhellen_timeout: float
    pyhellen_concurrency: int
    pyhellen_max_consecutive_failures: int
    modernize_api: dict
    modernize_timeout: float
    modernize_batch_size: int
    modernize_concurrency: int
    modernize_similarity_min: float
    health_timeout: float
    ner_confidence_threshold: float
    max_workers: int
    debug: bool
    log_file: Path | None
    log_level: str
    skip_existing: bool
    tei_rng: Path | None
    # Plain dicts, not MappingProxyType: this object is an initarg of the
    # multiprocessing pool, and a mappingproxy cannot be pickled. The
    # dataclass being frozen is what protects the settings; the mappings
    # inside it are a detail nobody reaches for.
    origins: dict = field(default_factory=dict)
    rejected: tuple = ()

    # -- introspection ----------------------------------------------------

    def origin(self, name):
        """Where *name* got its value: "default", "config:<path>",
        "env:<VARIABLE>" or "flag"."""
        return self.origins.get(name, "default")

    @staticmethod
    def environment_variables():
        """Every TDOUCE_* name a setting answers to.

        The documented ones are a contract: the prefix is kept so that
        existing wrapper scripts and CI configurations keep working.
        """
        names = {d.env for d in _SETTINGS if d.env}
        names.add(_MODERNIZE_URL_ENV)
        return frozenset(names)

    # -- construction -----------------------------------------------------

    @classmethod
    def load(cls, *, flags=None, env=None, config_file=None):
        """Resolve the four layers into one frozen object.

        Args:
            flags (dict): parsed command-line values; a key whose value is
                None counts as not given and falls through.
            env (dict): the environment to read; defaults to os.environ.
            config_file (Path): a TOML file, or None.

        Returns:
            Settings: the resolved settings.

        Raises:
            ValueError: if the config file holds a key no setting answers
                to. Ignoring it is how a user spends an afternoon.
        """
        env = os.environ if env is None else env
        flags = flags or {}
        from_file = _read_config_file(config_file)

        values, origins, rejected = {}, {}, []
        for declaration in _SETTINGS:
            value, origin = _resolve(
                declaration, flags, env, from_file, config_file, rejected
            )
            values[declaration.name] = value
            if origin != "default":
                origins[declaration.name] = origin

        values["modernize_api"] = _resolve_modernize_api(
            flags, env, from_file, config_file, rejected
        )
        return cls(**values, origins=origins, rejected=tuple(rejected))


def _read_config_file(path):
    """Flatten a TOML file into {"section.key": value}."""
    if path is None:
        return {}
    with open(path, "rb") as handle:
        document = tomllib.load(handle)

    flat = {}
    for section, body in document.items():
        if not isinstance(body, dict):
            raise ValueError(
                f"{path}: '{section}' must be a section, not a bare value"
            )
        for key, value in body.items():
            name = f"{section}.{key}"
            if name not in _BY_KEY:
                near = _nearest_key(name)
                hint = f" — did you mean '{near}'?" if near else ""
                raise ValueError(f"{path}: unknown setting '{name}'{hint}")
            # A relative path in the file means "next to the file". The
            # file is found by walking up, so anchoring on the working
            # directory would make it mean something different from every
            # subdirectory it was meant to serve.
            if _BY_KEY[name].convert is _as_path and isinstance(value, str):
                candidate = Path(value)
                if not candidate.is_absolute():
                    value = str(path.parent / candidate)
            flat[name] = value
    return flat


def _nearest_key(name):
    """The declared key closest to *name*, for the error message."""
    import difflib
    matches = difflib.get_close_matches(name, _BY_KEY, n=1, cutoff=0.6)
    return matches[0] if matches else None


def _resolve(declaration, flags, env, from_file, config_path, rejected):
    """Walk the layers for one setting, highest first."""
    layers = [("flag", None, flags.get(declaration.name))]
    if declaration.env:
        layers.append((f"env:{declaration.env}", declaration.env,
                       env.get(declaration.env)))
    if declaration.key and declaration.key in from_file:
        layers.append((f"config:{config_path}", declaration.key,
                       from_file[declaration.key]))

    for origin, label, raw in layers:
        if raw is None:
            continue
        # A value typed on the command line becomes a usage error upstream,
        # so promising to keep the default here would be a lie in transit.
        announce = origin != "flag"
        if isinstance(raw, str) and not raw.strip():
            if announce:
                warnings.warn(
                    f"{label} is set but empty — keeping the default "
                    f"{declaration.default!r}",
                    RuntimeWarning, stacklevel=4,
                )
            rejected.append(
                Rejection(label or declaration.name, origin, raw, "is empty")
            )
            continue
        try:
            return declaration.convert(
                raw.strip() if isinstance(raw, str) else raw
            ), origin
        except (ValueError, TypeError) as reason:
            name = label or declaration.name
            if announce:
                warnings.warn(
                    f"{name}={raw!r} {reason} — keeping the default "
                    f"{declaration.default!r}",
                    RuntimeWarning, stacklevel=4,
                )
            rejected.append(Rejection(name, origin, str(raw), str(reason)))

    default = declaration.default
    if default is None:
        return None, "default"
    return declaration.convert(default), "default"


def _resolve_modernize_api(flags, env, from_file, config_path, rejected):
    """Base URLs per language.

    TDOUCE_MODERNIZE_URL_<IDENT> wins for one language;
    TDOUCE_MODERNIZE_URL moves every language that has no override of its
    own. Only a language config.py already declares can be overridden — a
    variable naming an unknown ident is read for nobody and does nothing.
    """
    shared, shared_origin = _resolve(_MODERNIZE_URL, flags, env, from_file,
                                     config_path, rejected)
    flag_given = shared is not None and shared_origin == "flag"
    resolved = {}
    for ident, default in config.DEFAULT_MODERNIZE_API.items():
        # Through _resolve like everything else: read raw, a stray space
        # became part of the URL and the service was reported unreachable
        # with nothing naming the variable.
        per_language = _Declaration(
            f"modernize_url_{ident}", f"{_MODERNIZE_URL_ENV}_{ident.upper()}",
            None, _as_str, None,
        )
        specific, _ = _resolve(per_language, flags, env, from_file,
                               config_path, rejected)
        # The flag lands in `shared` and the per-language variable in
        # `specific`, so preferring `specific` inverted the chain: a box
        # exporting TDOUCE_MODERNIZE_URL_FRA could not be redirected from
        # the command line, and nothing said the flag had been ignored.
        if flag_given:
            resolved[ident] = shared
        else:
            resolved[ident] = specific or shared or default
    return resolved


# =============================================================================
# Process-wide access
#
# Read at call time, never bound at import time: that is what lets a flag
# parsed after the imports still take effect.
# =============================================================================

_ACTIVE = None


def get_settings():
    """The settings in force.

    If none were installed, build them from the environment alone — so
    importing a module in a REPL, in a notebook, or in a test that never
    touches the CLI behaves exactly as it did before this object existed.
    """
    global _ACTIVE
    if _ACTIVE is None:
        _ACTIVE = Settings.load()
    return _ACTIVE


def set_settings(settings):
    """Install the settings of this run. Called once, by the CLI."""
    global _ACTIVE
    _ACTIVE = settings
    return settings


@contextlib.contextmanager
def use_settings(settings=None, **overrides):
    """Install settings for the duration of a block, then restore.

    Replaces the `importlib.reload(config)` dance the tests needed while
    config.py had import-time side effects.
    """
    global _ACTIVE
    previous = _ACTIVE
    try:
        if overrides:
            settings = replace(settings or get_settings(), **overrides)
        _ACTIVE = settings
        yield _ACTIVE
    finally:
        _ACTIVE = previous


__all__ = [
    "Rejection", "Settings", "get_settings", "set_settings", "use_settings",
]
