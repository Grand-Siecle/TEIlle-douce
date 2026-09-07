"""Compile `schema/teille-douce.odd` into schemas that can be used.

The ODD is the source of truth: what the pipeline emits, with which
values and under which constraints. This derives the three versioned
artefacts beside it:

    schema/teille-douce.rng        content model, enforced by lxml
    schema/teille-douce.sch        Schematron constraints, readable form
    schema/teille-douce.svrl.xsl   the same, precompiled, executable form

The third exists because the TEI produces Schematron in
`queryBinding="xslt2"`, which `lxml.isoschematron` refuses (it implements
XPath 1.0 only). Precompiling the SVRL sheet here lets validation depend
on saxonche alone, without the full toolchain.

The toolchain is materialised in `.odd-toolchain/` (gitignored) at pinned
versions — a compilation chain that moved under our feet would produce
schema diffs with no ODD change:

    p5subset.xml       the compiled TEI P5, what @source resolves against
    Stylesheets/       the official odd2odd, odd2relax, extract-isosch XSLT
    schxslt/           the Schematron -> SVRL compiler

The XSLT engine is SaxonC-HE through the `saxonche` wheel: XSLT 2.0 with
no JVM and no ant, unlike the shell scripts shipped with the Stylesheets.
"""

import io
import re
import shutil
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from lxml import etree

from teille_douce.cli.exits import MISCONFIGURED, refuse
from teille_douce.paths import CHECKOUT, ROOT

from .simplify import simplify

SCHEMA = ROOT / "schema"
ODD = SCHEMA / "teille-douce.odd"
DEFAULT_TOOLCHAIN = ROOT / ".odd-toolchain"

# Pinned versions. Raising them is a deliberate change: recompile and read
# the diff of the schemas produced.
P5_VERSION = "4.12.0"
STYLESHEETS_TAG = "v7.61.0"
SCHXSLT_VERSION = "1.10.1"

P5_URL = f"https://www.tei-c.org/Vault/P5/{P5_VERSION}/xml/tei/odd/p5subset.xml"
STYLESHEETS_URL = (
    f"https://codeload.github.com/TEIC/Stylesheets/tar.gz/refs/tags/{STYLESHEETS_TAG}"
)
# GitHub throttles anonymous downloads in bursts. When it does, the same
# archive comes through `gh`'s authenticated API, if it is installed and
# logged in — otherwise there is nothing to do but try again later.
STYLESHEETS_API = f"repos/TEIC/Stylesheets/tarball/{STYLESHEETS_TAG}"
SCHXSLT_URL = (
    f"https://codeberg.org/SchXslt/schxslt/releases/download/"
    f"v{SCHXSLT_VERSION}/schxslt-{SCHXSLT_VERSION}-xslt-only.zip"
)

# The language of the error messages. `extract-isosch.xsl` STOPS if the
# constraints exist in several languages without one being chosen — and
# the ODD is bilingual, so this parameter is not optional.
MESSAGE_LANGUAGE = "en"


