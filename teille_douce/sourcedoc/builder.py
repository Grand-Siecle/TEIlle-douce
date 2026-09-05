# -----------------------------------------------------------
# Code by: Kelly Christensen
# Orchestrates the construction of <sourceDoc> with parallel processing.
# -----------------------------------------------------------
"""
SourceDoc builder module.

This module handles the construction of the TEI <sourceDoc> element from
ALTO XML files. Uses multiprocessing for parallel page processing.
"""

import logging
import multiprocessing
from pathlib import Path
from multiprocessing import cpu_count

from lxml import etree
from rich.markup import escape as markup_escape

from teille_douce.settings import get_settings, set_settings

logger = logging.getLogger(__name__)

# fork() in a multi-threaded parent (rich's progress refresh thread,
# coverage's tracing) deadlocks intermittently — observed locally and in
# CI, and warned about by Python itself. The forkserver context forks
# workers from a clean single-threaded server instead; preloading this
# module there keeps worker startup fork-fast (no per-worker re-import
# of lxml/pandas). spawn is the fallback where forkserver is missing.
if "forkserver" in multiprocessing.get_all_start_methods():
    _MP_CONTEXT = multiprocessing.get_context("forkserver")
    _MP_CONTEXT.set_forkserver_preload(["teille_douce.sourcedoc.builder"])
else:  # pragma: no cover - non-POSIX platforms
    _MP_CONTEXT = multiprocessing.get_context("spawn")
from ..constants import NS_ALTO, NS_ALTO_URI, XML_ID
from ..utils.files import Files, NO_PAGE_NUMBER
from ..metadata.iiif import IIIFMapping
from .attributes import Attributes
from .elements import SurfaceTree, build_alto_id_index


# XML parser configured for large ALTO files. Strict: recovery is a
# separate, explicit second chance in _parse_alto, always reported
# (audit 2.3 — no more silent repairs).
XML_PARSER = etree.XMLParser(huge_tree=True)


_TAG_OTHERTAG = f"{{{NS_ALTO_URI}}}OtherTag"
_TAG_TAGS = f"{{{NS_ALTO_URI}}}Tags"


def extract_labels(source):
    """
    Extract SegmOnto labels from an ALTO file.

    The single label extractor of the pipeline (header and workers).
    OtherTag entries missing ID or LABEL are skipped instead of raising
    KeyError (audit 2.3).

    Args:
        source: An already-parsed ALTO root element (the workers' case:
            no second parse, audit 3.4), or a path — then read with
            iterparse and an early stop after </Tags> (audit 3.2: the
            labels sit in the first kilobyte of megabyte files). A file
            that does not parse before its Tags contributes no labels;
            its fate is decided page by page in the workers.

    Returns:
        dict: Mapping of element IDs to their LABEL values.
    """
    if isinstance(source, etree._Element):
        elements = [t.attrib for t in source.findall(".//a:OtherTag", namespaces=NS_ALTO)]
        return {d["ID"]: d["LABEL"] for d in elements if "ID" in d and "LABEL" in d}

    labels = {}
    try:
        for _, elem in etree.iterparse(
            str(source), events=("end",),
            tag=(_TAG_OTHERTAG, _TAG_TAGS), huge_tree=True,
        ):
            if elem.tag == _TAG_OTHERTAG:
                tag_id, label = elem.get("ID"), elem.get("LABEL")
                if tag_id and label:
                    labels[tag_id] = label
            else:  # </Tags> reached: labels only live there, stop reading
                break
    except etree.XMLSyntaxError as e:
        logger.warning("No labels read from %s (unparseable: %s)", source, e)
        return {}
    return labels


# What every page of one document shares. Sent to each worker ONCE, by
# the Pool initializer, instead of riding in every job: the IIIF mapping
# of a 850-page volume was pickled 850 times, which is O(N²) bytes of
# pure repetition for a dict that never changes within a document
# (audit 3.7).
_JOB_CONTEXT = {}


