"""`teille-douce info` — which value is in force, and which layer set it.

There are four configuration layers, per setting: a flag, a `TDOUCE_*`
variable, a `teille-douce.toml` key, and the default in `config.py`.
Without a way to ask the chain where a value came from, that is a trap
rather than a feature — and the config file is the sharpest edge of it,
because it is found by walking up from the working directory, so one a
reader has forgotten is the least debuggable thing in the design. This
command names it whether or not anything in it was used.

It reports refusals too. A `TDOUCE_JOBS=eight` is refused by its
converter and the layer below takes effect, which the run says once, in
a warning, four hours before anyone reads the log. Here it is a line
against the layer that offered it.

Rendering is a pure function of the resolved report, as everywhere else
in this instalment: no terminal, no corpus, no environment.
"""

from dataclasses import dataclass

from teille_douce.report.text import cells, clip, pad, shorten_path

MAX_WIDTH = 100
_MARGIN = 2

# The order the layers are consulted in, highest first. It is the order
# they are printed in, because a reader looking for "why is my variable
# ignored" is looking for something ABOVE it.
LAYERS = ("flag", "env", "config", "default")


@dataclass(frozen=True, slots=True)
class Offer:
    """What one layer had to say about one setting.

    `value` is None when the layer offered nothing at all — which is not
    the same as offering something that was refused, and the two used to
    be told apart only by reading the run log.
    """

    layer: str
    where: str
    value: str | None
    used: bool = False
    refused: str | None = None


@dataclass(frozen=True, slots=True)
class Resolved:
    """One setting, its value, and the four layers behind it."""

    name: str
    value: str
    origin: str
    offers: tuple

    @property
    def inherited(self):
        return self.origin == "default"


@dataclass(frozen=True, slots=True)
class Report:
    """Everything `info` knows, measured once."""

    settings: tuple
    config_path: object = None
    config_used: bool = False
    versions: tuple = ()


def _shown(value):
    """A value as one line of text, whatever the converter produced."""
    from pathlib import Path

    if value is None:
        return "(none)"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, dict):
        return ", ".join(f"{key}={_shown(item)}"
                         for key, item in sorted(value.items())) or "(empty)"
    return str(value)


# The settings whose flag does not carry their name. `--concurrency`
# feeds two, `-v` and `-q` decide two more between them, and `--strict`
# is an alias — argparse's dest cannot say any of that, so these six are
# written here and everything else is read off the parser.
_INDIRECT = {
    "ui": "--dashboard/--plain",
    "debug": "-v/-vv",
    "log_level": "--log-level (floored by -v/-q)",
    "skip_existing": "--skip-existing/--force",
    "pyhellen_concurrency": "--concurrency",
    "modernize_concurrency": "--concurrency",
    "fail_on": "--fail-on/--strict",
}

_FLAGS = None


def _flag_for(name):
    """The flag that sets *name*, or None.

    Read off `run`'s parser rather than from a list of its own:
    `option_for` exists to name a flag in a usage message and invents
    `--<setting>` when there is none, which here would answer "which
    flag sets this" with one that does not exist. A setting with no flag
    says so.
    """
    global _FLAGS

    if _FLAGS is None:
        from teille_douce.cli.app import build_parser

        parser = build_parser()
        subcommands, = [action for action in parser._actions
                        if getattr(action, "choices", None)
                        and not action.option_strings]
        _FLAGS = {action.dest: "/".join(action.option_strings)
                  for action in subcommands.choices["run"]._actions
                  if action.option_strings}
    return _INDIRECT.get(name) or _FLAGS.get(name)


def resolve(settings, name, env, from_file, config_path):
    """The four layers of one setting, and which of them took effect."""
    from teille_douce.settings import declarations

    declaration = next(row for row in declarations() if row.name == name)
    origin = settings.origin(name)
    refusals = {rejection.layer: rejection
                for rejection in settings.rejected if rejection.name in
                (name, declaration.env, declaration.key)}

    flag = _flag_for(name)
    offers = [Offer("flag", flag or "(no flag)",
                    _value_of(settings, name) if origin == "flag" else None,
                    used=origin == "flag",
                    refused=_reason(refusals.get("flag")))]
    if declaration.env:
        raw = env.get(declaration.env)
        offers.append(Offer(
            "env", declaration.env, raw if raw is not None else None,
            used=origin == f"env:{declaration.env}",
            refused=_reason(refusals.get(f"env:{declaration.env}"))))
    if declaration.key:
        raw = from_file.get(declaration.key)
        offers.append(Offer(
            "config", declaration.key,
            _shown(raw) if raw is not None else None,
            used=origin.startswith("config:"),
            refused=_reason(refusals.get(f"config:{config_path}"))))
    offers.append(Offer("default", "config.py", _shown(declaration.default),
                        used=origin == "default"))

    return Resolved(name=name, value=_shown(_value_of(settings, name)),
                    origin=origin, offers=tuple(offers))


