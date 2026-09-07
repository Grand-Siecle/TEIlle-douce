"""Rebuild the versioned ALTO fixture, and the golden file beside it.

Six real pages of the private corpus, thinned to a few tens of kilobytes
while keeping everything the pipeline has to know how to handle. The
pages were chosen by minimal coverage of the SegmOnto labels the corpus
actually uses: eleven distinct labels in five pages, plus one page
carrying a note anchor and one carrying a hyphenated break.

Only useful to REGENERATE the fixture, and it needs the private corpus.
What it produces is versioned, and is all the test suite needs.

It used to be `scripts/build_test_fixture.py`, which tested
`if "--golden" in sys.argv` and let everything else — `--help` and every
typo included — fall through to a function whose first act is `rmtree` of
the versioned fixture. **Asking for the help destroyed a file the strict
golden diff depends on.** That is what an `ArgumentParser` is for, and it
is why this moved into the package: `scripts/` is not importable from an
installed distribution, so the subcommand could not have shared it.
"""

import re
import shutil
from pathlib import Path

from lxml import etree

from teille_douce.cli.exits import MISCONFIGURED, USAGE, refuse
from teille_douce.paths import CHECKOUT, ROOT

NS_ALTO = "http://www.loc.gov/standards/alto/ns-v4#"
NS = {"a": NS_ALTO}

DEFAULT_SOURCE = ROOT / "OCR"
DEFAULT_TARGET = ROOT / "tests" / "fixtures" / "alto_min" / "LIV9001_reconciled"

# (path inside the corpus, name to write, what the page contributes)
PAGES = [
    ("LIV0039b_reconciled/content/data/doc_1/f874.xml", "f1.xml",
     "MainZone, MarginTextZone, DropCapitalZone, NumberingZone, "
     "QuireMarksZone, RunningTitleZone, default, ¬ break"),
    ("LIV0039b_reconciled/content/data/doc_1/f533.xml", "f2.xml", "GraphicZone"),
    ("LIV0044_reconciled/content/data/doc_1/f3.xml", "f3.xml", "TitlePageZone"),
    ("LIV0044_reconciled/content/data/doc_1/f188.xml", "f4.xml",
     "note anchor + MarginTextZone"),
    ("LIV0044_reconciled/content/data/doc_1/f190.xml", "f5.xml", "StampZone"),
    ("LIV0039b_reconciled/content/data/doc_1/f141.xml", "f6.xml",
     "CustomZone, ¬ break"),
    ("LIV0039a_reconciled/content/data/doc_1/f346.xml", "f7.xml",
     "a plain hyphen break (mainte-)"),
    # The only page in the corpus — fifty-four documents — carrying a line
    # label other than DefaultLine: one HeadingLine, which drives the <hi>
    # branch of body/builder.py. DropCapitalLine is applied nowhere.
    ("LIV0042_reconciled/content/data/doc_1/f560.xml", "f8.xml",
     "HeadingLine -> the <hi> branch"),
]

# A line is kept regardless if its text carries a trait worth testing.
INTERESTING = re.compile(r"[¬*†‡]|-$")
LINES_PER_BLOCK = 2         # ordinary lines kept per block
INTERESTING_PER_BLOCK = 2   # plus at most this many carrying a trait
POLYGON_POINTS = 4          # coordinates kept per Polygon
GLYPHS_PER_STRING = 2       # keeps the GC/WC coverage without the volume


def _thin_polygons(element):
    for polygon in element.iter(f"{{{NS_ALTO}}}Polygon"):
        points = (polygon.get("POINTS") or "").split()
        if len(points) > POLYGON_POINTS * 2:
            polygon.set("POINTS", " ".join(points[: POLYGON_POINTS * 2]))


def _line_text(line):
    return " ".join(s.get("CONTENT", "") for s in line.iter(f"{{{NS_ALTO}}}String"))


def _tag_map(root):
    return {tag.get("ID"): tag.get("LABEL")
            for tag in root.iter(f"{{{NS_ALTO}}}OtherTag") if tag.get("ID")}


def _is_labelled_line(line, tags):
    """True where the line carries a SegmOnto label other than the default.

    These are vanishingly rare in the corpus — one HeadingLine across
    fifty-four documents — and they drive the <hi> branch of
    body/builder.py, so they are kept whatever the cap.
    """
    for reference in (line.get("TAGREFS") or "").split():
        label = tags.get(reference, "")
        if label.endswith("Line") and label != "DefaultLine":
            return True
    return False


def thin_page(source, name, number, target):
    tree = etree.parse(str(source),
                       etree.XMLParser(huge_tree=True, remove_blank_text=False))
    root = tree.getroot()

    # The image name has to follow the renumbering.
    for filename in root.iter(f"{{{NS_ALTO}}}fileName"):
        filename.text = f"{Path(name).stem}.jpg"
    for page in root.iter(f"{{{NS_ALTO}}}Page"):
        page.set("PHYSICAL_IMG_NR", str(number - 1))

    tags = _tag_map(root)
    for block in root.iter(f"{{{NS_ALTO}}}TextBlock"):
        lines = block.findall("a:TextLine", namespaces=NS)
        kept, ordinary, special = [], 0, 0
        for line in lines:
            if _is_labelled_line(line, tags):
                kept.append(line)
                continue
            notable = bool(INTERESTING.search(_line_text(line)))
            if notable and special < INTERESTING_PER_BLOCK:
                kept.append(line)
                special += 1
            elif not notable and ordinary < LINES_PER_BLOCK:
                kept.append(line)
                ordinary += 1
        for line in lines:
            if line not in kept:
                block.remove(line)

    # Glyphs: a few are enough, and they are the whole of the volume.
    for string in root.iter(f"{{{NS_ALTO}}}String"):
        for glyph in string.findall("a:Glyph", namespaces=NS)[GLYPHS_PER_STRING:]:
            string.remove(glyph)

    _thin_polygons(root)

    written = target / "content" / "data" / "doc_1" / name
    written.parent.mkdir(parents=True, exist_ok=True)
    tree.write(str(written), encoding="UTF-8", xml_declaration=True,
               pretty_print=True)
    return written.stat().st_size


