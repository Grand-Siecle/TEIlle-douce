# -----------------------------------------------------------
# The exit codes of `teille-douce run`, end to end.
#
# They used to be one number: 1 meant "your input directory is missing"
# and "forty volumes broke" alike, so a wrapper could not tell "fix your
# setup and rerun the same command" from "rerun what failed". They are a
# public contract now, so each one is exercised against the real binary.
#
#   0  everything asked for succeeded
#   1  some units failed
#   2  usage error
#   3  misconfiguration, nothing ran
#
# Run: venv/bin/python -m pytest tests/test_cli_exit_codes.py -q
# -----------------------------------------------------------
import shutil
import zipfile

import pytest

from test_e2e_pipeline import ALTO_MIN, DOCUMENT, MODE_COURT, _executer_main

pytestmark = pytest.mark.e2e


def test_a_missing_input_directory_is_a_misconfiguration(tmp_path):
    res, _ = _executer_main(tmp_path, tmp_path / "absent", **MODE_COURT)

    assert res.returncode == 3
    assert "absent" in res.stdout


def test_an_empty_input_directory_names_the_directory_it_read(tmp_path):
    """The message was the literal "OCR/" whatever the input actually was."""
    empty = tmp_path / "empty"
    empty.mkdir()

    res, _ = _executer_main(tmp_path, empty, **MODE_COURT)

    assert res.returncode == 3
    assert "empty" in res.stdout


def test_a_selector_that_names_nothing_stops_the_run(tmp_path):
    """A typo must not look like an empty corpus."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)

    res, sortie = _executer_main(tmp_path, ocr, args=("LIV9999",), **MODE_COURT)

    assert res.returncode == 3
    assert "LIV9999" in res.stdout
    assert not sortie.exists()


def test_excluding_every_volume_stops_the_run(tmp_path):
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)

    res, _ = _executer_main(tmp_path, ocr, args=("-x", "LIV*"), **MODE_COURT)

    assert res.returncode == 3


def test_a_corpus_of_only_broken_archives_is_a_failure_not_a_misconfiguration(tmp_path):
    """The input was there and unreadable, which is a different diagnosis —
    and the failures have to be named, not swallowed."""
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    (ocr / "broken.zip").write_bytes(b"not a zip at all")

    res, _ = _executer_main(tmp_path, ocr, **MODE_COURT)

    assert res.returncode == 1
    assert "broken.zip" in res.stdout


def test_dry_run_writes_nothing_at_all(tmp_path):
    """Including the output directory and the archives it would unpack."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    with zipfile.ZipFile(ocr / "extra.zip", "w") as archive:
        archive.writestr("content/data/doc/f1.xml", "<alto/>")

    res, sortie = _executer_main(tmp_path, ocr, args=("--dry-run",), **MODE_COURT)

    assert res.returncode == 0
    assert not sortie.exists(), "--dry-run created the output directory"
    assert not (ocr / "extra").exists(), "--dry-run unpacked an archive"
    assert "nothing written" in res.stdout


def test_require_services_refuses_to_start_when_a_service_is_down(tmp_path):
    """Without it, a whole corpus is quietly converted with no annotation
    at all — the warning scrolls past and every file looks complete."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)

    res, sortie = _executer_main(
        tmp_path, ocr,
        args=("--require-services",),
        TDOUCE_NER="0",
        TDOUCE_PYHELLEN_URL="http://127.0.0.1:9",
        TDOUCE_MODERNIZE_URL="http://127.0.0.1:9",
        TDOUCE_HEALTH_TIMEOUT="1",
    )

    assert res.returncode == 3
    assert "require-services" in res.stdout
    assert not sortie.exists(), "a file was written despite --require-services"


def test_a_broken_archive_is_not_forgiven_by_a_finished_corpus(tmp_path):
    """`--skip-existing` returning early skipped the summary, so a nightly
    wrapper would report success forever while one archive never
    converted."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    (ocr / "LIV0009.zip").write_bytes(b"not a zip")

    res, sortie = _executer_main(tmp_path, ocr, **MODE_COURT)
    assert res.returncode == 1

    # Everything convertible is converted; only the archive is left.
    again, _ = _executer_main(
        tmp_path, ocr, args=("--skip-existing",), **MODE_COURT
    )

    assert again.returncode == 1, again.stdout[-2000:]
    assert "LIV0009.zip" in again.stdout