def _reason(rejection):
    return None if rejection is None else f"{rejection.raw!r} {rejection.reason}"


def _value_of(settings, name):
    """The value in force. `modernize_url` is a layer, not a field."""
    if name == "modernize_url":
        return settings.modernize_api
    return getattr(settings, name)


def gather(settings, env=None, config_path=None):
    """Every setting, resolved, plus what produced this installation."""
    import os

    import teille_douce
    from teille_douce.config import APP_VERSIONS
    from teille_douce.settings import config_values, declarations

    env = os.environ if env is None else env
    from_file = config_values(config_path) if config_path else {}
    resolved = tuple(resolve(settings, row.name, env, from_file, config_path)
                     for row in declarations())
    versions = (("teille-douce", teille_douce.__version__),) + tuple(
        (entry["label"], entry["version"]) for entry in APP_VERSIONS.values())
    return Report(settings=resolved, config_path=config_path,
                  config_used=any(item.origin.startswith("config:")
                                  for item in resolved),
                  versions=versions)


def render(report, width=92, name=None):
    """The report, as lines. One setting in full, or all of them."""
    room = min(width, MAX_WIDTH) - _MARGIN
    if name is not None:
        one, = [item for item in report.settings if item.name == name]
        return _one(one, report, room)
    return _all(report, room)


