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

from lxml import etree

from teille_douce.paths import CHECKOUT, ROOT

ODD_RNG = ROOT / "schema" / "teille-douce.rng"
ODD_SVRL = ROOT / "schema" / "teille-douce.svrl.xsl"


def _absent(path):
    """Why a schema is not there, in the terms of the install at hand.

    `teille-douce odd build` is the answer in a checkout, and only
    there: the wheel carries `teille_douce/` alone, so an installed
    distribution was told to run a command that compiles an ODD it does
    not have either. It cannot build them; it needs them shipped.
    """
    if CHECKOUT is None:
        return (f"{path.name} is not part of the installed distribution: "
                f"the schemas live beside the sources, in schema/. "
                f"Install from a checkout (pip install -e .) to validate "
                f"against the project schema, or pass --no-odd.")
    return f"{path.name} is missing: teille-douce odd build"


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
    except Exception as reason:
        # Not `ImportError` alone: a SaxonC wheel whose native
        # `libsaxonc` will not load raises `OSError`, and an install
        # that is broken rather than absent is still a schema this
        # process cannot apply — which is what `Missing` means.
        raise Missing(f"saxonche cannot be used ({type(reason).__name__}: "
                      f"{reason}): pip install -r requirements-dev.txt")
    if not ODD_SVRL.exists():
        raise Missing(_absent(ODD_SVRL))
    processor = PySaxonProcessor(license=False)
    # The processor is returned with the sheet and kept alive
    # deliberately: the compiled sheet depends on it, and nothing
    # guarantees that link from one SaxonC version to the next.
    return processor, processor.new_xslt30_processor().compile_stylesheet(
        stylesheet_file=str(ODD_SVRL))


def project_relaxng():
    """The project content model, compiled."""
    if not ODD_RNG.exists():
        raise Missing(_absent(ODD_RNG))
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
        raise Missing(_absent(ODD_RNG))
    try:
        import saxonche  # noqa: F401
    except Exception as reason:
        # Same widening as above, and for the sharper reason: this runs
        # in the PARENT, before any worker exists, so an `OSError` here
        # came out as a traceback and exit 1 — "some files failed
        # validation" — from `teille-douce validate` with no flags at
        # all. One broken install must not be reported as a corpus that
        # does not conform.
        return False, (f"note: Schematron not applied (saxonche cannot be "
                       f"used: {type(reason).__name__}) — only "
                       f"teille-douce.rng was used")
    if not ODD_SVRL.exists():
        return False, (f"note: Schematron not applied ({_absent(ODD_SVRL)}) "
                       f"— only teille-douce.rng was used")
    return True, None
