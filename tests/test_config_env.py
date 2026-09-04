# Ce que config.py contient, et ce que la prose editoriale en fait.
#
# Les surcharges d'environnement elles-memes ont demenage dans
# tests/test_settings.py, qui les verifie mieux : il affirme la COUCHE
# d'ou vient une valeur, pas seulement la valeur finale, et il n'a plus
# besoin d'`importlib.reload` puisque config.py n'a plus d'effet de bord
# a l'import.
#
# Restent ici deux familles : la repartition des contenus (des reglages,
# pas de schema d'encodage) et le domicile unique des seuils que la prose
# publie dans le teiHeader.
#
# Run: venv/bin/python -m pytest tests/test_config_env.py -q
from dataclasses import replace

import pytest

from teille_douce.settings import get_settings, set_settings, use_settings
from teille_douce.teiheader.prose import (
    LANGUAGE_DETECTION_DESCRIPTION,
    editorial_declarations,
)


@pytest.fixture(autouse=True)
def _restore_settings():
    """Les reglages sont desormais a l'echelle du processus."""
    before = get_settings()
    yield
    set_settings(before)


# =============================================================================
# Ce que config.py contient
# =============================================================================

def test_config_holds_settings_and_not_the_encoding_schema():
    """Audit 4.10 : config.py melait des reglages, un schema d'encodage
    dont le code depend structurellement, et la prose editoriale. Un
    lecteur venu changer un repertoire de sortie ne doit pas enjamber
    cent lignes de correspondance TEI."""
    import teille_douce.config as config

    for schema in ("NER_ENTITY_TYPES", "NER_VOCABULARIES", "EDITORIAL_DECLARATIONS",
                   "POS_TAGSETS", "KEYWORDS_TAXONOMY", "LANGUAGE_DETECTION_DESCRIPTION"):
        assert not hasattr(config, schema), f"{schema} est reste dans config.py"

    # et il garde bien ce qui decrit le projet, plus les valeurs par defaut
    for reglage in ("DEFAULT_OCR_DIR", "DEFAULT_OUTPUT_DIR", "DEFAULT_PYHELLEN_URL",
                    "NER_CERT_THRESHOLDS", "RESPONSIBILITY", "APP_VERSIONS"):
        assert hasattr(config, reglage), f"{reglage} a disparu de config.py"


def test_config_no_longer_reads_the_environment_at_import(monkeypatch):
    """C'etait le bug : une valeur figee a l'import ne peut plus etre
    surchargee par un drapeau analyse ensuite. Les couches sont resolues
    dans teille_douce/settings.py, apres parse_args.

    Verifie sur le comportement et non sur le texte du fichier : la
    version precedente cherchait "os.environ" et "_env(" dans la source,
    et un `os.getenv` au niveau du module — exactement la rechute — passait
    les deux assertions sans etre vu."""
    import importlib

    import teille_douce.config as config

    monkeypatch.setenv("TDOUCE_OCR_DIR", "/depuis-l-environnement")
    monkeypatch.setenv("TDOUCE_OUTPUT_DIR", "/depuis-l-environnement")
    monkeypatch.setenv("TDOUCE_PYHELLEN_URL", "http://depuis-l-environnement")
    importlib.reload(config)

    try:
        assert config.DEFAULT_OCR_DIR == "OCR"
        assert config.DEFAULT_OUTPUT_DIR == "tei_output"
        assert config.DEFAULT_PYHELLEN_URL == "http://localhost:8000"

        # Et la seule chose qui lit bien cet environnement, c'est la
        # resolution des couches, appelee apres parse_args.
        from teille_douce.settings import Settings
        resolved = Settings.load(env={"TDOUCE_OCR_DIR": "/depuis-l-environnement"},
                                 flags={})
        assert str(resolved.ocr_dir) == "/depuis-l-environnement"
    finally:
        monkeypatch.undo()
        importlib.reload(config)


