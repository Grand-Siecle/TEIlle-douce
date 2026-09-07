"""Is this installation usable, and what will it cost?

The question the pipeline could not answer without being run. The user
guide says so itself — "it is also the mode to use to check that your
input is well formed, before committing to a long run" — of a mode that
converts the whole corpus. Forty minutes to learn that a `BDD` column
matches nothing.

Four of the ten troubleshooting entries in that guide are diagnoses this
poses in two seconds: an unmatched catalogue row, a persons CSV that did
not load, file names with no sortable number, an IIIF mapping under the
threshold and therefore refused.

Nothing here writes anything: it answers, and
`cli/check.py` renders the answer. The analyses are the run's own —
`pages_sharing_a_number`, `find_metadata_row`, `IIIFMapping.detect_csv`,
the same service probes — because a preflight that disagreed with the run
it precedes would be worse than none.
"""

from dataclasses import dataclass, field
from pathlib import Path

from teille_douce.config import IIIF_CSV_MIN_MATCH_RATE
from teille_douce.utils.files import NO_PAGE_NUMBER, pages_sharing_a_number


@dataclass(frozen=True, slots=True)
class Attention:
    """One thing worth looking at before a run, and what it would cost.

    `subject` is what to go and look at — a volume, a person key, a
    service. `consequence` is what a run would do about it, because a
    line that says something is wrong without saying what it costs is a
    line an operator learns to skip.
    """

    subject: str
    detail: str
    consequence: str


@dataclass(frozen=True, slots=True)
class Service:
    name: str
    endpoint: str
    state: str          # "up", "refused", "not asked for", "missing"
    detail: str = ""


@dataclass(frozen=True, slots=True)
class Preflight:
    """Everything `check` is allowed to know, all of it measured."""

    input_dir: Path
    output_dir: Path
    volumes: tuple = ()          # (name, page count)
    archives: int = 0
    already_converted: int = 0
    output_writable: bool = True
    catalogue: dict = field(default_factory=dict)
    persons: dict = field(default_factory=dict)
    services: tuple = ()
    attention: tuple = ()
    unusable: tuple = ()         # (setting, path, reason, where)

    @property
    def pages(self):
        return sum(pages for _, pages in self.volumes)

    @property
    def verdict(self):
        """0 nothing to say, 1 usable but degraded, 3 unusable.

        Unusable is not a matter of degree: no input directory, no
        volume in it, or an output that cannot be written. Everything
        else is a corpus of seventeenth-century OCR, where imperfection
        is the normal state and stopping on it would stop every run.
        """
        if self.unusable or not self.volumes or not self.output_writable:
            return 3
        return 1 if self.attention else 0


def _writable(path):
    """Whether the output directory can be written, without writing.

    The first existing ancestor is what decides: `-o runs/2026-09/tei`
    names three directories that do not exist yet, and the run creates
    them.
    """
    import os

    ancestor = Path(path)
    while not ancestor.exists():
        parent = ancestor.parent
        if parent == ancestor:
            return False
        ancestor = parent
    return ancestor.is_dir() and os.access(ancestor, os.W_OK)


def _volumes(ocr_dir):
    """Directories holding ALTO, and archives waiting to be unpacked.

    Deliberately simpler than the run's own discovery, which has
    selectors, `--limit` and `--skip-existing` to honour. `check` is
    asked about the installation, not about one invocation.
    """
    volumes, archives, without_alto = [], [], []
    if not ocr_dir.is_dir():
        return volumes, archives, without_alto
    for entry in sorted(ocr_dir.iterdir()):
        if entry.is_dir():
            pages = sorted(entry.rglob("*.xml"))
            if pages:
                volumes.append((entry.name, pages))
            else:
                without_alto.append(entry.name)
        elif entry.suffix == ".zip":
            archives.append(entry.name)
    return volumes, archives, without_alto


def _catalogue(settings, names):
    """How much of the corpus the book catalogue actually covers."""
    from teille_douce.metadata.csv_book import find_metadata_row, load_metadata

    table = load_metadata(settings.metadata_csv)
    if table is None:
        return {"path": settings.metadata_csv, "rows": None,
                "matched": 0, "unmatched": list(names)}
    unmatched = []
    for name in names:
        # `find_metadata_row` logs when it finds nothing, which is right
        # during a run and noise during a preflight; the caller silences
        # the module for the duration.
        if find_metadata_row(table, name) is None:
            unmatched.append(name)
    return {"path": settings.metadata_csv, "rows": len(table),
            "matched": len(names) - len(unmatched), "unmatched": unmatched}


def _persons(settings):
    """Whether the persons table loaded, and how many it holds."""
    from teille_douce.metadata.csv_person import PersonDatabase

    database = PersonDatabase()
    loaded = False
    try:
        loaded = bool(database.load(settings.persons_csv))
    except Exception:                       # pragma: no cover - defensive
        loaded = False
    return {"path": settings.persons_csv, "loaded": loaded,
            "count": len(database) if hasattr(database, "__len__") else 0}


