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


def _fail_the_run(sortie):
    """Make the writing step fail, so a document reaches the cleanup."""
    sortie.mkdir(exist_ok=True)
    sortie.chmod(0o500)


def test_a_failure_touches_no_entity_file_when_ner_did_not_run(tmp_path):
    """With the phase off nothing was written there, so there is nothing of
    this run's to remove — and removing anything would be removing someone
    else's files. (The removal path itself needs NER models; it is
    exercised by the e2e_full mode.)"""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    entities = tmp_path / "ents" / DOCUMENT
    entities.mkdir(parents=True)
    (entities / "from-a-previous-run.csv").write_text("x", encoding="utf-8")

    sortie = tmp_path / "out"
    _fail_the_run(sortie)
    try:
        res, _ = _executer_main(
            tmp_path, ocr, args=("--entities", str(tmp_path / "ents")),
            **MODE_COURT,
        )
    finally:
        sortie.chmod(0o700)

    assert res.returncode == 1
    assert (entities / "from-a-previous-run.csv").exists(), (
        "a failure with NER off removed files it never wrote"
    )


def test_an_exclusion_that_matches_nothing_stops_the_run(tmp_path):
    """Documented: an exclusion that silently misses converts at full cost
    the volume it was meant to hold back."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)

    res, _ = _executer_main(tmp_path, ocr, args=("-x", "NOPE"), **MODE_COURT)

    assert res.returncode == 3
    assert "NOPE" in res.stdout


def test_an_empty_selected_volume_names_itself(tmp_path):
    """The message named the whole input directory, sending the operator to
    inspect a corpus that was perfectly healthy."""
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    (ocr / "FOO").mkdir()
    shutil.copytree(ALTO_MIN / DOCUMENT, ocr / "BAR")

    res, _ = _executer_main(tmp_path, ocr, args=("FOO",), **MODE_COURT)

    assert res.returncode == 3
    assert "FOO" in res.stdout
    assert "No ALTO documents found in" not in res.stdout


def test_no_probe_and_require_services_contradict_each_other(tmp_path):
    """One says "assume the services answer", the other "prove they do".
    Refused before expand_archives unpacks anything, because
    --require-services promises to fail before a single write."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)

    res, sortie = _executer_main(
        tmp_path, ocr, args=("--no-probe", "--require-services"), **MODE_COURT)

    assert res.returncode == 3
    assert "--no-probe" in res.stdout and "--require-services" in res.stdout
    assert not sortie.exists()


def _archive_without_alto(ocr, name="LIV9004_reconciled"):
    """An archive whose volume directory carries no *.xml -- the ALTO
    subfolder left out at packing time, which is how this arrives in
    practice."""
    with zipfile.ZipFile(ocr / f"{name}.zip", "w") as zf:
        zf.writestr(f"{name}/readme.txt", "packed without the ALTO folder")
    return name


def test_a_volume_that_holds_no_alto_is_named_not_dropped(tmp_path):
    """It fell out of the document list with no counter, no log line and
    no summary mention: absent from both sides of the fraction, so a run
    that converted one of two volumes said "1/1 documents converted" and
    exited 0. A volume whose ALTO subfolder was left out of its archive
    disappeared from a two-hundred-volume run without a word."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    name = _archive_without_alto(ocr)

    res, _ = _executer_main(tmp_path, ocr, **MODE_COURT)

    assert res.returncode == 0
    assert name in res.stdout
    assert "no ALTO" in res.stdout
    assert "held no ALTO" in res.stdout


def test_a_plain_directory_with_no_alto_is_named_like_an_archived_one(tmp_path):
    """The documented input layout is directories, not archives, and that
    is the shape this missed: `expand_archives` required an *.xml before
    letting an existing directory through, while the archive loop added
    its target whatever the archive held. The same empty volume was
    therefore reported when it arrived as a zip and invisible the day the
    operator deleted that zip."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    (ocr / "LIV9004_reconciled").mkdir()
    (ocr / "LIV9004_reconciled" / "notes.txt").write_text("no ALTO here",
                                                          encoding="utf-8")

    res, _ = _executer_main(tmp_path, ocr, **MODE_COURT)

    assert res.returncode == 0
    assert "no ALTO file, nothing to convert: LIV9004_reconciled" in res.stdout
    assert "held no ALTO" in res.stdout


