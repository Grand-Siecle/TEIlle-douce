"""Checking produced TEI against what this pipeline promises it is.

It was `scripts/validate_tei.py`, whose logic was sound and whose
envelope was not: the help and every message were in French, in a
repository whose CONTRIBUTING.md says CLI help is in English, and its
three defaults were each the opposite of the one everybody used.
"""

from .checks import PARSER, svrl_violations, validate
from .schemas import (Missing, ODD_RNG, ODD_SVRL, available, project_relaxng,
                      project_schematron)

__all__ = ["PARSER", "svrl_violations", "validate", "Missing", "ODD_RNG",
           "ODD_SVRL", "available", "project_relaxng", "project_schematron"]