def build(source=DEFAULT_SOURCE, target=DEFAULT_TARGET, say=print):
    """Rebuild the fixture from the private corpus."""
    source, target = Path(source), Path(target)
    if not source.exists():
        refuse(f"source corpus not found: {source}", MISCONFIGURED,
               "teille-douce fixture")
    # EVERY page, before anything is removed. The whole-corpus guard was
    # first and the per-page one was inside the loop, so a single page
    # renamed in the private corpus rmtree'd the versioned fixture, wrote
    # the pages before it, and then raised — leaving the file the strict
    # golden diff compares against deleted and half rebuilt, with no IIIF
    # mapping. The ordering is the point of this function.
    missing = [relative for relative, _, _ in PAGES
               if not (source / relative).exists()]
    if missing:
        refuse("source pages missing, nothing was removed:\n  "
               + "\n  ".join(missing), MISCONFIGURED, "teille-douce fixture")
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)

    total = 0
    say("Building the minimal ALTO fixture\n")
    for number, (relative, name, contributes) in enumerate(PAGES, 1):
        page = source / relative
        before = page.stat().st_size
        after = thin_page(page, name, number, target)
        total += after
        say(f"  {name}  {before / 1024:7.0f} kB -> {after / 1024:5.1f} kB   "
            f"{contributes}")

    # The IIIF mapping: three columns, no header, the shape IIIFMapping
    # expects.
    mapping = target / "gallica-bnf-fr-iiif-manifest-json.csv"
    mapping.write_text("".join(
        f"https://gallica.bnf.fr/iiif/ark:/12148/bpt6k9001/f{number}"
        f"/full/full/0/native.jpg,bpt6k9001,f{number}\n"
        for number in range(1, len(PAGES) + 1)), encoding="utf-8")
    say(f"\n  IIIF mapping: {mapping.name} ({len(PAGES)} entries)")
    say(f"\nFixture total: {total / 1024:.1f} kB")
    return total


def rebuild_golden(say=print):
    """Regenerate the reference output of the short mode."""
    import sys
    import tempfile

    if CHECKOUT is None:
        refuse("teille-douce fixture needs the source checkout: the "
               "fixture, the golden file and the end-to-end harness that "
               "produces it are versioned beside the sources, and an "
               "installed distribution does not carry them.",
               MISCONFIGURED, "teille-douce fixture")
    sys.path.insert(0, str(ROOT / "tests"))
    from test_e2e_pipeline import GOLDEN, MODE_COURT, lancer_pipeline, normaliser

    tei = lancer_pipeline(Path(tempfile.mkdtemp()), **MODE_COURT)
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(normaliser(tei), encoding="utf-8")
    say(f"golden written: {GOLDEN.relative_to(ROOT)} "
        f"({GOLDEN.stat().st_size / 1024:.1f} kB)")


def add_arguments(parser):
    """The surface of `teille-douce fixture`."""
    # No abbreviations on THIS command. Every other subcommand can afford
    # them; this one rebuilds versioned files, and argparse resolving
    # `--gold` to `--golden` is a typo that silently rewrites the file the
    # strict end-to-end diff compares against.
    parser.allow_abbrev = False
    parser.add_argument(
        "action", nargs="?", default=None, choices=("build", "golden"),
        help="build: rebuild the versioned fixture from the private corpus. "
             "golden: regenerate the reference output instead, by running "
             "the short pipeline over the fixture")
    parser.add_argument(
        "--golden", action="store_true",
        help="the same as the `golden` action")
    parser.add_argument(
        "--source", type=Path, default=DEFAULT_SOURCE, metavar="DIR",
        help=f"the private corpus to thin (default: {DEFAULT_SOURCE.name}/)")
    parser.add_argument(
        "--target", type=Path, default=DEFAULT_TARGET, metavar="DIR",
        help="where to write the fixture (default: the versioned one)")
    return parser


def execute(args):
    # Two ways of asking for different things is a usage error, not a
    # precedence rule: `fixture build --golden` used to regenerate the
    # golden and never build the fixture, saying nothing about the action
    # it had discarded.
    if args.golden and args.action == "build":
        # 2: two flags that contradict each other is what the exit-code
        # table calls a usage error, and `raise SystemExit(str)` exits 1.
        refuse("fixture build and --golden ask for different things: "
               "`fixture build` rebuilds the fixture, `fixture golden` "
               "rebuilds the reference output", USAGE, "teille-douce fixture")
    if args.golden or args.action == "golden":
        rebuild_golden()
        return 0
    build(source=args.source, target=args.target)
    return 0