def test_a_hidden_directory_is_not_a_volume_that_lost_its_alto(tmp_path):
    """`.git`, `__MACOSX` and the `.extracting` residue of an interrupted
    run are not volumes, and naming them as losses would train the
    operator to ignore the line."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    for noise in (".git", "__MACOSX", "LIV9005_reconciled.extracting"):
        (ocr / noise).mkdir()

    res, _ = _executer_main(tmp_path, ocr, **MODE_COURT)

    assert res.returncode == 0
    assert "held no ALTO" not in res.stdout


def test_a_corpus_of_only_alto_less_volumes_says_which_ones(tmp_path):
    """Exit 3 is right — nothing there could be converted — but the
    message named the directory and left the operator to work out which
    of two hundred volumes was the empty one."""
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    name = _archive_without_alto(ocr)

    res, sortie = _executer_main(tmp_path, ocr, **MODE_COURT)

    assert res.returncode == 3
    assert f"no ALTO file, nothing to convert: {name}" in res.stdout
    assert not sortie.exists()


def test_a_volume_with_no_alto_that_was_not_selected_stays_quiet(tmp_path):
    """Reporting every unselected volume of a large corpus would bury the
    one the operator asked about."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    name = _archive_without_alto(ocr)

    res, _ = _executer_main(tmp_path, ocr, args=(DOCUMENT,), **MODE_COURT)

    assert res.returncode == 0
    assert "held no ALTO" not in res.stdout
    assert name not in res.stdout


def test_resuming_does_not_claim_a_volume_with_no_alto_was_converted(tmp_path):
    """"every document already has a TEI output" printed on the line under
    the warning naming a volume that has none, and never will. The resume
    path returns before the summary, so the suffix added there did not
    reach it."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    (ocr / "LIV9004_reconciled").mkdir()
    (ocr / "LIV9004_reconciled" / "notes.txt").write_text("no ALTO",
                                                          encoding="utf-8")

    first, _ = _executer_main(tmp_path, ocr, args=("--skip-existing",),
                              **MODE_COURT)
    again, _ = _executer_main(tmp_path, ocr, args=("--skip-existing",),
                              **MODE_COURT)

    assert (first.returncode, again.returncode) == (0, 0)
    assert "Nothing to do" in again.stdout
    assert "every document already has a TEI output" not in again.stdout
    assert "held no ALTO" in again.stdout


def test_a_volume_is_counted_once_however_many_traces_it_left(tmp_path):
    """An ALTO-less directory with its archive still beside it and a TEI
    output already on disk hits three counters at once. It used to land in
    two of them: `_worth_unpacking` skipped it as already converted AND the
    directory pass held it as having no ALTO, so one volume appeared twice
    in one summary line. The condition that decided this was written when
    the directory pass still dropped ALTO-less directories."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    empty = ocr / "LIV9004_reconciled"
    empty.mkdir()
    (empty / "notes.txt").write_text("no ALTO", encoding="utf-8")
    with zipfile.ZipFile(ocr / "LIV9004_reconciled.zip", "w") as zf:
        zf.writestr("LIV9004_reconciled/c/f1.xml", "<alto/>")
    out = tmp_path / "out"
    out.mkdir()
    (out / "LIV9004_reconciled.tei.xml").write_text("<TEI/>", encoding="utf-8")

    res, _ = _executer_main(tmp_path, ocr, args=("--skip-existing",),
                            **MODE_COURT)

    assert res.returncode == 0
    assert "held no ALTO" in res.stdout
    assert "already converted" not in res.stdout, (
        "the same volume was counted as a skip and as holding no ALTO"
    )


def test_a_directory_that_cannot_be_read_is_a_failure_not_an_empty_volume(
        tmp_path):
    """`rglob` answers "no ALTO" for a directory it may not open, which is
    a different diagnosis and sends the operator to repack a volume whose
    only problem is its mode. And it is the corrupt-archive case — the
    input was there and could not be read — so it answers with the same
    exit code rather than telling a wrapper to fix its configuration."""
    import os
    import stat

    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    shut = ocr / "LIV9007_reconciled"
    shutil.copytree(ocr / DOCUMENT, shut)
    os.chmod(shut, 0o000)
    try:
        if os.access(shut, os.R_OK):
            pytest.skip("running as a user that ignores file permissions")
        res, _ = _executer_main(tmp_path, ocr, **MODE_COURT)
    finally:
        os.chmod(shut, stat.S_IRWXU)

    assert res.returncode == 1
    assert "LIV9007_reconciled: directory could not be read" in res.stdout
    assert "held no ALTO" not in res.stdout
    assert "1/2 documents converted" in res.stdout


