# Run: venv/bin/python -m pytest tests/test_dehyphenation.py -q
#
# Deux etages, a ne pas confondre :
#   - dehyphenate_lines produit le TEXTE ENVOYE A L'API, ou le mot
#     recolle est repete sur chaque ligne qu'il traverse (contexte) ;
#   - drop_carried_words defait cette repetition avant l'ecriture des
#     <reg>, sinon toute extraction du texte modernise sort des mots
#     doubles (audit 1.12).
# Le second parametre de retour, `carried`, est le lien entre les deux.
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.modernize import dehyphenate_lines
from src.tei import drop_carried_words, strip_residual_hyphens


def test_basic_join():
    out, carried = dehyphenate_lines(["et vne Instruction necessa¬", "ire pour tous"])
    assert out[0].endswith("necessaire"), out[0]
    assert out[1].startswith("necessaire"), out[1]
    assert carried == {1}


def test_cross_zone_not_joined():
    out, carried = dehyphenate_lines(
        ["fin de page auec Com¬", "TITRE COVRANT", "pliés et le reste"],
        zone_types=["MainZone", "RunningTitleZone", "MainZone"],
    )
    # ligne 0 fusionne avec la ligne 2 (même zone), pas la 1
    assert out[0].endswith("Compliés"), out[0]
    assert out[2].startswith("Compliés"), out[2]
    assert carried == {2}


def test_chained_hyphens_repeat_the_word_as_api_context():
    """Le mot complet est pose sur chaque ligne traversee : c'est ce que
    l'API doit voir. Les lignes 1 et 2 sont signalees comme portees."""
    out, carried = dehyphenate_lines(["extra¬", "ordinai¬", "rement grand"])
    assert "¬" not in "".join(out), out
    assert out == ["extraordinairement", "extraordinairement",
                   "extraordinairement grand"]
    assert carried == {1, 2}


def test_chained_4_lines():
    out, carried = dehyphenate_lines(["ex¬", "tra¬", "ordinai¬", "rement grand"])
    assert "¬" not in "".join(out), out
    assert out == ["extraordinairement"] * 3 + ["extraordinairement grand"]
    assert carried == {1, 2, 3}


def test_cascade_two_words():
    # Pas une chaine de cesures : la ligne 1 porte deux tokens ("cc dd¬"),
    # la ligne 0 n'absorbe donc que le premier ("cc") ; le "dd¬" restant
    # se resout contre la ligne 2 au passage suivant.
    out, carried = dehyphenate_lines(["aaa bb¬", "cc dd¬", "ee"])
    assert "¬" not in "".join(out), out
    assert out == ["aaa bbcc", "bbcc ddee", "ddee"]
    assert carried == {1, 2}


# =============================================================================
# La repetition ne doit pas atteindre la sortie (audit 1.12)
# =============================================================================

def test_carried_words_are_dropped_from_the_modernized_output():
    """Le mot recolle reste sur la ligne ou il COMMENCE ; les lignes qui
    ne font que le continuer perdent leur premier token — une ligne qui
    n'etait qu'un fragment ne contribue donc rien en propre."""
    joined, carried = dehyphenate_lines(["extra¬", "ordinai¬", "rement grand"])
    # l'API renvoie ici le texte inchange : on teste le decoupage, pas elle
    sortie = drop_carried_words(joined, carried)
    assert sortie == ["extraordinairement", "", "grand"]
    # aucun mot double dans une extraction du texte modernise
    assert " ".join(s for s in sortie if s) == "extraordinairement grand"


def test_carried_words_dropped_on_a_cascade():
    joined, carried = dehyphenate_lines(["aaa bb¬", "cc dd¬", "ee"])
    sortie = drop_carried_words(joined, carried)
    assert sortie == ["aaa bbcc", "ddee", ""]
    assert " ".join(s for s in sortie if s) == "aaa bbcc ddee"


def test_drop_carried_words_survives_none_and_short_lines():
    assert drop_carried_words(["mot", None, ""], {1, 2, 99}) == ["mot", None, ""]
    assert drop_carried_words(["seulmot"], {0}) == [""]


def test_strip_residual():
    assert strip_residual_hyphens(["Com¬pliés où nous", None, "sans césure"]) == \
        ["Compliés où nous", None, "sans césure"]