def test_dry_run_counts_a_volume_that_is_still_archived(tmp_path):
    """Archives are a documented input layout and --dry-run unpacks
    nothing, so it reported "no documents found" and exited 3 exactly when
    the plan is most useful: before the first run."""
    import zipfile

    ocr = tmp_path / "ocr"
    ocr.mkdir()
    with zipfile.ZipFile(ocr / "LIV0055_reconciled.zip", "w") as archive:
        for page in sorted(ALTO_MIN.rglob("*.xml")):
            archive.write(page, page.name)

    res, sortie = _executer_main(tmp_path, ocr, args=("--dry-run",), **MODE_COURT)

    assert res.returncode == 0, res.stdout[-2000:]
    # The volume, not the archive: the plan merges archives and directories
    # and sorts them, so it names what the run would convert.
    assert "LIV0055_reconciled" in res.stdout
    assert "still archived" in res.stdout
    assert not (ocr / "LIV0055_reconciled").exists(), "--dry-run unpacked it"
    assert not sortie.exists()


def test_an_output_path_that_is_a_file_is_a_misconfiguration(tmp_path):
    """`-o` makes it as easy to name an existing file as `-i` does, and the
    input guard already refuses that."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    (tmp_path / "out").write_text("not a directory", encoding="utf-8")

    res, _ = _executer_main(tmp_path, ocr, **MODE_COURT)

    assert res.returncode == 3
    assert "--output" in res.stdout


def test_an_unusable_log_path_costs_the_log_not_the_conversion(tmp_path):
    """A log is a diagnostic, not the job."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    (tmp_path / "notadir").write_text("x", encoding="utf-8")

    res, sortie = _executer_main(
        tmp_path, ocr, args=("--log-file", str(tmp_path / "notadir" / "x.log")),
        **MODE_COURT,
    )

    assert res.returncode == 0, res.stdout[-2000:] + res.stderr[-2000:]
    assert (sortie / f"{DOCUMENT}.tei.xml").exists()
    assert "cannot write the run log" in res.stderr


