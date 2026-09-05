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
import re
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
    try:
        # A shell expands ~ before the variable is ever read, but a config
        # file and a quoted flag do not: without this, "~/tei" is a
        # directory literally named "~" in the working directory.
        return Path(raw).expanduser()
    except RuntimeError:
        raise ValueError("names a home directory that cannot be resolved")


_DEVICE = re.compile(r"^(auto|cpu|mps|cuda(:\d+)?)$")


def _as_device(raw):
    """Where the NER models run.

    Validated here rather than left to torch: a typo in `--device cdua`
    would otherwise surface as a RuntimeError several gigabytes of model
    download later, at the first document of a long run.
    """
    if not isinstance(raw, str):
        raise ValueError("is not a device name")
    value = raw.strip().lower()
    if not _DEVICE.match(value):
        raise ValueError("is not one of auto, cpu, mps, cuda, cuda:<n>")
    return value


_UI = ("auto", "plain", "dashboard")


def _as_ui(raw):
    """Which reporter to use, as a value the layers can refuse.

    It used to be read straight out of `os.environ`, so it was the one
    TDOUCE_* variable with no flag layer, no config layer, no origin, and
    no place in the manifest — and an empty one, which is a normal CI
    idiom, aborted the run with a usage error after the metadata had
    loaded.
    """
    if not isinstance(raw, str):
        raise ValueError("is not a reporter name")
    value = raw.strip().lower()
    if value not in _UI:
        raise ValueError(f"is not one of {', '.join(_UI)}")
    return value


_FAIL_ON = ("never", "incident", "loss")


def _as_fail_on(raw):
    """What counts as a failure at the end of a run.

    A closed set, and validated here rather than left to a comparison
    somewhere downstream: `--fail-on incidents` silently meaning "never"
    is how a CI gate stops guarding without anyone noticing.
    """
    if not isinstance(raw, str):
        raise ValueError("is not a level name")
    value = raw.strip().lower()
    if value not in _FAIL_ON:
        raise ValueError(f"is not one of {', '.join(_FAIL_ON)}")
    return value


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
    # Both answer to one config key on purpose, matching --concurrency,
    # which sets both services at once. The consequence is deliberate: from
    # a config file the two cannot be set apart, only through their own
    # TDOUCE_* variables.
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
    # Where the NER models run. Automatic is right almost always; the
    # exceptions are a shared GPU somebody else is filling and a machine
    # with more than one, neither of which the pipeline can guess.
    # The quality gate. Default "never": a degraded conversion is still a
    # conversion, and on seventeenth-century OCR block 1 is never empty.
    _Declaration("ui", "TDOUCE_UI", "output.ui", _as_ui, "auto"),
    _Declaration("fail_on", "TDOUCE_FAIL_ON",
                 "quality.fail_on", _as_fail_on, "never"),
    _Declaration("max_page_loss", "TDOUCE_MAX_PAGE_LOSS",
                 "quality.max_page_loss",
                 _as_number(minimum=0.0, maximum=100.0), 100.0),
    _Declaration("ner_device", "TDOUCE_NER_DEVICE",
                 "models.device", _as_device, "auto"),
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

# Two settings may share a config key on purpose — limits.concurrency
# feeds both services, matching --concurrency. Sharing is declared here so
# that an accidental collision, the day two rows with different converters
# land on one key, is an error at import rather than a silent win for
# whichever came last.
SHARED_KEYS = frozenset({"limits.concurrency"})


def _index_by_key(declarations, shared=SHARED_KEYS):
    """Map config key to declaration, refusing an undeclared collision.

    First row wins for a shared key: the layer above only needs the
    converter and the anchoring rule, which the sharers agree on.
    """
    index = {}
    for declaration in declarations:
        if not declaration.key:
            continue
        first = index.get(declaration.key)
        if first is not None and declaration.key not in shared:
            raise RuntimeError(
                f"two settings declare the config key {declaration.key!r}: "
                f"{first.name} and {declaration.name}. Add it to SHARED_KEYS "
                f"if that is intended, and check that both convert alike."
            )
        if first is None:
            index[declaration.key] = declaration
    return index


_BY_KEY = _index_by_key(_SETTINGS)
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