def _init_worker(document_name, segmonto_zones, segmonto_lines, config,
                 iiif_mapping_dict, settings=None):
    """Store one document's invariants in the worker process.

    `settings` travels with them because a forkserver/spawn child
    re-imports in a fresh interpreter, where get_settings() would rebuild
    from the environment alone and therefore ignore every command-line
    flag. Nothing under this function reads a runtime setting today; the
    plumbing is here so that the first one to do so is correct rather than
    silently reading the environment behind the CLI's back.
    """
    if settings is not None:
        set_settings(settings)
    _JOB_CONTEXT.update(
        document_name=document_name,
        segmonto_zones=segmonto_zones,
        segmonto_lines=segmonto_lines,
        config=config,
        iiif_mapping_dict=iiif_mapping_dict,
    )


def _build_surface_fragment(args):
    """
    Build a <surface> fragment for a single ALTO page.

    This function is designed to run in a multiprocessing pool.
    It processes one ALTO file and returns a serialized surface element.

    Args:
        args (tuple): (job_index, filepath, num) — everything else is the
            document's invariants, set once per worker by _init_worker.
            `job_index` is the file's position in the ordered file list,
            and it is what routes the result back to its page: `num`
            cannot, because two files can share one (the same first digit
            run in their names, or no digit at all).

    Returns:
        tuple: (job_index, xml_bytes, error, warning, duplicated) - the
        job's position, serialized surface XML (None on failure), an error
        message (None on success), a warning (e.g. "recovered malformed
        XML", None when clean), and how many ALTO ids this page had to
        disambiguate. The count travels in the tuple because it is the one
        measurement made inside a worker that the summary needs: a logger
        call in a forkserver child never reaches the parent. Errors travel
        as plain strings: lxml exceptions are not picklable and used to
        come back as an opaque MaybeEncodingError that killed the whole
        pool, losing the already-processed pages (audit 5.5).
    """
    job_index, filepath, num = args

    try:
        # Inside the try like everything else: this function must never
        # raise (audit 2.3/5.5), and a worker whose initializer did not
        # run would otherwise take the whole document down with it.
        _, xml_bytes, warning, duplicated, minted = _build_surface_fragment_inner(
            _JOB_CONTEXT["document_name"], filepath, num,
            _JOB_CONTEXT["segmonto_zones"], _JOB_CONTEXT["segmonto_lines"],
            _JOB_CONTEXT["config"], _JOB_CONTEXT["iiif_mapping_dict"],
        )
        return job_index, xml_bytes, None, warning, duplicated, minted
    except Exception as e:  # audit 2.3: one bad page must not kill the run
        return job_index, None, f"{type(e).__name__}: {e}", None, 0, 0


def _parse_alto(filepath):
    """
    Parse an ALTO file: strict first, libxml2 recovery as a second chance.

    Audit 2.3 asked for one parser and no *silent* repairs. Recovery is
    still valuable for HTR exports with small quirks (unescaped ampersands,
    stray control characters): losing a whole page is worse than keeping a
    mostly-correct one — as long as it is reported. Returns (root, warning)
    where warning is None for a clean parse; raises when even recovery
    cannot produce a tree.
    """
    try:
        return etree.parse(str(filepath), parser=XML_PARSER).getroot(), None
    except etree.XMLSyntaxError as strict_error:
        recovery_parser = etree.XMLParser(huge_tree=True, recover=True)
        try:
            root = etree.parse(str(filepath), parser=recovery_parser).getroot()
        except etree.XMLSyntaxError:
            root = None
        if root is None:
            raise strict_error
        n_err = len(recovery_parser.error_log)
        first = recovery_parser.error_log[0] if n_err else strict_error
        return root, f"malformed XML recovered ({n_err} parse error(s); first: {first})"


