"""Allow `python -m teille_douce`, equivalent to the `teille-douce` command."""

from teille_douce.cli import main

if __name__ == "__main__":
    raise SystemExit(main())