def _plain(value):
    """A value json.dumps will accept, whatever the converter produced."""
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    return value


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
    ui: str
    fail_on: str
    max_page_loss: float
    ner_device: str
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

    def as_manifest(self):
        """Every setting, its value, and where the value came from.

        Written into run.json so that "why did it write there" is
        answerable three days later — by the origin, which is the part
        the value cannot tell you. Keyed on the config key rather than
        the attribute name, because that is what a reader would type to
        change it — so a setting with no config key (`TDOUCE_TEI_RNG`,
        which points the schema tests at a `tei_all.rng` and is not a
        property of the corpus) is not in here.

        Everything is coerced to a JSON-safe form here: a Path or an Enum
        surviving into the dump would only be discovered at the end of a
        four-hour run.
        """
        manifest = {}
        for declaration in _SETTINGS:
            if not declaration.key:
                continue
            entry = {
                "value": _plain(getattr(self, declaration.name)),
                "origin": self.origin(declaration.name),
            }
            standing = manifest.get(declaration.key)
            if standing is None:
                manifest[declaration.key] = entry
            elif standing != entry:
                # Two settings may share a config key on purpose, and one
                # `TDOUCE_*` variable can then move only one of them. The
                # loop simply overwrote, so `run.json` asserted a value
                # and an origin the other path never used, and the value
                # that WAS used went unrecorded. When they diverge each
                # gets its own line; when they agree the shared key
                # stands, which is the ordinary case and the one a reader
                # would type.
                manifest.pop(declaration.key, None)
                for other in _SETTINGS:
                    if other.key == declaration.key:
                        manifest[f"{other.key} ({other.name})"] = {
                            "value": _plain(getattr(self, other.name)),
                            "origin": self.origin(other.name),
                        }
        manifest[_MODERNIZE_URL.key] = {
            "value": _plain(self.modernize_api),
            "origin": self.origin("modernize_url"),
        }
        return manifest

    @staticmethod
    def config_keys():
        """Every "section.key" a config file may set.

        Documented the same way and for the same reason as the environment
        names: a key the file does not know is refused, so a reader who
        cannot find the list has no way to guess it -- TDOUCE_OCR_DIR is
        paths.input, not paths.ocr_dir.
        """
        return frozenset(_BY_KEY)

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

        # Which file was read, if any. A teille-douce.toml found by
        # walking up and forgotten is the least debuggable thing in this
        # design, so the run can name it.
        if config_file is not None:
            origins["__config__"] = str(config_file)

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
            if _BY_KEY[name].convert is _as_path and isinstance(value, str):
                value = _anchor(value, path.parent)
            flat[name] = value
    return flat


def _anchor(raw, base):
    """Read a config file's path value as written next to that file.

    The file is found by walking up, so anchoring on the working directory
    would make one line mean something different in every subdirectory it
    was meant to serve.

    Anchoring runs ahead of the layer's own validation, so it has to refuse
    what that validation would have refused. `Path("")` is `Path(".")`, and
    joining it hands back the config file's own directory: unguarded, an
    empty value turned into a real, non-empty path that the "is empty"
    check could no longer see, and `[paths] output = ""` -- the wrapper
    idiom this project already refuses everywhere else -- quietly meant
    "write the corpus beside the config file".
    """
    stripped = raw.strip()
    if not stripped:
        return raw
    try:
        candidate = Path(stripped).expanduser()
    except RuntimeError:
        return raw
    return str(candidate if candidate.is_absolute() else base / candidate)


def _nearest_key(name):
    """The declared key closest to *name*, for the error message."""
    import difflib
    matches = difflib.get_close_matches(name, _BY_KEY, n=1, cutoff=0.6)
    return matches[0] if matches else None


def _resolve(declaration, flags, env, from_file, config_path, rejected):
    """Walk the layers for one setting, highest first.

    Refusals are collected as the walk goes and only announced once the
    value that actually took effect is known: the message used to promise
    "keeping the default" even when a lower layer supplied something else,
    which misdirects the reader it exists to inform.
    """
    layers = [("flag", None, flags.get(declaration.name))]
    if declaration.env:
        layers.append((f"env:{declaration.env}", declaration.env,
                       env.get(declaration.env)))
    if declaration.key and declaration.key in from_file:
        layers.append((f"config:{config_path}", declaration.key,
                       from_file[declaration.key]))

    refused, value, origin = [], None, "default"
    for layer, label, raw in layers:
        if raw is None:
            continue
        name = label or declaration.name
        if isinstance(raw, str) and not raw.strip():
            refused.append((layer, name, raw, "is empty"))
            continue
        try:
            value = declaration.convert(
                raw.strip() if isinstance(raw, str) else raw
            )
            origin = layer
            break
        except (ValueError, TypeError) as reason:
            refused.append((layer, name, raw, str(reason)))

    if origin == "default":
        value = None if declaration.default is None else \
            declaration.convert(declaration.default)

    for layer, name, raw, reason in refused:
        rejected.append(Rejection(name, layer, str(raw), reason))
        # A value typed on the command line becomes a usage error upstream,
        # so announcing what was kept instead would be a lie in transit.
        if layer == "flag":
            continue
        warnings.warn(
            f"{name}={raw!r} {reason} — using {value!r} "
            f"({'the default' if origin == 'default' else origin})",
            RuntimeWarning, stacklevel=4,
        )

    return value, origin