def _build_surface_fragment_inner(document_name, filepath, num, segmonto_zones,
                                  segmonto_lines, config, iiif_mapping_dict):
    # Parse ALTO file (single parse, reused for the labels below)
    input_alto_root, warning = _parse_alto(filepath)
    file_stem = Path(filepath).stem

    # Extract zone/line type mappings from the already-parsed root (audit 3.4)
    tags = extract_labels(input_alto_root)

    # One first-wins ID index per page, shared with SurfaceTree
    # (audit 3.5 — review follow-up: the worker used to build a second,
    # last-wins index over the same tree; on the corpus pages carrying
    # duplicated ALTO ids both indexes point at identical duplicated
    # blocks, so first-wins is a safe single semantics).
    by_id = build_alto_id_index(input_alto_root)

    # Create surface tree builder (with or without IIIF mapping)
    if iiif_mapping_dict:
        iiif_mapping = IIIFMapping()
        iiif_mapping.mapping = iiif_mapping_dict
        surface_tree = SurfaceTree(document_name, file_stem, input_alto_root,
                                   iiif_mapping, by_id=by_id)
    else:
        surface_tree = SurfaceTree(document_name, file_stem, input_alto_root,
                                   by_id=by_id)

    # Create <surface> element
    # `num` is the page number parsed from the filename stem by
    # Files.order_files() ("f1.xml" -> 1). Using it as the IIIF view number
    # (f<N>) ASSUMES the corpus convention that ALTO files are named after
    # their 1-based scan view; documents numbered from 0 yield view_number=0
    # for their first page, which Attributes treats as untrustworthy (no
    # @source emitted).
    attributes = Attributes(document_name, file_stem, input_alto_root, tags,
                            dict(config, view_number=num))
    surface = surface_tree.surface(attributes.surface())

    # Pre-group TextLines by their block's ID in one pass (audit 3.5).
    # NB: duplicated block IDs exist in real exports; the old per-block
    # XPath returned the UNION of all same-ID blocks' lines, so the
    # grouping reproduces exactly that.
    alto_ns = NS_ALTO["a"]
    lines_by_block = {}
    for block in input_alto_root.iter(f"{{{alto_ns}}}TextBlock"):
        block_lines = lines_by_block.setdefault(block.get("ID"), [])
        block_lines.extend(block.findall(f"{{{alto_ns}}}TextLine"))

    # Process TextBlocks
    textblocks = attributes.zones("PrintSpace", "TextBlock", segmonto_zones)

    for tb in textblocks:
        if not tb.id:
            continue

        textblock = surface_tree.zone1(surface, tb.attributes, tb.id)
        textlines = attributes.zones(
            f'TextBlock[@ID="{tb.id}"]', "TextLine", segmonto_lines,
            elements=lines_by_block.get(tb.id, []),
        )
        line_count = 0

        for tl in textlines:
            if not tl.id:
                continue

            textline = surface_tree.zone2(textblock, tb.id, tl.attributes, tl.id)

            # Get the ALTO TextLine element
            line_node = by_id.get(tl.id)
            if line_node is None:
                continue

            # Extract text content from String and SP elements
            words_parts = []
            for child in line_node:
                local = etree.QName(child).localname
                if local == "String":
                    content = child.get("CONTENT")
                    if content:
                        words_parts.append(content)
                elif local == "SP":
                    words_parts.append(" ")

            line_count += 1
            surface_tree.line(textline, tb.id, tl.id, line_count, "".join(words_parts).strip())

    # Duplicate ALTO ids were disambiguated by SurfaceTree; report them
    # through the return tuple — a logger call inside a forkserver worker
    # never reaches the parent's log file.
    duplicated = _duplicate_ids(surface_tree._seen_keys)
    minted = _distinct_ids(surface_tree._seen_keys)
    if duplicated:
        dup_note = f"{duplicated} duplicate ALTO id(s) disambiguated"
        warning = f"{warning}; {dup_note}" if warning else dup_note

    return (num, etree.tostring(surface, encoding="utf-8"), warning,
            duplicated, minted)


def _distinct_ids(seen_keys):
    """How many ALTO ids this page used at all.

    The denominator the repaired count is measured against: "6 174 of
    41 908" says a defect was widespread, "6 174 of 6 174" says nothing.
    """
    return len({parts for _prefix, parts in seen_keys})


def _duplicate_ids(seen_keys):
    """How many ALTO ids were duplicated — not how many registry entries.

    The registry is keyed on (TEI prefix, ALTO ids), so one duplicated
    ALTO id reported under `zoneLine_`, `path_`, `line_` and `string_`
    used to count as four. This figure is printed in the summary as a
    repaired source defect, and it is the largest number this corpus
    produces: a fourfold exaggeration would make the biggest line of the
    report the least trustworthy one.
    """
    return len({parts for (_prefix, parts), count in seen_keys.items()
                if count > 1})