def _services(settings, probe):
    """What each phase's service says, or that nobody asked it."""
    from teille_douce.cli.run import missing_ner_dependencies

    services = []

    if not settings.modernize:
        services.append(Service("VieuxParler", "modernization",
                                "not asked for"))
    elif not probe:
        services.append(Service("VieuxParler", "modernization", "not probed"))
    else:
        from teille_douce.modernize import check_api

        services.append(Service("VieuxParler", "modernization",
                                "up" if check_api() else "refused"))

    if not settings.enrich:
        services.append(Service("PyHellen", "enrichment", "not asked for"))
    elif not probe:
        services.append(Service("PyHellen", "enrichment", "not probed"))
    else:
        from teille_douce.enrichment.client import check_server

        services.append(Service("PyHellen", "enrichment",
                                "up" if check_server() else "refused"))

    if not settings.ner:
        services.append(Service("NER models", "entity recognition",
                                "not asked for"))
    else:
        missing = missing_ner_dependencies()
        services.append(Service(
            "NER models", "entity recognition",
            "missing" if missing else "up",
            ", ".join(missing) if missing else ""))
    return tuple(services)


def _iiif_rate(directory, pages):
    """How much of a volume's IIIF mapping would be accepted, or None.

    The detection refuses a CSV matching less than thirty per cent of the
    file names, and says nothing: the volume then converts with no
    @source at all, and an operator who provided a mapping has no way to
    learn it was thrown away.
    """
    from teille_douce.metadata.iiif import IIIFMapping

    if IIIFMapping.detect_csv(directory, pages) is not None:
        return None                    # accepted, nothing to say
    import pandas as pd

    from teille_douce.config import IIIF_CSV_PATTERNS

    names = {page.name for page in pages} | {page.stem for page in pages}
    best = None
    for pattern in IIIF_CSV_PATTERNS:
        for candidate in directory.glob(pattern):
            try:
                table = pd.read_csv(candidate, header=None, dtype=str,
                                    keep_default_na=False)
            except Exception:
                continue
            if table.shape[1] < 3 or not len(table):
                continue
            column = table.iloc[:, 2].astype(str).str.strip()
            matched = sum(1 for value in column
                          if value in names
                          or value.replace(".xml", "") in names)
            rate = matched / len(column)
            best = rate if best is None else max(best, rate)
    return best


def inspect(settings, probe=True):
    """Everything `check` reports, measured once."""
    import logging

    volumes, archives, without_alto = _volumes(settings.ocr_dir)
    names = [name for name, _ in volumes]

    quiet = logging.getLogger("teille_douce.metadata")
    was = quiet.level
    quiet.setLevel(logging.CRITICAL)
    try:
        catalogue = _catalogue(settings, names)
        persons = _persons(settings)
    finally:
        quiet.setLevel(was)

    attention = []
    for name in catalogue["unmatched"]:
        attention.append(Attention(
            name, f"no catalogue row for {name!r}",
            "its header would keep every placeholder"))
    if not persons["loaded"]:
        attention.append(Attention(
            Path(persons["path"]).name, "the persons table did not load",
            "no curated person would reach <particDesc>"))

    for name, pages in volumes:
        directory = settings.ocr_dir / name
        for number, paths in pages_sharing_a_number(name, pages).items():
            shown = ", ".join(path.name for path in paths[:3])
            if number == NO_PAGE_NUMBER:
                attention.append(Attention(
                    name, f"{len(paths)} files carry no page number ({shown})",
                    "they share one IIIF view and are placed last"))
            else:
                attention.append(Attention(
                    name,
                    f"{len(paths)} files claim page {number} ({shown})",
                    "the page numbering is ambiguous"))
        rate = _iiif_rate(directory, pages)
        if rate is not None:
            attention.append(Attention(
                name,
                f"IIIF mapping matches {rate:.0%} of the file names "
                f"(under {IIIF_CSV_MIN_MATCH_RATE:.0%})",
                "it would be refused and the zones carry no @source"))

    for name in without_alto:
        attention.append(Attention(
            name, "the directory holds no ALTO",
            "nothing would be converted from it"))

    services = _services(settings, probe)
    for service in services:
        if service.state == "refused":
            attention.append(Attention(
                service.name, f"refused the {service.endpoint} probe",
                "a run would produce none of that phase's output"))
        elif service.state == "missing":
            attention.append(Attention(
                service.name, f"dependencies not installed ({service.detail})",
                "a run would produce no <standOff>"))

    return Preflight(
        input_dir=settings.ocr_dir, output_dir=settings.output_dir,
        volumes=tuple((name, len(pages)) for name, pages in volumes),
        archives=len(archives),
        already_converted=sum(
            1 for name, _ in volumes
            if (settings.output_dir / f"{name}.tei.xml").exists()),
        output_writable=_writable(settings.output_dir),
        catalogue=catalogue, persons=persons, services=services,
        attention=tuple(attention),
        unusable=settings.unreadable_inputs())