def _resolve_modernize_api(flags, env, from_file, config_path, rejected):
    """Base URLs per language.

    TDOUCE_MODERNIZE_URL_<IDENT> wins for one language;
    TDOUCE_MODERNIZE_URL moves every language that has no override of its
    own. Only a language config.py already declares can be overridden — a
    variable naming an unknown ident is read for nobody and does nothing.
    """
    # The declared default is None, so a refusal here used to announce
    # "using None (the default)" while the run went on to pick a
    # per-language URL or the configured one. The fallback is stated so the
    # message names something the reader can act on.
    # A shared URL that no language will read cannot be worth a warning:
    # when every ident has its own TDOUCE_MODERNIZE_URL_<IDENT>, the shared
    # one affects nothing, and announcing a fallback for it named a value
    # the run does not use.
    overridden = {
        ident for ident in config.DEFAULT_MODERNIZE_API
        if env.get(f"{_MODERNIZE_URL_ENV}_{ident.upper()}")
    }
    shared_is_read = overridden != set(config.DEFAULT_MODERNIZE_API)

    # The declared default is None, so a refusal used to announce "using
    # None (the default)" while the run went on to pick the configured
    # URL. The fallback is stated so the message names something the
    # reader can act on.
    fallback = next(iter(config.DEFAULT_MODERNIZE_API.values()), None)
    # Suppressing the *warning* for a shared URL no language reads is
    # right; suppressing the *usage error* is not. A flag the user typed
    # and the converter refused stays a usage error whatever the other
    # layers hold.
    scratch = rejected if shared_is_read else []
    with warnings.catch_warnings():
        if not shared_is_read:
            warnings.simplefilter("ignore", RuntimeWarning)
        shared, shared_origin = _resolve(
            _Declaration(_MODERNIZE_URL.name, _MODERNIZE_URL.env,
                         _MODERNIZE_URL.key, _MODERNIZE_URL.convert, fallback),
            flags, env, from_file, config_path, scratch,
        )
    if scratch is not rejected:
        rejected.extend(r for r in scratch if r.layer == "flag")
    if shared == fallback and shared_origin == "default":
        shared = None
    flag_given = shared is not None and shared_origin == "flag"
    resolved = {}
    for ident, default in config.DEFAULT_MODERNIZE_API.items():
        # Through _resolve like everything else: read raw, a stray space
        # became part of the URL and the service was reported unreachable
        # with nothing naming the variable.
        if flag_given:
            # --vieuxparler has already decided. Walking the per-language
            # layer anyway warned about a variable that had no effect on
            # the run, in the one situation where the operator had
            # deliberately overridden it.
            resolved[ident] = shared
            continue

        # The fallback is what the run would use without this variable, so
        # a refusal announces the URL that actually takes effect rather
        # than None.
        per_language = _Declaration(
            f"modernize_url_{ident}", f"{_MODERNIZE_URL_ENV}_{ident.upper()}",
            None, _as_str, shared or default,
        )
        specific, _ = _resolve(per_language, flags, env, from_file,
                               config_path, rejected)
        # The flag lands in `shared` and the per-language variable in
        # `specific`, so preferring `specific` inverted the chain: a box
        # exporting TDOUCE_MODERNIZE_URL_FRA could not be redirected from
        # the command line, and nothing said the flag had been ignored.
        resolved[ident] = specific or shared or default
    return resolved


# =============================================================================
# Process-wide access
#
# Read at call time, never bound at import time: that is what lets a flag
# parsed after the imports still take effect.
# =============================================================================

_ACTIVE = None

# Distinguishes `use_settings()` — keep what is in force — from
# `use_settings(None)`, which deliberately installs nothing.
_KEEP = object()


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
def use_settings(settings=_KEEP, **overrides):
    """Install settings for the duration of a block, then restore.

    Replaces the `importlib.reload(config)` dance the tests needed while
    config.py had import-time side effects.
    """
    global _ACTIVE
    previous = _ACTIVE
    try:
        if overrides:
            base = get_settings() if settings is _KEEP else (settings or get_settings())
            settings = replace(base, **overrides)
        elif settings is _KEEP:
            # Bare `use_settings()` reads as "scope this block", so it keeps
            # what is in force. `use_settings(None)` is the deliberate
            # spelling for "install nothing", which the lazy-build test
            # needs, and the two must not be the same call.
            settings = get_settings()
        _ACTIVE = settings
        yield _ACTIVE
    finally:
        _ACTIVE = previous


__all__ = [
    "Rejection", "Settings", "get_settings", "set_settings", "use_settings",
]