def test_every_volume_offered_lands_in_exactly_one_bucket(tmp_path):
    """The invariant behind every counting bug this run has had, stated
    once instead of one scenario at a time.

    A volume can be converted, already converted and skipped, holding no
    ALTO, unreadable, or a failure — and it must be exactly one of those.
    The denominator of the fraction covers what was attempted; the skips
    and the ALTO-less are the notes beside it. Under-counting hid a
    dropped volume; over-counting announced two documents where the corpus
    held one."""
    import re

    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)                       # LIV9001: converted
    shutil.copytree(ocr / DOCUMENT, ocr / "LIV9002_reconciled")
    (ocr / "LIV9003_reconciled").mkdir()                 # no ALTO
    (ocr / "LIV9003_reconciled" / "n.txt").write_text("x", encoding="utf-8")
    # Its archive still beside it AND a TEI output already there: three
    # counters reach for this one volume at once, which is how the double
    # count happened.
    with zipfile.ZipFile(ocr / "LIV9003_reconciled.zip", "w") as zf:
        zf.writestr("LIV9003_reconciled/c/f1.xml", "<alto/>")
    (ocr / "LIV9005_reconciled.zip").write_bytes(b"not a zip")

    out = tmp_path / "out"                               # LIV9002: skipped
    out.mkdir()
    (out / "LIV9002_reconciled.tei.xml").write_text("<TEI/>", encoding="utf-8")
    (out / "LIV9003_reconciled.tei.xml").write_text("<TEI/>", encoding="utf-8")

    # A wide console on purpose: Rich wraps the summary at 80 columns and
    # the notes this test reads then land on the following line.
    res, _ = _executer_main(tmp_path, ocr, args=("--skip-existing",),
                            COLUMNS="200", **MODE_COURT)

    line = next(l for l in res.stdout.splitlines()
                if "documents converted" in l)
    converted, attempted = (int(n) for n in
                            re.search(r"(\d+)/(\d+) documents", line).groups())

    def note(pattern):
        found = re.search(pattern, line)
        return int(found.group(1)) if found else 0

    skipped = note(r"\((\d+) more skipped")
    no_alto = note(r"\((\d+) more held no ALTO")
    failed = len([l for l in res.stdout.splitlines() if "FAILED" in l])

    # An archive and its extracted directory are ONE volume.
    offered = len({e.name if e.is_dir() else e.stem for e in ocr.iterdir()
                   if e.is_dir() or e.suffix == ".zip"})

    assert (converted, attempted, skipped, no_alto, failed) == (1, 2, 1, 1, 1)
    assert converted + failed == attempted
    assert attempted + skipped + no_alto == offered, (
        f"{offered} volumes offered, {attempted + skipped + no_alto} "
        f"accounted for, in: {line}"
    )


def _unreadable(path):
    """chmod 000, or tell the caller this user ignores that."""
    import os

    os.chmod(path, 0o000)
    return not os.access(path, os.R_OK)


def test_an_unreadable_volume_does_not_vanish_behind_a_finished_resume(
        tmp_path):
    """The early "nothing left to convert" return tested `failed_archives`
    and not the unreadable volumes, so a resumed corpus announced success
    and exited 0 with one volume it had never been able to open. The same
    corpus with a corrupt archive in that position exited 1."""
    import stat

    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    shut = ocr / "LIV9002_reconciled"
    shutil.copytree(ocr / DOCUMENT, shut)
    out = tmp_path / "out"
    out.mkdir()
    (out / f"{DOCUMENT}.tei.xml").write_text("<TEI/>", encoding="utf-8")

    if not _unreadable(shut):
        __import__("os").chmod(shut, stat.S_IRWXU)
        pytest.skip("running as a user that ignores file permissions")
    try:
        res, _ = _executer_main(tmp_path, ocr, args=("--skip-existing",),
                                COLUMNS="200", **MODE_COURT)
    finally:
        __import__("os").chmod(shut, stat.S_IRWXU)

    assert res.returncode == 1
    assert "0/1 documents converted (1 more skipped, already converted)" \
        in res.stdout
    assert "LIV9002_reconciled: directory could not be read" in res.stdout


