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
