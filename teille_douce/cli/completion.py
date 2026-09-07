"""`teille-douce completion bash|zsh|fish` — generated, never written.

A list of flags copied into a shell script diverges at the first PR that
adds one, and a completion that offers a flag the program does not have
is worse than none: it is a promise the parser then refuses. So the
surface is read off `build_parser()` at the moment the script is
generated, subcommand by subcommand, including the `choices` of the
options that have them — `--block <TAB>` offers the three blocks
because `record.py` declares three, not because this file lists them.

    teille-douce completion bash > ~/.local/share/bash-completion/completions/teille-douce
    teille-douce completion zsh  > ~/.zfunc/_teille-douce
    teille-douce completion fish > ~/.config/fish/completions/teille-douce.fish

Generate it once, into a file, and let the shell source that: building
the parser imports every subcommand module, and `validate` imports lxml,
so this is not something to put in a `.bashrc` as a command substitution
that runs at every login.
"""

from dataclasses import dataclass, field

SHELLS = ("bash", "zsh", "fish")


@dataclass(frozen=True, slots=True)
class Option:
    """One flag, as the shell needs to know it."""

    flags: tuple
    help: str = ""
    choices: tuple = ()
    takes_a_value: bool = False


@dataclass(frozen=True, slots=True)
class Command:
    """One subcommand and its surface."""

    name: str
    help: str = ""
    options: tuple = ()
    positional_choices: tuple = ()
    takes_paths: bool = False


@dataclass(frozen=True, slots=True)
class Surface:
    """Everything the shell is told, read off the parser."""

    globals: tuple = ()
    commands: tuple = field(default_factory=tuple)


# The subcommands whose positional argument is a path. argparse cannot
# say so — `nargs="*"` with `type=Path` and `nargs="?"` with a `choices`
# look the same from outside — and offering directory names where an
# action is expected is the kind of wrong help that trains people to
# press TAB less.
_TAKE_PATHS = {"validate", "run"}


def _clean(text):
    """One line of help, safe to put inside a shell string."""
    said = " ".join((text or "").split())
    return said.replace("'", "").replace('"', "").replace("`", "")


def surface(parser=None):
    """The parser, as the shells need it. Read, never listed."""
    if parser is None:
        from teille_douce.cli.app import build_parser

        parser = build_parser()

    subcommands, = [action for action in parser._actions
                    if getattr(action, "choices", None)
                    and not action.option_strings]
    commands = []
    for name, subparser in subcommands.choices.items():
        options, positional = [], ()
        for action in subparser._actions:
            if action.dest == "help":
                continue
            if not action.option_strings:
                if getattr(action, "choices", None):
                    positional = tuple(str(value) for value in action.choices)
                continue
            options.append(Option(
                flags=tuple(action.option_strings),
                help=_clean(action.help),
                choices=tuple(str(value) for value in (action.choices or ())),
                # `nargs == 0` is the whole answer, and the only one
                # that does not go stale: argparse sets it on every
                # action that consumes nothing — store_true, store_const,
                # count, version — and on any custom action that says so,
                # as `_PhaseAction` does for `--fast` and not for
                # `--phases`. A list of class names beside it looked like
                # a safety net and matched nothing this one had missed.
                takes_a_value=action.nargs != 0))
        commands.append(Command(
            name=name,
            help=_clean(subcommands._choices_actions and next(
                (choice.help for choice in subcommands._choices_actions
                 if choice.dest == name), "") or ""),
            options=tuple(options),
            positional_choices=positional,
            takes_paths=name in _TAKE_PATHS))

    globals_ = tuple(Option(flags=tuple(action.option_strings),
                            help=_clean(action.help))
                     for action in parser._actions
                     if action.option_strings and action.dest != "help")
    return Surface(globals=globals_, commands=tuple(commands))


def _all_flags(command):
    return " ".join(flag for option in command.options for flag in option.flags)


def _value_cases(command, indent):
    """`--block <TAB>` offers the three blocks, because argparse knows."""
    lines = []
    for option in command.options:
        if not option.choices:
            continue
        pattern = "|".join(option.flags)
        lines.append(f'{indent}{pattern})')
        lines.append(f'{indent}    COMPREPLY=( $(compgen -W '
                     f'"{" ".join(option.choices)}" -- "$cur") ); return ;;')
    return lines


