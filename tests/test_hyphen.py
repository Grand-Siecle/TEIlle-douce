# Run: venv/bin/python -m pytest tests/test_hyphen.py -q
"""
Les predicats de cesure, partages par les trois phases qui la defont.

Elles ne peuvent pas fusionner — l'une garde une carte d'offsets, l'autre
repete le mot recolle comme contexte d'API, la troisieme ne fait
qu'effacer — mais elles doivent s'accorder sur CE QU'EST une cesure de
fin de ligne. Elles ne s'accordaient pas : l'une acceptait « - » et
l'autre seulement « ¬ » (audit 4.7).
"""
import pytest

from src.utils.hyphen import (
    HYPHEN_CHARS,
    ends_with_hyphen,
    joins_words,
    remove_soft_hyphens,
    strip_trailing_hyphen,
)


@pytest.mark.parametrize("ligne, attendu", [
    ("et vne Instruction necessa¬", True),
    ("mainte-", True),
    ("avec des espaces apres ¬  ", True),
    ("ligne ordinaire", False),
    ("", False),
    (None, False),
])
def test_ends_with_hyphen(ligne, attendu):
    assert ends_with_hyphen(ligne) is attendu


def test_strip_trailing_hyphen_keeps_the_rest():
    assert strip_trailing_hyphen("necessa¬") == "necessa"
    assert strip_trailing_hyphen("mainte-  ") == "mainte"
    assert strip_trailing_hyphen("ligne entiere") == "ligne entiere"


@pytest.mark.parametrize("avant, apres, attendu", [
    ("souv", "erain", True),
    ("Paris ", " Lyon", False),   # un tiret entre deux blancs ne coupe rien
    ("", "erain", False),
    ("souv", "", False),
    ("18", "20", False),          # un intervalle de chiffres non plus
])
def test_joins_words(avant, apres, attendu):
    assert joins_words(avant, apres) is attendu


def test_remove_soft_hyphens_is_always_the_right_repair():
    """Le ¬ marque le saut de ligne imprime et ne fait jamais partie d'un
    mot : quoi qu'un service renvoie, l'effacer est correct."""
    assert remove_soft_hyphens("Com¬pliés") == "Compliés"
    assert remove_soft_hyphens(None) is None
    assert remove_soft_hyphens("") == ""


def test_the_two_marks_are_declared_once():
    assert HYPHEN_CHARS == ("¬", "-")


@pytest.mark.parametrize("marque", ["¬", "-"])
def test_the_three_phases_rejoin_both_marks(marque):
    """La fixture porte les deux formes (f1 en « ¬ », f7 en « - »). Les
    trois phases nommees dans le module doivent recoller les deux — c'est
    l'accord que ce module existe pour garantir."""
    from src.modernize import dehyphenate_lines
    from src.lang.detector import LinguaDetector
    from src.enrichment.extractor import TextSpan
    from src.enrichment.dehyphenation import dehyphenate

    # 1. modernisation : recolle les lignes d'une meme zone
    joined, _ = dehyphenate_lines([f"mainte{marque}", "nant"])
    assert joined[0] == "maintenant"

    # 2. detection de langue : efface la marque sans rien recoller d'autre
    nettoye, _ = LinguaDetector()._clean_with_map(f"mainte{marque} nant")
    assert "maintenant" in nettoye

    # 3. enrichissement : recolle ET garde la carte des offsets
    # la seconde portion porte son separateur, comme l'extracteur le pose
    brut = f"mainte{marque} nant"
    def _span(texte, debut, fin, ligne, hyphen=False):
        return TextSpan(
            text=texte, offset_start=debut, offset_end=fin, line_index=ligne,
            lb_element=None, lb_corresp=None, hi_element=None, hi_rend=None,
            has_hyphen=hyphen,
        )

    spans = [
        _span(f"mainte{marque}", 0, 7, 0, hyphen=True),
        _span(" nant", 7, 12, 1),
    ]
    texte, offsets, joins = dehyphenate(brut, spans)
    assert "maintenant" in texte
    assert len(offsets) == len(texte)
    assert [j.hyphen_char for j in joins] == [marque]