def test_the_early_failure_summary_names_every_volume_it_could_not_open(
        tmp_path):
    """It counted and listed `failed_archives` alone while the final
    summary had been taught the combined list, so a corrupt archive was
    named and an unreadable directory beside it was not."""
    import stat

    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    (ocr / "LIV9002_reconciled.zip").write_bytes(b"not a zip")
    shut = ocr / "LIV9003_reconciled"
    shutil.copytree(ocr / DOCUMENT, shut)
    out = tmp_path / "out"
    out.mkdir()
    (out / f"{DOCUMENT}.tei.xml").write_text("<TEI/>", encoding="utf-8")

    if not _unreadable(shut):
        __import__("os").chmod(shut, stat.S_IRWXU)
        pytest.skip("running as a user that ignores file permissions")
    try:
        res, _ = _executer_main(tmp_path, ocr, args=("--skip-existing",),
                                COLUMNS="200", **MODE_COURT)
    finally:
        __import__("os").chmod(shut, stat.S_IRWXU)

    assert res.returncode == 1
    assert "0/2 documents converted" in res.stdout
    assert "LIV9002_reconciled.zip: File is not a zip file" in res.stdout
    assert "LIV9003_reconciled: directory could not be read" in res.stdout


def test_a_volume_that_could_not_be_opened_does_not_hide_the_ones_never_tried(
        tmp_path):
    """`never_tried` subtracted only the archives from the failures, so
    every unreadable volume — which is not one of `docs` either — was
    taken off a set it had never been in. Two volumes a --fail-fast never
    reached were reported nowhere."""
    import stat

    ocr = tmp_path / "ocr"
    ocr.mkdir()
    for name in ("LIV9001_reconciled", "LIV9002_reconciled"):
        (ocr / name).mkdir()
        (ocr / name / "f1.xml").write_text("not ALTO at all", encoding="utf-8")
    shutil.copytree(ALTO_MIN / DOCUMENT, ocr / "LIV9003_reconciled")
    shut = [ocr / "LIV9004_reconciled", ocr / "LIV9005_reconciled"]
    for directory in shut:
        shutil.copytree(ALTO_MIN / DOCUMENT, directory)

    if not all(_unreadable(d) for d in shut):
        for directory in shut:
            __import__("os").chmod(directory, stat.S_IRWXU)
        pytest.skip("running as a user that ignores file permissions")
    try:
        res, _ = _executer_main(tmp_path, ocr, args=("--fail-fast",),
                                COLUMNS="200", **MODE_COURT)
    finally:
        for directory in shut:
            __import__("os").chmod(directory, stat.S_IRWXU)

    assert res.returncode == 1
    assert "0/5 documents converted (2 never attempted, the run stopped early)" \
        in res.stdout


def test_resume_does_not_reopen_a_volume_it_has_nothing_left_to_do_to(tmp_path):
    """Its mode stops mattering once its TEI is on disk, which is what
    --skip-existing means and what a corrupt archive in the same position
    already got: `_worth_unpacking` returns before ever touching it.
    Failing here turned a nightly wrapper permanently red over one
    badly-moded volume there was nothing left to convert."""
    import stat

    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    shut = ocr / "LIV9002_reconciled"
    shutil.copytree(ocr / DOCUMENT, shut)
    out = tmp_path / "out"
    out.mkdir()
    (out / "LIV9002_reconciled.tei.xml").write_text("<TEI/>", encoding="utf-8")

    if not _unreadable(shut):
        __import__("os").chmod(shut, stat.S_IRWXU)
        pytest.skip("running as a user that ignores file permissions")
    try:
        resumed, _ = _executer_main(tmp_path, ocr, args=("--skip-existing",),
                                    COLUMNS="200", **MODE_COURT)
        # And without --skip-existing it is still the failure it is.
        plain, _ = _executer_main(tmp_path, ocr, COLUMNS="200", **MODE_COURT)
    finally:
        __import__("os").chmod(shut, stat.S_IRWXU)

    assert resumed.returncode == 0
    assert "1/1 documents converted (1 more skipped, already converted)" \
        in resumed.stdout
    assert "could not be read" not in resumed.stdout

    assert plain.returncode == 1
    assert "LIV9002_reconciled: directory could not be read" in plain.stdout