def test_resuming_a_finished_corpus_is_a_success(tmp_path):
    """The idiom the user guide recommends for a nightly wrapper: once
    everything is converted, a rerun says so and exits 0."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)

    first, sortie = _executer_main(tmp_path, ocr, **MODE_COURT)
    assert first.returncode == 0

    again, _ = _executer_main(
        tmp_path, ocr, args=("--skip-existing",), **MODE_COURT
    )

    assert again.returncode == 0, again.stdout[-2000:]
    assert "Nothing to do" in again.stdout


def test_a_typo_is_refused_without_probing_the_services(tmp_path):
    """Selectors are validated from the directory listing, before the
    probes: a typo used to pay up to two health timeouts of blocking HTTP
    before being told it was a typo."""
    import time

    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)

    started = time.monotonic()
    res, _ = _executer_main(
        tmp_path, ocr, args=("LIV0O44",),
        # Unroutable, not merely closed: 127.0.0.1 refuses instantly, so
        # the test could not see the probes it exists to keep out of the
        # way. This address makes the probe block until it times out.
        TDOUCE_PYHELLEN_URL="http://10.255.255.1:9",
        TDOUCE_MODERNIZE_URL="http://10.255.255.1:9",
        TDOUCE_HEALTH_TIMEOUT="20",
        TDOUCE_NER="0",
    )
    elapsed = time.monotonic() - started

    assert res.returncode == 3
    assert "LIV0O44" in res.stdout
    assert elapsed < 10, f"probed before refusing a typo ({elapsed:.1f}s)"


def test_the_plan_names_the_volumes_the_run_would_convert(tmp_path):
    """A real run extracts first, so an archive enters the work list sorted
    and competes for the limit in name order. Appending the archives at the
    end of the plan made `--dry-run --limit 1` name one volume and the run
    convert another."""
    import zipfile

    ocr = tmp_path / "ocr"
    ocr.mkdir()
    shutil.copytree(ALTO_MIN / DOCUMENT, ocr / "LIV0002_reconciled")
    with zipfile.ZipFile(ocr / "LIV0001_reconciled.zip", "w") as archive:
        for page in sorted((ocr / "LIV0002_reconciled").rglob("*.xml")):
            archive.write(page, page.name)

    plan, _ = _executer_main(tmp_path, ocr, args=("--limit", "1", "--dry-run"),
                             **MODE_COURT)
    real, sortie = _executer_main(tmp_path, ocr, args=("--limit", "1"),
                                  **MODE_COURT)

    converted = [p.name.replace(".tei.xml", "") for p in sortie.glob("*.tei.xml")]
    assert converted == ["LIV0001_reconciled"], converted
    assert "LIV0001_reconciled" in plan.stdout
    assert "LIV0002_reconciled" not in plan.stdout


def test_a_refused_run_leaves_no_log_directory_behind(tmp_path):
    """Logging was configured first thing, so a run refused for a missing
    input still created the log's parent — the same rule the output
    directory follows."""
    res, _ = _executer_main(
        tmp_path, tmp_path / "absent",
        args=("--log-file", str(tmp_path / "logs" / "run.log")), **MODE_COURT,
    )

    assert res.returncode == 3
    assert not (tmp_path / "logs").exists()


def test_naming_one_volume_does_not_unpack_the_whole_corpus(tmp_path):
    """Extraction ran before selection, so converting one named volume
    unpacked every archive first — the same waste the input and output
    guards were tightened against."""
    import zipfile

    ocr = tmp_path / "ocr"
    ocr.mkdir()
    for name in ("LIV0044", "LIV0055", "LIV0066"):
        with zipfile.ZipFile(ocr / f"{name}_reconciled.zip", "w") as archive:
            for page in sorted(ALTO_MIN.rglob("*.xml")):
                archive.write(page, page.name)

    res, sortie = _executer_main(tmp_path, ocr, args=("LIV0044",), **MODE_COURT)

    assert res.returncode == 0, res.stdout[-2000:]
    unpacked = sorted(p.name for p in ocr.iterdir() if p.is_dir())
    assert unpacked == ["LIV0044_reconciled"], unpacked


def test_a_dry_run_over_a_finished_archive_corpus_is_a_success(tmp_path):
    """`OCR/` holding only ZIPs is the documented layout. With every output
    already written, the plan reported a misconfiguration and exited 3,
    while the real run of the same command exited 0 — so a wrapper that
    pre-flights with --dry-run read a completed corpus as broken."""
    import zipfile

    ocr = tmp_path / "ocr"
    ocr.mkdir()
    with zipfile.ZipFile(ocr / f"{DOCUMENT}.zip", "w") as archive:
        for page in sorted(ALTO_MIN.rglob("*.xml")):
            archive.write(page, page.name)

    first, sortie = _executer_main(tmp_path, ocr, **MODE_COURT)
    assert first.returncode == 0, first.stdout[-2000:]

    plan, _ = _executer_main(
        tmp_path, ocr, args=("--skip-existing", "--dry-run"), **MODE_COURT
    )

    assert plan.returncode == 0, plan.stdout[-2000:]
    assert "Nothing to do" in plan.stdout


def test_require_services_leaves_no_directory_behind(tmp_path):
    """"nothing written" has to include the log directory and the output
    directory, both of which were created before the refusal."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)

    res, sortie = _executer_main(
        tmp_path, ocr,
        args=("--require-services", "--no-ner",
              "--log-file", str(tmp_path / "logs" / "r.log")),
        TDOUCE_PYHELLEN_URL="http://127.0.0.1:1",
        TDOUCE_MODERNIZE_URL="http://127.0.0.1:1",
        TDOUCE_HEALTH_TIMEOUT="2",
    )

    assert res.returncode == 3
    assert not (tmp_path / "logs").exists(), "a log directory survived the refusal"
    assert not sortie.exists(), "an output directory survived the refusal"


