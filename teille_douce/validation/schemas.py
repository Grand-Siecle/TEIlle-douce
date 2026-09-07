"""The schemas a check can apply, and where they come from.

Three of them, and they cost very different amounts:

    schema/teille-douce.rng        the project content model, lxml reads it
    schema/teille-douce.svrl.xsl   the project constraints, Saxon runs it
    a tei_all.rng named by --schema the whole TEI, lxml reads it

The first two are versioned, which is the point: applying them needs
nothing but `lxml` and `saxonche`, not the hundred megabytes of TEI
Stylesheets that COMPILING them needs. That is why `teille-douce odd
build` is a maintainer's command and this is not.
"""

from pathlib import Path

from lxml import etree

ROOT = Path(__file__).resolve().parent.parent.parent
ODD_RNG = ROOT / "schema" / "teille-douce.rng"
ODD_SVRL = ROOT / "schema" / "teille-douce.svrl.xsl"


class Missing(Exception):
    """A schema, or the engine that runs it, is not here.

    An exception rather than a `SystemExit`: the caller decides whether
    an absent Schematron ends the check or only narrows it, and the old
    code raised `SystemExit` from a helper and then caught it two frames
    up to decide exactly that.
    """


def project_schematron():
    """The SVRL sheet compiled from the ODD, ready to apply.

    It is XSLT 2.0 — that is what the TEI produces — so lxml cannot run
    it: it needs saxonche, which is a development dependency and not a
    dependency of the pipeline.
    """
    try:
        from saxonche import PySaxonProcessor
    except ImportError:
        raise Missing("saxonche is not installed: "
                      "pip install -r requirements-dev.txt")
    if not ODD_SVRL.exists():
        raise Missing(f"{ODD_SVRL.name} is missing: teille-douce odd build")
    processor = PySaxonProcessor(license=False)
    # The processor is returned with the sheet and kept alive
    # deliberately: the compiled sheet depends on it, and nothing
    # guarantees that link from one SaxonC version to the next.
    return processor, processor.new_xslt30_processor().compile_stylesheet(
        stylesheet_file=str(ODD_SVRL))


def project_relaxng():
    """The project content model, compiled."""
    if not ODD_RNG.exists():
        raise Missing(f"{ODD_RNG.name} is missing: teille-douce odd build")
    return etree.RelaxNG(etree.parse(str(ODD_RNG)))


def available(with_odd):
    """What can be applied, and what cannot, without compiling anything.

    Asked in the parent so it can say so once; the workers compile. The
    parent used to build every schema itself and then have each worker
    build them again into the context the check actually reads — so the
    parent's copies were never read at all, and only their side effect,
    these two notes, mattered. A `tei_all.rng` is a megabyte and the SVRL
    sheet goes through Saxon; that is not free.

    Returns:
        tuple: (schematron is applicable, one note to print or None)
    """
    if not with_odd:
        return False, None
    if not ODD_RNG.exists():
        raise Missing(f"{ODD_RNG.name} is missing: teille-douce odd build")
    try:
        import saxonche  # noqa: F401
    except ImportError:
        return False, ("note: Schematron not applied (saxonche is not "
                       "installed) — only teille-douce.rng was used")
    if not ODD_SVRL.exists():
        return False, (f"note: Schematron not applied ({ODD_SVRL.name} is "
                       f"missing: teille-douce odd build) — only "
                       f"teille-douce.rng was used")
    return True, None