def test_the_moved_blocks_are_reachable_where_they_now_live():
    from teille_douce.constants import KEYWORDS_TAXONOMY, POS_TAGSETS
    from teille_douce.enrichment.entity_schema import NER_ENTITY_TYPES, NER_VOCABULARIES

    assert "person" in NER_ENTITY_TYPES
    assert "materials" in NER_VOCABULARIES["art-vocabulary"]["categories"]
    assert "normalization" in editorial_declarations()
    assert POS_TAGSETS["freem"]["id"] and KEYWORDS_TAXONOMY["id"]
    assert LANGUAGE_DETECTION_DESCRIPTION


# =============================================================================
# Le domicile unique des seuils publies
# =============================================================================

def test_divergence_threshold_has_a_single_home():
    """Audit 2.13 : le seuil vivait dans teille_douce/modernize.py ET dans
    la prose de l'editorialDecl, libres de diverger. Les deux le lisent
    maintenant au meme endroit, et au moment de l'appel."""
    from teille_douce.modernize import _is_divergent

    # La paire ci-dessous est a 0.904 de similarite : gardee au seuil par
    # defaut (0.8), rejetee a 0.93.
    ancien = "Il eſtoit fort habile en la peinture"
    modernise = "Il était fort habile dans la peinture"

    with use_settings(modernize_similarity_min=0.93):
        assert "0.93" in editorial_declarations()["normalization"]["text"]
        assert _is_divergent(ancien, modernise) is True

    with use_settings(modernize_similarity_min=0.8):
        assert _is_divergent(ancien, modernise) is False


def test_the_prose_follows_the_phases_actually_enabled():
    """La table etait evaluee a l'import, donc les drapeaux de phase
    etaient figes avant que la ligne de commande soit lue : un run lance
    avec --no-modernize declarait quand meme la modernisation dans son
    en-tete."""
    with use_settings(modernize=False, enrich=True, ner=True):
        assert editorial_declarations()["normalization"]["enabled"] is False

    with use_settings(modernize=True):
        assert editorial_declarations()["normalization"]["enabled"] is True


def test_ner_certainty_prose_has_a_single_home(monkeypatch):
    """Meme classe de defaut que l'audit 2.13, cote NER : la prose tapait
    « mid 0.6-0.85 » a la main quand le code emet @cert="medium" — chaque
    document annote documentait une valeur qu'aucun element ne porte. Les
    bandes se construisent desormais depuis NER_CERT_THRESHOLDS."""
    import teille_douce.teiheader.prose as prose_mod

    monkeypatch.setattr(
        prose_mod, "NER_CERT_THRESHOLDS", {"low": 0.0, "medium": 0.5, "high": 0.9}
    )
    prose = editorial_declarations()["interpretation"]["text"]

    assert "low < 0.5" in prose, prose
    assert "medium 0.5–0.9" in prose, prose
    assert "high >= 0.9" in prose, prose


def test_the_prose_names_the_cert_values_the_code_emits():
    """Les cles de la table SONT les valeurs emises (teidata.certainty) :
    la prose doit nommer celles-la, et pas une etiquette inventee."""
    from teille_douce.config import NER_CERT_THRESHOLDS

    prose = editorial_declarations()["interpretation"]["text"]
    for etiquette in NER_CERT_THRESHOLDS:
        assert etiquette in prose, f"{etiquette} absent de la prose : {prose}"
    assert "mid " not in prose, prose


def test_the_langusage_prose_lists_the_containers_actually_scanned():
    """La detection de langue tourne sur TEXT_CONTAINERS : <head> et
    <titlePart> l'ont rejointe (une page de titre est detectee comme le
    reste) sans que la phrase publiee le dise."""
    from teille_douce.constants import TEXT_CONTAINERS

    assert ", ".join(TEXT_CONTAINERS) in LANGUAGE_DETECTION_DESCRIPTION