def test_a_corpus_kept_alive_only_by_an_unread_skip_is_not_called_empty(
        tmp_path):
    """The fifth bucket, forgotten the same way the fourth was one commit
    earlier. A volume skipped without being read is still a volume, and
    the "is there anything here" guard did not know about it: the corpus
    answered "No ALTO documents found" and exited 3 — the code that tells
    a wrapper to go and fix its own configuration — for a corpus that had
    nothing left to convert. The readable and archive-only shapes of the
    same volume both answered 0."""
    import stat

    ocr = tmp_path / "ocr"
    ocr.mkdir()
    shut = ocr / "LIV9002_reconciled"
    shutil.copytree(ALTO_MIN / DOCUMENT, shut)
    out = tmp_path / "out"
    out.mkdir()
    (out / "LIV9002_reconciled.tei.xml").write_text("<TEI/>", encoding="utf-8")

    if not _unreadable(shut):
        __import__("os").chmod(shut, stat.S_IRWXU)
        pytest.skip("running as a user that ignores file permissions")
    try:
        res, _ = _executer_main(tmp_path, ocr, args=("--skip-existing",),
                                COLUMNS="200", **MODE_COURT)
    finally:
        __import__("os").chmod(shut, stat.S_IRWXU)

    assert res.returncode == 0
    assert "Nothing to do: 1 document(s) already converted" in res.stdout


def test_an_unread_skip_is_still_counted_when_something_else_failed(tmp_path):
    """Same guard, the other way out of it: with a corrupt archive beside
    it the run reported the archive and the skipped volume appeared in no
    note at all."""
    import stat

    ocr = tmp_path / "ocr"
    ocr.mkdir()
    (ocr / "LIV9005_reconciled.zip").write_bytes(b"not a zip")
    shut = ocr / "LIV9002_reconciled"
    shutil.copytree(ALTO_MIN / DOCUMENT, shut)
    out = tmp_path / "out"
    out.mkdir()
    (out / "LIV9002_reconciled.tei.xml").write_text("<TEI/>", encoding="utf-8")

    if not _unreadable(shut):
        __import__("os").chmod(shut, stat.S_IRWXU)
        pytest.skip("running as a user that ignores file permissions")
    try:
        res, _ = _executer_main(tmp_path, ocr, args=("--skip-existing",),
                                COLUMNS="200", **MODE_COURT)
    finally:
        __import__("os").chmod(shut, stat.S_IRWXU)

    assert res.returncode == 1
    assert "0/1 documents converted (1 more skipped, already converted)" \
        in res.stdout


def test_the_refusal_to_read_anything_still_names_what_held_no_alto(tmp_path):
    """Both "Completed with errors" summaries carry the ALTO-less note;
    this branch did not, so its line did not balance against the corpus."""
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    (ocr / "LIV9005_reconciled.zip").write_bytes(b"not a zip")
    (ocr / "LIV9003_reconciled").mkdir()
    (ocr / "LIV9003_reconciled" / "n.txt").write_text("x", encoding="utf-8")

    res, _ = _executer_main(tmp_path, ocr, COLUMNS="200", **MODE_COURT)

    assert res.returncode == 1
    assert "1 volume(s) could not be opened. (1 more held no ALTO)" \
        in res.stdout


# =============================================================================
# The accounting, on every path out of a run
# =============================================================================

def _accounting(stdout):
    """Every number the run claims about volumes, whichever line it used.

    A run leaves by one of four doors — the full summary, the early
    failure summary, the finished-resume return, the nothing-readable
    refusal — and each writes its own sentence. Three of the four have
    already been caught telling a different story from the others.
    """
    import re

    def one(pattern):
        found = re.search(pattern, stdout)
        return int(found.group(1)) if found else 0

    fraction = re.search(r"(\d+)/(\d+) documents converted", stdout)
    return {
        "converted": int(fraction.group(1)) if fraction else 0,
        "attempted": (int(fraction.group(2)) if fraction else
                      one(r"(\d+) volume\(s\) could not be opened")),
        "skipped": (one(r"\((\d+) more skipped, already converted\)")
                    or one(r"Nothing to do: (\d+) document\(s\)")),
        "no_alto": one(r"\((\d+) more held no ALTO\)"),
        "never_tried": one(r"\((\d+) never attempted"),
        "failed": len([l for l in stdout.splitlines() if "FAILED" in l]),
    }


def _volumes_offered(ocr):
    """An archive and the directory it was extracted into are ONE volume."""
    return len({entry.name if entry.is_dir() else entry.stem
                for entry in ocr.iterdir()
                if entry.is_dir() or entry.suffix == ".zip"})


def _corpus_full_summary(ocr, out):
    shutil.copytree(ALTO_MIN / DOCUMENT, ocr / "LIV9001_reconciled")
    (ocr / "LIV9003_reconciled").mkdir()
    (ocr / "LIV9003_reconciled" / "n.txt").write_text("x", encoding="utf-8")
    (ocr / "LIV9005_reconciled.zip").write_bytes(b"not a zip")
    return ("--skip-existing",), 1