class Toolchain:
    """Where the pinned compilation chain lives, and how to get it."""

    def __init__(self, directory=DEFAULT_TOOLCHAIN):
        self.directory = Path(directory)
        self.p5 = self.directory / "p5subset.xml"
        self.stylesheets = self.directory / "Stylesheets"
        self.schxslt = self.directory / f"schxslt-{SCHXSLT_VERSION}"

    @property
    def required(self):
        return (self.p5,
                self.stylesheets / "odds" / "odd2odd.xsl",
                self.schxslt / "2.0" / "pipeline-for-svrl.xsl")

    def prepare(self, refresh=False, say=print):
        """Materialise p5subset.xml, the Stylesheets and SchXslt, pinned.

        Downloads only what is missing, unless *refresh*.
        """
        if refresh:
            self._invalidate()
        self.directory.mkdir(parents=True, exist_ok=True)

        if not self.p5.exists():
            say(f"TEI P5 {P5_VERSION}")
            say(f"  downloading {P5_URL}")
            with urllib.request.urlopen(P5_URL, timeout=300) as answer:
                self.p5.write_bytes(answer.read())

        if not (self.stylesheets / "odds" / "odd2odd.xsl").exists():
            say(f"TEI Stylesheets {STYLESHEETS_TAG}")
            if self.stylesheets.exists():
                shutil.rmtree(self.stylesheets)
            self._unpack_stylesheets(self._stylesheets_archive(say))

        if not (self.schxslt / "2.0" / "pipeline-for-svrl.xsl").exists():
            say(f"SchXslt {SCHXSLT_VERSION}")
            with urllib.request.urlopen(SCHXSLT_URL, timeout=300) as answer:
                zipfile.ZipFile(io.BytesIO(answer.read())).extractall(self.directory)

        missing = [path for path in self.required if not path.exists()]
        if missing:
            # 3, not 1: nothing was compiled, and `odd check` answers 1
            # for "the versioned schemas have drifted". A CI could not
            # tell a real drift from a download that got throttled.
            refuse("incomplete toolchain: "
                   + ", ".join(str(path) for path in missing),
                   MISCONFIGURED, "teille-douce odd")

    def _invalidate(self):
        """Remove the three things this class put there, and nothing else.

        `--refresh` used to `rmtree` the directory itself, which is fine
        for the default `.odd-toolchain/` and is data loss the moment
        `--toolchain-dir` names somewhere shared — nothing stops it
        being `~/tei`, or a directory that holds a p5subset.xml among
        other work. A cache invalidates its own entries.
        """
        for owned in (self.p5, self.stylesheets, self.schxslt):
            if owned.is_dir():
                shutil.rmtree(owned)
            elif owned.exists():
                owned.unlink()

    def _stylesheets_archive(self, say):
        """The Stylesheets archive, anonymously and then, if that is
        throttled, through gh's authenticated API."""
        try:
            say(f"  downloading {STYLESHEETS_URL}")
            with urllib.request.urlopen(STYLESHEETS_URL, timeout=600) as answer:
                return answer.read()
        except (urllib.error.URLError, TimeoutError) as refused:
            if not shutil.which("gh"):
                refuse(
                    f"the Stylesheets could not be downloaded ({refused}).\n"
                    "GitHub throttles anonymous downloads in bursts: try again\n"
                    "later, or install gh (https://cli.github.com) and log in.",
                    MISCONFIGURED, "teille-douce odd")
            say(f"  anonymous throttled ({refused}) — retrying through gh api")
            finished = subprocess.run(["gh", "api", STYLESHEETS_API],
                                      capture_output=True)
            if finished.returncode != 0:
                # The commonest case is a gh that is installed and not
                # logged in: its explanation is on stderr, and silencing
                # it would leave a call trace where the diagnosis goes.
                refuse(
                    "gh api failed for the Stylesheets:\n"
                    + (finished.stderr.decode("utf-8", "replace").strip()
                       or f"exit code {finished.returncode}")
                    + "\n(log in with `gh auth login`, or try again later)",
                    MISCONFIGURED, "teille-douce odd")
            return finished.stdout

    def _unpack_stylesheets(self, archive):
        """The GitHub archive wraps everything in a dated directory; it is
        unfolded under `<toolchain>/Stylesheets`."""
        with tempfile.TemporaryDirectory() as tmp:
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
                tar.extractall(tmp, filter="data")
            roots = list(Path(tmp).iterdir())
            if len(roots) != 1:
                refuse(f"unexpected Stylesheets archive: {roots}",
                       MISCONFIGURED, "teille-douce odd")
            shutil.move(str(roots[0]), str(self.stylesheets))


def _processor():
    try:
        from saxonche import PySaxonProcessor
    except Exception:
        # A wheel whose native runtime will not load raises OSError, not
        # ImportError, and `odd check` exits 1 for schema drift and
        # nothing else: a CI could not tell a broken install from a
        # derivative someone had edited by hand.
        refuse(
            "saxonche cannot be used: pip install -r requirements-dev.txt\n"
            "(SaxonC-HE, the XSLT 2.0 engine; no JVM required)",
            MISCONFIGURED, "teille-douce odd")
    return PySaxonProcessor


def _transform(processor, sheet, source, target, **parameters):
    xslt = processor.new_xslt30_processor()
    for name, value in parameters.items():
        # XSLT parameter names take dots; they are written with double
        # underscores on the Python side and restored here.
        xslt.set_parameter(
            name.replace("__", "."),
            processor.make_boolean_value(value) if isinstance(value, bool)
            else processor.make_string_value(value),
        )
    executable = xslt.compile_stylesheet(stylesheet_file=str(sheet))
    executable.transform_to_file(source_file=str(source), output_file=str(target))
    if not Path(target).exists():
        refuse(f"{sheet.name} produced nothing for {source}",
               MISCONFIGURED, "teille-douce odd")


