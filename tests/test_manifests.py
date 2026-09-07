# Run: venv/bin/python tests/test_manifests.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from teille_douce.metadata.csv_book import select_manifest

M1 = "https://gallica.bnf.fr/iiif/ark:/12148/bpt6k8720514m/manifest.json"
M2 = "https://gallica.bnf.fr/iiif/ark:/12148/bpt6k8720519p/manifest.json"


def test_single():
    assert select_manifest([M1], None) == M1
    assert select_manifest([M1], "a") == M1


def test_volume_letter():
    assert select_manifest([M1, M2], "a") == M1
    assert select_manifest([M1, M2], "b") == M2


def test_volume_numbered():
    assert select_manifest([M1, M2], "t1") == M1
    assert select_manifest([M1, M2], "t2") == M2
    assert select_manifest([M1, M2], "v2") == M2


def test_out_of_range_or_unknown():
    # volume inconnu ou hors limites -> None (on n'invente pas)
    assert select_manifest([M1, M2], None) is None
    assert select_manifest([M1, M2], "t3") is None
    assert select_manifest([], "a") is None


if __name__ == "__main__":
    test_single()
    test_volume_letter()
    test_volume_numbered()
    test_out_of_range_or_unknown()
    print("OK test_manifests")


def test_a_candidate_that_is_not_a_regular_file_is_skipped(tmp_path):
    """A FIFO named like a mapping reports zero bytes, so it passed the
    size cap and `read_csv` then waited for a writer that never came:
    `run` and `check` both hung with nothing on screen, which is the
    worst way for a command to fail. A directory or a broken symlink
    raised instead.

    Run in a subprocess with a timeout: a regression here HANGS, and a
    suite that hangs says nothing at all.
    """
    import os
    import subprocess
    import sys

    volume = tmp_path / "vol"
    volume.mkdir()
    (volume / "f1.xml").write_text("<alto/>", encoding="utf-8")
    os.mkfifo(volume / "gallica-iiif-manifest.csv")
    (volume / "mapping-iiif.csv").symlink_to(tmp_path / "gone.csv")
    (volume / "manifest-iiif.csv").mkdir()

    finished = subprocess.run(
        [sys.executable, "-c",
         "import pathlib, sys;"
         "from teille_douce.metadata.iiif import IIIFMapping;"
         "from teille_douce.preflight import _iiif_refused;"
         f"d = pathlib.Path({str(volume)!r});"
         "pages = [d / 'f1.xml'];"
         "print(IIIFMapping.detect_csv(d, pages));"
         "print(_iiif_refused(d, pages))"],
        capture_output=True, text=True, timeout=60,
        cwd=str(Path(__file__).resolve().parent.parent))

    assert finished.returncode == 0, finished.stderr
    assert finished.stdout.splitlines()[0] == "None"
