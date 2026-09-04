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
    assert "LIV0055_reconciled.zip" in res.stdout
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
    assert "output directory" in res.stdout


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