def compile_odd(destination, toolchain=None, odd=ODD, say=print):
    """Produce the three artefacts in *destination*.

    Everything is compiled in a temporary directory and reaches
    *destination* only once all four steps have succeeded. Writing as it
    went left, when a step failed, a fresh .rng beside a .sch and a
    .svrl.xsl from the previous compilation — and, in the case of the
    impossible-pattern check, exactly the uncompilable schema that check
    exists to prevent.
    """
    toolchain = toolchain or Toolchain()
    PySaxonProcessor = _processor()
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        workshop = Path(tmp)
        rng = workshop / "teille-douce.rng"
        sch = workshop / "teille-douce.sch"
        svrl = workshop / "teille-douce.svrl.xsl"
        compiled = workshop / "teille-douce.compiled.odd"
        with PySaxonProcessor(license=False) as processor:
            # 1. ODD -> compiled ODD: the moduleRef are resolved against
            #    the P5, the elementSpec mode="change" merged into their
            #    original definition.
            say("odd2odd")
            _transform(processor, toolchain.stylesheets / "odds" / "odd2odd.xsl",
                       odd, compiled, defaultSource=str(toolchain.p5))

            # 2. -> RelaxNG
            say("odd2relax")
            _transform(processor, toolchain.stylesheets / "odds" / "odd2relax.xsl",
                       compiled, rng)

            # 2b. The TEI classes the pruning emptied come out of here as
            # <notAllowed/>. libvxml2 does not reduce them usefully — it
            # spent more than ten minutes on them without finishing — so
            # the rules of the specification are applied here (RELAX NG
            # 4.19 and 4.20).
            say("simplifying the notAllowed")
            grammar = etree.parse(str(rng))
            removed = simplify(grammar)
            grammar.write(str(rng), encoding="UTF-8", xml_declaration=True)
            left = len(list(grammar.iter(
                "{http://relaxng.org/ns/structure/1.0}notAllowed")))
            say(f"  {removed} patterns removed, {left} left")
            if left:
                refuse(
                    f"{left} <notAllowed/> survived the simplification:\n"
                    "libxml2 could not compile the schema. See "
                    "teille_douce/odd/simplify.py.",
                    MISCONFIGURED, "teille-douce odd")

            # 3. -> ISO Schematron
            say(f"extract-isosch (lang={MESSAGE_LANGUAGE})")
            _transform(processor,
                       toolchain.stylesheets / "odds" / "extract-isosch.xsl",
                       compiled, sch, lang=MESSAGE_LANGUAGE)

            # 4. -> executable SVRL sheet
            # SchXslt dates the sheet it produces; without this, two
            # compilations of one ODD would give two different files and
            # --check would cry drift every time.
            say("schxslt")
            _transform(processor,
                       toolchain.schxslt / "2.0" / "pipeline-for-svrl.xsl",
                       sch, svrl, schxslt__compile__metadata=False)

        produced = []
        for temporary in (rng, sch, svrl):
            final = destination / temporary.name
            shutil.copy2(temporary, final)
            produced.append(final)

    for artefact in produced:
        shown = (artefact.relative_to(ROOT)
                 if artefact.is_relative_to(ROOT) else artefact)
        say(f"  {shown} ({artefact.stat().st_size / 1024:.0f} kB)")
    return tuple(produced)


DATED = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def without_the_date(text):
    """The text without the generation date the Stylesheets write into it.

    They date the schema they produce, so two compilations of one ODD
    differ by a line. That is the only tolerated difference, as the
    generation date is in the golden end-to-end comparison.
    """
    return DATED.sub("DATE", text)


def verify(toolchain=None, odd=ODD, schema=SCHEMA, say=print):
    """Recompile alongside and compare: 1 if the versioned artefacts no
    longer match the ODD."""
    with tempfile.TemporaryDirectory() as tmp:
        produced = compile_odd(Path(tmp), toolchain=toolchain, odd=odd, say=say)
        drifted = []
        for artefact in produced:
            versioned = Path(schema) / artefact.name
            if not versioned.exists():
                drifted.append(f"{artefact.name}: missing from schema/")
            elif without_the_date(artefact.read_text(encoding="utf-8")) != \
                    without_the_date(versioned.read_text(encoding="utf-8")):
                drifted.append(f"{artefact.name}: no longer matches the ODD")
    if drifted:
        say("\nDRIFT:")
        for line in drifted:
            say(f"  {line}")
        say("\nRecompile: teille-douce odd build")
        return 1
    say("\nThe versioned schemas match the ODD.")
    return 0