def test_the_summary_points_at_a_log_that_holds_the_failure(tmp_path):
    """Logging was configured after the records emitted during setup, so an
    archive failure reached the console and never the file the summary
    names."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    (ocr / "BROKEN.zip").write_bytes(b"not a zip")

    res, _ = _executer_main(
        tmp_path, ocr,
        args=("--log-file", str(tmp_path / "run.log")), **MODE_COURT,
    )

    assert res.returncode == 1
    written = sorted(tmp_path.glob("run_*.log"))
    assert written, "no run log was written"
    assert "BROKEN.zip" in written[0].read_text(encoding="utf-8")


def test_quiet_suppresses_the_lines_printed_during_setup(tmp_path):
    """`_QUIET` was set inside configure_logging, which ran after them, and
    was never set at all under --dry-run."""
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    shutil.make_archive(str(ocr / "LIV0001_reconciled"), "zip",
                        ALTO_MIN / DOCUMENT)

    res, _ = _executer_main(tmp_path, ocr, args=("-q",), **MODE_COURT)
    assert "Extracting" not in res.stdout, res.stdout

    plan, _ = _executer_main(tmp_path, ocr, args=("-q", "--dry-run"), **MODE_COURT)
    assert "Loaded" not in plan.stdout, plan.stdout


def _archive(ocr, name):
    import zipfile

    with zipfile.ZipFile(ocr / f"{name}.zip", "w") as archive:
        for page in sorted(ALTO_MIN.rglob("*.xml")):
            archive.write(page, page.name)


def test_a_resume_does_not_unpack_what_it_is_about_to_skip(tmp_path):
    """Unlike --limit, the resume predicate is name-based and knowable
    without unpacking: a resumed corpus extracted every archive and only
    then skipped it."""
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    _archive(ocr, "LIV0009_reconciled")
    sortie = tmp_path / "out"
    sortie.mkdir()
    (sortie / "LIV0009_reconciled.tei.xml").write_text("<x/>", encoding="utf-8")

    res, _ = _executer_main(tmp_path, ocr, args=("--skip-existing",), **MODE_COURT)

    assert res.returncode == 0, res.stdout[-2000:]
    assert not (ocr / "LIV0009_reconciled").exists(), "unpacked what it skipped"


def test_a_plan_over_a_finished_archive_corpus_that_was_never_unpacked(tmp_path):
    """The earlier test let a real run leave the extracted directory
    behind, so it never saw the archives-only case: the plan dropped them
    as converted and then reported a misconfiguration."""
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    _archive(ocr, "LIV0009_reconciled")
    sortie = tmp_path / "out"
    sortie.mkdir()
    (sortie / "LIV0009_reconciled.tei.xml").write_text("<x/>", encoding="utf-8")

    plan, _ = _executer_main(
        tmp_path, ocr, args=("--skip-existing", "--dry-run"), **MODE_COURT
    )

    assert plan.returncode == 0, plan.stdout[-2000:]
    assert "Nothing to do" in plan.stdout


def test_the_skip_count_ignores_volumes_no_selector_named(tmp_path):
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    _archive(ocr, "LIV0009_reconciled")
    _archive(ocr, "LIV0100_reconciled")
    sortie = tmp_path / "out"
    sortie.mkdir()
    (sortie / "LIV0009_reconciled.tei.xml").write_text("<x/>", encoding="utf-8")

    plan, _ = _executer_main(
        tmp_path, ocr, args=("LIV0100", "--skip-existing", "--dry-run"),
        **MODE_COURT,
    )

    assert "already converted" not in plan.stdout, plan.stdout


def test_the_skip_count_is_one_per_volume_not_one_per_trace(tmp_path):
    """A volume with both an archive and an extracted directory was counted
    where it was refused for unpacking AND where selection skipped it, so
    one volume reported as two."""
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    _archive(ocr, "A_reconciled")
    _archive(ocr, "B_reconciled")
    sortie = tmp_path / "out"
    sortie.mkdir()
    (sortie / "A_reconciled.tei.xml").write_text("<x/>", encoding="utf-8")

    res, _ = _executer_main(tmp_path, ocr, args=("--skip-existing",), **MODE_COURT)

    assert res.returncode == 0, res.stdout[-2000:]
    assert "1 document(s) already converted" in res.stdout, res.stdout
    assert "2 document(s)" not in res.stdout, res.stdout


def test_excluding_one_volume_of_several_works(tmp_path):
    """The documented example. Gating the directory scan on a predicate
    that applied exclusions dropped the volume before select_documents
    could tell a matched `-x` from a mistyped one, so every working `-x`
    reported "No volume matches" and exited 3."""
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    for name in ("LIV0031_reconciled", "LIV0038_reconciled"):
        shutil.copytree(ALTO_MIN / DOCUMENT, ocr / name)

    res, sortie = _executer_main(
        tmp_path, ocr, args=("LIV003*", "-x", "LIV0038"), **MODE_COURT
    )

    assert res.returncode == 0, res.stdout[-2000:]
    produced = sorted(p.name for p in sortie.glob("*.tei.xml"))
    assert produced == ["LIV0031_reconciled.tei.xml"], produced


def test_naming_an_archived_volume_the_resume_will_skip(tmp_path):
    """A volume the resume skips is a name the selector legitimately found;
    without it, naming an already-converted, still-archived volume was
    reported as a typo."""
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    _archive(ocr, "LIV0044_reconciled")
    sortie = tmp_path / "out"
    sortie.mkdir()
    (sortie / "LIV0044_reconciled.tei.xml").write_text("<x/>", encoding="utf-8")

    res, _ = _executer_main(
        tmp_path, ocr, args=("LIV0044_reconciled", "--skip-existing"),
        **MODE_COURT,
    )

    assert res.returncode == 0, res.stdout[-2000:]
    assert "Nothing to do" in res.stdout


@pytest.mark.parametrize("args", [
    ("-x", "LIV0038_reconciled"),                       # an extracted directory
    ("-x", "LIV0099_reconciled"),                       # a volume still archived
    ("LIV0001_reconciled", "-x", "LIV0038_reconciled"),  # outside the selector
])
def test_every_shape_of_exclusion_works(tmp_path, args):
    """The first fix covered one shape of three. An excluded archive is
    never unpacked and a non-selected directory is never scanned, so
    judging the pattern after that narrowing answered "no match" for
    patterns that matched."""
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    for name in ("LIV0001_reconciled", "LIV0038_reconciled"):
        shutil.copytree(ALTO_MIN / DOCUMENT, ocr / name)
    _archive(ocr, "LIV0099_reconciled")

    res, _ = _executer_main(tmp_path, ocr, args=args, **MODE_COURT)

    assert res.returncode == 0, res.stdout[-2000:]


def test_a_mistyped_exclusion_is_still_refused(tmp_path):
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)

    res, _ = _executer_main(tmp_path, ocr, args=("-x", "NEXISTEPAS"), **MODE_COURT)

    assert res.returncode == 3
    assert "NEXISTEPAS" in res.stdout


def test_an_entities_directory_over_the_corpus_is_refused(tmp_path):
    """A per-document failure removes that document's entity directory.
    `NER_OUTPUT_DIR` was a hardcoded constant, so the blast radius was
    bounded; making it a setting let `--entities OCR` plus any failure
    delete the ALTO volume the run had just read."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)

    res, _ = _executer_main(
        tmp_path, ocr, args=("--entities", str(ocr)), **MODE_COURT
    )

    assert res.returncode == 3
    assert "must not overlap" in res.stdout
    assert (ocr / DOCUMENT).is_dir(), "the source volume was destroyed"