def _one(item, report, room):
    """One setting, and every layer that had something to say."""
    # `keep="right"`: the origin is the question asked, and the value is
    # said again below against the layer that supplied it, marked used.
    # Keeping the left half whole clipped the origin away entirely on any
    # setting holding an absolute path.
    lines = [pad(f"  {item.name} = {item.value}", f"({_origin(item)})", room,
                 keep="right"), ""]
    # Measured, not `{:<22}`: `pyhellen_max_consecutive_failures` and
    # `TDOUCE_MODERNIZE_SIMILARITY_MIN` are both longer than any literal
    # anyone would pick, and a column that stops running is the defect
    # the summary and the panel were each fixed for once already.
    column = min(max(cells(offer.where) for offer in item.offers) + 2,
                 max(20, room // 3))
    for offer in item.offers:
        gap = " " * max(2, column - cells(offer.where))
        said = offer.value if offer.value is not None else "(not given)"
        if offer.layer == "config" and offer.value is not None:
            said = f"{shorten_path(report.config_path, 28)}: {said}"
        left = f"  {offer.layer:<9}{offer.where}{gap}{said}"
        # The branch this used to be — `pad` when used, `clip` when not —
        # existed because `pad` padded to the full width whatever the
        # right half was, so every line but one ended in trailing
        # spaces. `pad` treats a half that carries nothing as no half at
        # all now, which the property test found and fixed at the root.
        lines.append(pad(left, "<- used" if offer.used else "", room,
                         keep="right"))
        if offer.refused:
            # Refused, so a lower layer took effect. The run says this
            # once in a warning, four hours before anyone reads the log.
            lines.append(clip(f"  {' ' * (9 + column)}refused: "
                              f"{offer.refused}", room))
    return lines


def _all(report, room):
    """Every setting, with the ones nobody set held apart from the rest."""
    lines = []
    if report.config_path:
        aside = "read" if report.config_used else "nothing used from it"
        # `shorten_path`, not `pad`'s own clipping: a config file is
        # identified by its directory AND its name, and clipping from the
        # right drops the `teille-douce.toml` that says what the line is
        # about.
        room_for_path = room - cells(aside) - 17
        lines.append(pad(f"  config file  "
                         f"{shorten_path(report.config_path, room_for_path)}",
                         aside, room, keep="right"))
    else:
        lines.append(clip("  config file  (none found)", room))
    lines.append("")

    column = min(max(cells(item.name) for item in report.settings) + 2,
                 max(20, room // 3))
    for item in report.settings:
        gap = " " * max(2, column - cells(item.name))
        # A bullet, not a colour: the origin column already answers the
        # question, and this is what makes the answer scannable on a
        # terminal that has no colours and in a file someone pastes.
        mark = "  " if item.inherited else "• "
        lines.append(pad(f"{mark}{item.name}{gap}{item.value}",
                         _origin(item), room, keep="right"))

    lines.append("")
    lines.append(clip("  versions written into <appInfo>", room))
    # Measured. `{label:<24}` pads to twenty-four and stops, and
    # "YALTAi - You Actually Look Twice At it" is thirty-seven, so the
    # label ran straight into its own version — the defect the summary,
    # the panel and `check` were each fixed for once already.
    column = min(max(cells(label) for label, _ in report.versions) + 2,
                 max(20, room // 2))
    for label, version in report.versions:
        gap = " " * max(2, column - cells(label))
        lines.append(clip(f"    {label}{gap}{version}", room))
    return lines


def _origin(item):
    """Where the value came from, as the reader would go and change it."""
    origin = item.origin
    if origin.startswith("env:"):
        return f"env {origin[4:]}"
    if origin.startswith("config:"):
        return f"config {shorten_path(origin[7:], 30)}"
    if origin == "flag":
        flag = _flag_for(item.name)
        return f"flag {flag}" if flag else "flag"
    return "default"


def _as_json(report, name=None):
    chosen = ([item for item in report.settings if item.name == name]
              if name is not None else list(report.settings))
    return {
        "config_file": str(report.config_path) if report.config_path else None,
        "settings": [
            {"name": item.name, "value": item.value, "origin": item.origin,
             "layers": [{"layer": offer.layer, "where": offer.where,
                         "value": offer.value, "used": offer.used,
                         "refused": offer.refused}
                        for offer in item.offers]}
            for item in chosen],
        "versions": {label: version for label, version in report.versions},
    }


def add_arguments(parser):
    """The surface of `teille-douce info`."""
    parser.add_argument(
        "setting", nargs="?", metavar="NAME",
        help="one setting, with every layer that had something to say "
             "[all of them]")
    parser.add_argument("--config", dest="config_file", metavar="PATH",
                        default=None,
                        help="read this config file instead of discovering one")
    parser.add_argument("--no-config", action="store_true", default=False,
                        help="skip config-file discovery entirely")
    parser.add_argument("--json", action="store_true",
                        help="the same report, as one JSON object")
    return parser


def _named(asked):
    """The setting *asked* names, whichever of its three names was typed.

    A reader who has just been shown `env TDOUCE_PYHELLEN_URL` will type
    that, and `config services.pyhellen` likewise; refusing either
    because the attribute is spelled differently would make the output
    of this command unusable as its own input.
    """
    import difflib

    from teille_douce.cli.exits import USAGE, refuse
    from teille_douce.settings import declarations

    rows = declarations()
    for row in rows:
        if asked in (row.name, row.env, row.key):
            return row.name
    known = [row.name for row in rows] + [row.env for row in rows if row.env]
    near = difflib.get_close_matches(asked, known, n=1, cutoff=0.6)
    hint = f" — did you mean {near[0]!r}?" if near else ""
    # stderr and 2, as argparse itself would: this is a value typed on
    # the command line. `SystemExit(2, message)` prints the whole tuple
    # and exits 1, which is this CLI's code for "some volumes failed" —
    # the trap `cli/exits.py` exists to close, in every command at once.
    refuse(f"unknown setting {asked!r}{hint}", USAGE, "teille-douce info")


def execute(args):
    import json
    import sys

    from teille_douce.cli.app import build_parser, config_file
    from teille_douce.cli.run import console
    from teille_douce.settings import get_settings

    name = _named(args.setting) if args.setting else None
    report = gather(get_settings(),
                    config_path=config_file(args, build_parser()))
    if args.json:
        json.dump(_as_json(report, name), sys.stdout, ensure_ascii=False,
                  indent=2)
        print()
        return 0
    for line in render(report, width=console.width, name=name):
        console.print(line, highlight=False)
    return 0