def build_sourcedoc(
    document_name,
    output_tei_root,
    filepath_list,
    segmonto_zones,
    segmonto_lines,
    config,
    progress=None,
    parent_task_pages=None,
    iiif_mapping=None,
):
    """
    Build the <sourceDoc> element with parallel page processing.

    Processes all ALTO files for a document in parallel using multiprocessing,
    then assembles the results in page order.

    Args:
        document_name (str): Name of the document.
        output_tei_root (etree.Element): TEI root element to append sourceDoc to.
        filepath_list (list): List of ALTO file paths.
        segmonto_zones (list): Valid SegmOnto zone types.
        segmonto_lines (list): Valid SegmOnto line types.
        config (dict): IIIF configuration dictionary.
        progress: Optional Rich progress bar instance.
        parent_task_pages: Optional task ID for progress updates.
        iiif_mapping (IIIFMapping): Optional IIIF URL mapping instance.

    Returns:
        tuple: (root, skipped_pages, repaired_ids, minted_ids)
            - root: the updated TEI root element
            - skipped_pages: sorted file stems whose ALTO was unusable —
              the stem is the surface xml:id, so each one is an address
            - repaired_ids: ALTO ids this document had to disambiguate —
              a source defect that was repaired, not a loss, and the
              largest single figure this corpus produces

    Raises:
        RuntimeError: if two pages would be emitted under one surface
            xml:id, which no XML parser would read back.
    """
    # Order files by page number
    ordered_files = Files(document_name, filepath_list).order_files()

    # That number is not unique. Files.order_files() reads the first digit
    # run of the file stem ("f12.xml" and "f12-np.xml" both give 12) and
    # hands every digit-less file the same sentinel. Colliding files are
    # all converted — the jobs below are routed by position, not by number
    # — but the number they share is the IIIF view their zones' @source is
    # built from, and surface/@n derives from the same filename numbers, so
    # the operator is told which files made this numbering ambiguous.
    files_by_num = {}
    for f in ordered_files:
        files_by_num.setdefault(f.num, []).append(f.filepath)
    for num, paths in sorted(files_by_num.items()):
        if len(paths) < 2:
            continue
        listed = ", ".join(str(p) for p in paths[:10]) + (
            f" (+{len(paths) - 10} more)" if len(paths) > 10 else ""
        )
        if num == NO_PAGE_NUMBER:
            logger.warning(
                "%s: %d files carry no page number in their name (%s). All "
                "are converted, and placed last, but they share one IIIF "
                "view number in their zones' @source.",
                document_name, len(paths), listed,
            )
        else:
            logger.warning(
                "%s: page number %s is claimed by %d files (%s). All are "
                "converted, but this document's page numbering is "
                "ambiguous: those pages share one IIIF view number in "
                "their zones' @source, and surface/@n derives from the "
                "same filename numbers.",
                document_name, num, len(paths), listed,
            )
    sourceDoc = etree.SubElement(output_tei_root, "sourceDoc")
    total_pages = len(ordered_files)

    # Prepare IIIF mapping for multiprocessing (must be serializable)
    iiif_mapping_dict = None
    if iiif_mapping and iiif_mapping.has_mapping():
        iiif_mapping_dict = iiif_mapping.mapping.copy()

    # One job carries only what changes from page to page, plus its
    # position in the ordered list: that position is the routing key, and
    # it is unique by construction. Keying the results by `num` dropped one
    # of two colliding pages and emitted the other twice, under a duplicate
    # xml:id that lxml itself refuses to read back.
    jobs = [(i, f.filepath, f.num) for i, f in enumerate(ordered_files)]

    # Limit workers to avoid resource exhaustion — and never more than
    # there are pages: each worker now unpickles the document's
    # invariants once, so eight of them for a two-page document would
    # send the IIIF mapping eight times to do two pages' work.
    settings = get_settings()
    workers = max(1, min(cpu_count(), settings.max_workers, len(jobs)))
    asked = settings.origin("max_workers")
    if settings.max_workers > cpu_count() and asked != "default":
        # Only when someone asked: the default is 8, so an unguarded test
        # warned on every document of every run on a machine with fewer
        # cores, naming a flag nobody had typed. And the origin is named,
        # because the value may have come from TDOUCE_JOBS or limits.jobs
        # rather than from -j.
        logger.warning(
            "%s: %d workers requested (%s) exceeds the %d available cores; "
            "running %d", document_name, settings.max_workers, asked,
            cpu_count(), workers,
        )

    # One slot per job, addressed by position: a result can only ever land
    # in its own slot, and the slots are already in reading order.
    results = [None] * len(jobs)

    skipped_pages = []
    repaired_ids = 0
    minted_ids = 0

    # Process pages in parallel
    # No `with Pool(...)`: Pool.__exit__ calls terminate(), which SIGTERMs
    # idle workers — and under `coverage run` (sigterm=true) a worker
    # killed mid-write can hang in its signal handler, blocking join()
    # forever (observed as intermittent 15-minute CI timeouts). Results
    # are fully consumed first, then close()+join() lets workers exit
    # cleanly; terminate() only on an actual error.
    pool = _MP_CONTEXT.Pool(
        workers,
        initializer=_init_worker,
        initargs=(
            document_name, segmonto_zones, segmonto_lines, config,
            iiif_mapping_dict, settings,
        ),
    )
    try:
        for done, (job_index, xml_bytes, error, warning, duplicated,
                   minted) in enumerate(
            pool.imap_unordered(_build_surface_fragment, jobs), start=1
        ):
            page = ordered_files[job_index]
            if warning:
                logger.warning("%s: page %s (%s) %s", document_name,
                               page.num, page.filepath.name, warning)
            if error is not None:
                # Audit 2.3: report and skip the page, keep the document.
                # The filename is part of the report: a page number does
                # not identify a page when two files share one.
                logger.error(
                    "%s: page %s skipped, ALTO unusable (%s: %s)",
                    document_name, page.num, page.filepath.name, error,
                )
                # The file stem, not the page number: the stem IS the
                # surface xml:id, so it resolves to a file to open, an
                # XPath that lands and a IIIF region. A number resolves to
                # none of those.
                skipped_pages.append(page.filepath.stem)
            repaired_ids += duplicated
            minted_ids += minted
            results[job_index] = xml_bytes

            # Update progress bar if provided
            if progress and parent_task_pages is not None:
                progress.update(
                    parent_task_pages,
                    description=f"[cyan]{markup_escape(document_name)}[/cyan] page {done}/{total_pages}",
                    advance=1,
                )
        pool.close()
    except BaseException:
        pool.terminate()
        raise
    finally:
        pool.join()

    # Assemble surfaces in reading order: `results` is addressed by
    # position in `ordered_files`, which order_files() already returned
    # sorted by page number, so the two lists zip. (Skipped pages were
    # reported above, one ERROR line each, and left their slot empty.)
    emitted = []
    for f, frag in zip(ordered_files, results):
        if frag:
            sourceDoc.append(etree.fromstring(frag))
            emitted.append(f.filepath)

    # Every phase reports what it lost. One usable page owes one <surface>
    # carrying its own xml:id: the body's <pb>, the note anchors and the
    # IIIF crops all link to that id, so two pages sharing one are welded
    # into a single page and the file cannot be read back at all — lxml
    # refuses a duplicate xml:id, and nothing downstream re-parses what
    # this pipeline writes. Distinct filenames do not guarantee distinct
    # ids: xml_id_safe() prefixes a leading digit, so "1.xml" and "f1.xml"
    # both give "f1", and pages are discovered with rglob(), so two
    # subdirectories of one document can each hold an "f1.xml".
    id_owners = {}
    for filepath, surface in zip(emitted, sourceDoc):
        id_owners.setdefault(surface.get(XML_ID), []).append(filepath)
    shared = {i: paths for i, paths in id_owners.items() if len(paths) > 1}
    if shared:
        detail = "; ".join(
            f"{i}: {', '.join(str(p) for p in paths)}"
            for i, paths in sorted(shared.items())
        )
        raise RuntimeError(
            f"{len(shared)} page id(s) claimed by several ALTO files "
            f"({detail}); their surfaces would share one xml:id and the "
            f"TEI would be unreadable — rename the files"
        )

    return output_tei_root, sorted(skipped_pages), repaired_ids, minted_ids
