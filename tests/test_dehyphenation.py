# Run: venv/bin/python tests/test_dehyphenation.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.modernize import dehyphenate_lines
from src.tei import strip_residual_hyphens


def test_basic_join():
    out = dehyphenate_lines(["et vne Instruction necessa¬", "ire pour tous"])
    assert out[0].endswith("necessaire"), out[0]
    assert out[1].startswith("necessaire"), out[1]


def test_cross_zone_not_joined():
    out = dehyphenate_lines(
        ["fin de page auec Com¬", "TITRE COVRANT", "pliés et le reste"],
        zone_types=["MainZone", "RunningTitleZone", "MainZone"],
    )
    # ligne 0 fusionne avec la ligne 2 (même zone), pas la 1
    assert out[0].endswith("Compliés"), out[0]
    assert out[2].startswith("Compliés"), out[2]


def test_chained_hyphens():
    out = dehyphenate_lines(["extra¬", "ordinai¬", "rement grand"])
    assert "¬" not in out[0] and "¬" not in out[1], out


def test_strip_residual():
    assert strip_residual_hyphens(["Com¬pliés où nous", None, "sans césure"]) == \
        ["Compliés où nous", None, "sans césure"]


if __name__ == "__main__":
    test_basic_join()
    test_cross_zone_not_joined()
    test_chained_hyphens()
    test_strip_residual()
    print("OK test_dehyphenation")