def _corpus_early_failure(ocr, out):
    """No convertible document left, but something failed: the early
    "Completed with errors" door."""
    shutil.copytree(ALTO_MIN / DOCUMENT, ocr / "LIV9001_reconciled")
    (out / "LIV9001_reconciled.tei.xml").write_text("<TEI/>", encoding="utf-8")
    (ocr / "LIV9005_reconciled.zip").write_bytes(b"not a zip")
    # One of each note, so a door that forgets one is caught here.
    (ocr / "LIV9003_reconciled").mkdir()
    (ocr / "LIV9003_reconciled" / "n.txt").write_text("x", encoding="utf-8")
    return ("--skip-existing",), 1


def _corpus_finished_resume(ocr, out):
    """Everything already converted: the "Nothing to do" door."""
    shutil.copytree(ALTO_MIN / DOCUMENT, ocr / "LIV9001_reconciled")
    (out / "LIV9001_reconciled.tei.xml").write_text("<TEI/>", encoding="utf-8")
    (ocr / "LIV9003_reconciled").mkdir()
    (ocr / "LIV9003_reconciled" / "n.txt").write_text("x", encoding="utf-8")
    return ("--skip-existing",), 0


def _corpus_nothing_readable(ocr, out):
    """Nothing could be opened at all: the refusal door."""
    (ocr / "LIV9005_reconciled.zip").write_bytes(b"not a zip")
    (ocr / "LIV9003_reconciled").mkdir()
    (ocr / "LIV9003_reconciled" / "n.txt").write_text("x", encoding="utf-8")
    return (), 1


@pytest.mark.parametrize("build", [
    _corpus_full_summary,
    _corpus_early_failure,
    _corpus_finished_resume,
    _corpus_nothing_readable,
], ids=["full-summary", "early-failure", "finished-resume", "nothing-readable"])
def test_the_accounting_balances_on_every_way_out_of_a_run(tmp_path, build):
    """One volume, one bucket — whichever door the run leaves by.

    Stated per door because that is where it kept breaking: a new bucket
    would be taught to the final summary and forgotten by one of the three
    early returns, and the volume then appeared twice, or in no line at
    all while the run exited 0 or, worse, 3."""
    ocr = tmp_path / "ocr"
    ocr.mkdir()
    out = tmp_path / "out"
    out.mkdir()
    args, expected_code = build(ocr, out)

    res, _ = _executer_main(tmp_path, ocr, args=args, COLUMNS="200",
                            **MODE_COURT)

    assert res.returncode == expected_code, res.stdout
    seen = _accounting(res.stdout)
    assert seen["converted"] + seen["failed"] + seen["never_tried"] \
        == seen["attempted"], f"{seen} in:\n{res.stdout}"
    assert seen["attempted"] + seen["skipped"] + seen["no_alto"] \
        == _volumes_offered(ocr), f"{seen} in:\n{res.stdout}"


# =============================================================================
# What a real run leaves on the screen
# =============================================================================

def test_a_run_ends_with_the_summary_it_promised(tmp_path):
    """The summary is the thing a reader is left with, and it is rendered
    from the record — so if it is not printed, nothing else in the report
    module is reaching a user."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)

    res, _ = _executer_main(tmp_path, ocr, COLUMNS="92", TDOUCE_UI="plain",
                            **MODE_COURT)

    assert res.returncode == 0
    assert "the source was defective" in res.stdout
    assert "withheld on purpose" in res.stdout
    assert "lost to an incident" in res.stdout
    assert "to a document only" in res.stdout
    assert "exit 0 — 1 of 1 volumes converted" in res.stdout


def test_the_journal_mode_draws_no_panel(tmp_path):
    """A live panel redirected to a file is forty thousand half-drawn
    frames."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)

    res, _ = _executer_main(tmp_path, ocr, COLUMNS="92", TDOUCE_UI="plain",
                            **MODE_COURT)

    assert "ctrl-c" not in res.stdout


def test_a_repaired_source_defect_reaches_the_summary(tmp_path):
    """The fixture volume carries duplicate ALTO ids — the loudest number
    in this corpus. It belongs in block 1, marked repaired, or the
    biggest figure in the summary reads as an alarm."""
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)

    res, _ = _executer_main(tmp_path, ocr, COLUMNS="92", TDOUCE_UI="plain",
                            **MODE_COURT)

    assert "ALTO ids repaired" in res.stdout