def bash(surface_=None):
    """A bash completion function, and the `complete` that installs it."""
    known = surface_ or surface()
    names = " ".join(command.name for command in known.commands)
    globals_ = " ".join(flag for option in known.globals
                        for flag in option.flags)

    out = [
        "# teille-douce completion for bash, generated by",
        "#   teille-douce completion bash",
        "# Regenerate it after upgrading: the flags come from the parser.",
        "_teille_douce() {",
        "    local cur prev command word",
        "    COMPREPLY=()",
        '    cur="${COMP_WORDS[COMP_CWORD]}"',
        '    prev="${COMP_WORDS[COMP_CWORD-1]}"',
        "    command=''",
        '    for word in "${COMP_WORDS[@]:1:COMP_CWORD-1}"; do',
        '        case "$word" in',
        "            -*) ;;",
        '            *) command="$word"; break ;;',
        "        esac",
        "    done",
        '    case "$command" in',
    ]
    for command in known.commands:
        out.append(f"        {command.name})")
        # The value of the flag before the cursor, when it has a closed
        # set of them.
        cases = _value_cases(command, " " * 12)
        if cases:
            out.append('            case "$prev" in')
            out.extend("    " + line for line in cases)
            out.append("            esac")
        offered = _all_flags(command)
        if command.positional_choices:
            offered += " " + " ".join(command.positional_choices)
        out.append(f'            COMPREPLY=( $(compgen -W "{offered}" '
                   f'-- "$cur") )')
        if command.takes_paths:
            out.append('            if [[ "$cur" != -* ]]; then')
            out.append('                COMPREPLY+=( $(compgen -f -- "$cur") )')
            out.append("            fi")
        out.append("            ;;")
    out.extend([
        "        *)",
        f'            COMPREPLY=( $(compgen -W "{names} {globals_}" '
        f'-- "$cur") )',
        "            ;;",
        "    esac",
        "    return 0",
        "}",
        "complete -F _teille_douce teille-douce",
    ])
    return "\n".join(out) + "\n"


def zsh(surface_=None):
    """A `#compdef` function: the descriptions come from the help."""
    known = surface_ or surface()
    out = [
        "#compdef teille-douce",
        "# generated by: teille-douce completion zsh",
        "_teille_douce() {",
        "    local -a commands",
        "    commands=(",
    ]
    for command in known.commands:
        out.append(f"        '{command.name}:{command.help}'")
    out.extend([
        "    )",
        "    local curcontext=\"$curcontext\" state",
        "    _arguments -C '1: :->command' '*:: :->argument'",
        "    case $state in",
        "        command) _describe 'command' commands ;;",
        "        argument)",
        "            case $words[1] in",
    ])
    for command in known.commands:
        out.append(f"                {command.name})")
        out.append("                    _arguments \\")
        for option in command.options:
            for flag in option.flags:
                value = ": :" if option.takes_a_value else ""
                if option.choices:
                    value = f": :({' '.join(option.choices)})"
                out.append(f"                        '{flag}[{option.help}]"
                           f"{value}' \\")
        tail = ("'*: :_files'" if command.takes_paths
                else f"'*: :({' '.join(command.positional_choices)})'"
                if command.positional_choices else "&& return 0")
        out.append(f"                        {tail}")
        out.append("                    ;;")
    out.extend([
        "            esac",
        "            ;;",
        "    esac",
        "}",
        "_teille_douce \"$@\"",
    ])
    return "\n".join(out) + "\n"


def fish(surface_=None):
    """One `complete` line per flag, conditioned on the subcommand."""
    known = surface_ or surface()
    out = ["# generated by: teille-douce completion fish",
           "complete -c teille-douce -f"]
    for command in known.commands:
        out.append(f"complete -c teille-douce -n '__fish_use_subcommand' "
                   f"-a {command.name} -d '{command.help}'")
    for command in known.commands:
        seen = f"__fish_seen_subcommand_from {command.name}"
        for option in command.options:
            pieces = [f"complete -c teille-douce -n '{seen}'"]
            for flag in option.flags:
                if flag.startswith("--"):
                    pieces.append(f"-l {flag[2:]}")
                else:
                    pieces.append(f"-s {flag[1:]}")
            if option.choices:
                pieces.append(f"-x -a '{' '.join(option.choices)}'")
            elif option.takes_a_value:
                pieces.append("-r")
            pieces.append(f"-d '{option.help}'")
            out.append(" ".join(pieces))
        if command.positional_choices:
            out.append(f"complete -c teille-douce -n '{seen}' "
                       f"-a '{' '.join(command.positional_choices)}'")
        if command.takes_paths:
            out.append(f"complete -c teille-douce -n '{seen}' -F")
    return "\n".join(out) + "\n"


WRITERS = {"bash": bash, "zsh": zsh, "fish": fish}


def add_arguments(parser):
    """The surface of `teille-douce completion`."""
    parser.add_argument("shell", choices=SHELLS,
                        help="the shell to generate a completion for")
    return parser


def execute(args):
    print(WRITERS[args.shell](), end="")
    return 0