def test_a_failure_removes_only_the_entity_directory_it_created(tmp_path):
    """The cleanup was an unbounded rmtree of a user-named path."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    entities = tmp_path / "ents" / DOCUMENT
    entities.mkdir(parents=True)
    (entities / "keep.txt").write_text("precious", encoding="utf-8")

    sortie = tmp_path / "out"
    sortie.mkdir()
    sortie.chmod(0o500)
    try:
        _executer_main(tmp_path, ocr, args=("--entities", str(tmp_path / "ents")),
                       **MODE_COURT)
    finally:
        sortie.chmod(0o700)

    assert (entities / "keep.txt").exists(), "pre-existing content was removed"


def test_excluding_everything_in_an_archive_corpus_says_so(tmp_path):
    """An excluded archive is never unpacked, so the emptiness guard fired
    before the exclusion branch and blamed a missing corpus."""
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    _archive(ocr, "LIV0001_reconciled")

    res, _ = _executer_main(
        tmp_path, ocr, args=("LIV0001", "-x", "LIV0001"), **MODE_COURT
    )

    assert res.returncode == 3
    assert "Every volume was excluded" in res.stdout, res.stdout


def test_the_config_file_is_named_in_a_real_run(tmp_path):
    """It was printed only under --dry-run, so a teille-douce.toml found by
    walking up could redirect paths.output with nothing naming it."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    (tmp_path / "teille-douce.toml").write_text(
        "[limits]\njobs = 2\n", encoding="utf-8"
    )

    res, _ = _executer_main(tmp_path, ocr, **MODE_COURT)

    assert res.returncode == 0, res.stdout[-2000:]
    # Rich wraps a long path, so the name can straddle a newline.
    assert "teille-douce.toml" in res.stdout.replace("\n", ""), res.stdout


def test_the_default_entity_directory_never_blocks_a_run(tmp_path):
    """The overlap guard fired on invocations that never passed --entities:
    `run -i .` refused, naming a flag the user had not typed."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)

    res, _ = _executer_main(tmp_path, tmp_path / "ocr", **MODE_COURT)

    assert res.returncode == 0, res.stdout[-2000:]
