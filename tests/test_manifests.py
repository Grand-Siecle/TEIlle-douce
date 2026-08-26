# Run: venv/bin/python tests/test_manifests.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.metadata.csv_book import select_manifest

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
