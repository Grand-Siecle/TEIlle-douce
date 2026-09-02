# Tests des surcharges d'environnement de config.py (audit 2.13) et de
# la source unique du seuil de divergence.
#
# config est un module a effets de bord a l'import : chaque cas le
# recharge avec un environnement prepare. src.modernize lit config par
# `from config import ...`, donc une valeur figee a SON import : les cas
# qui verifient le comportement le rechargent aussi, et la fixture le
# remet d'aplomb — sans quoi un seuil de test resterait en place pour
# tous les fichiers suivants de la session pytest.
#
# Run: venv/bin/python -m pytest tests/test_config_env.py -q
import importlib

import pytest


def _recharger_config(monkeypatch, **env):
    for cle, valeur in env.items():
        monkeypatch.setenv(cle, valeur)
    import config
    return importlib.reload(config)


def _recharger_modernize():
    import src.modernize
    return importlib.reload(src.modernize)


def _recharger_prose():
    """La prose de l'editorialDecl lit le seuil a SON import, comme
    src.modernize : elle doit etre rendue dans le meme etat."""
    import src.teiheader.prose
    return importlib.reload(src.teiheader.prose)


@pytest.fixture(autouse=True)
def _config_propre():
    """Rendre a la suite un config non pollue par les surcharges."""
    yield
    import config
    importlib.reload(config)
    _recharger_modernize()
    _recharger_prose()


def test_service_urls_are_overridable(monkeypatch):
    """Les services bougent d'une machine a l'autre (portable, serveur de
    labo, CI) : les URL ne doivent pas s'editer en modifiant le fichier."""
    cfg = _recharger_config(
        monkeypatch,
        ALTO2TEI_PYHELLEN_URL="http://pyhellen.labo:9000",
        ALTO2TEI_MODERNIZE_URL="http://vieuxparler.labo:9011",
    )
    assert cfg.PYHELLEN_URL == "http://pyhellen.labo:9000"
    assert cfg.MODERNIZE_API["fra"] == "http://vieuxparler.labo:9011"


def test_modernize_url_is_overridable_per_language(monkeypatch):
    """MODERNIZE_API est une table faite pour grandir : une deuxieme
    langue doit rester joignable sans repatcher config.py."""
    cfg = _recharger_config(
        monkeypatch,
        ALTO2TEI_MODERNIZE_URL="http://defaut:9011",
        ALTO2TEI_MODERNIZE_URL_FRA="http://francais:9012",
    )
    assert cfg.MODERNIZE_API["fra"] == "http://francais:9012"


def test_numeric_settings_are_overridable_and_survive_garbage(monkeypatch):
    cfg = _recharger_config(monkeypatch, ALTO2TEI_PYHELLEN_TIMEOUT="30")
    assert cfg.PYHELLEN_TIMEOUT == 30.0

    # une valeur illisible garde le defaut ET le signale : ignorer une
    # faute de frappe en silence laisserait croire au reglage applique
    import config as config_mod
    defaut = config_mod.MODERNIZE_TIMEOUT
    with pytest.warns(RuntimeWarning, match="not a number"):
        cfg = _recharger_config(monkeypatch, ALTO2TEI_MODERNIZE_TIMEOUT="pas-un-nombre")
    assert cfg.MODERNIZE_TIMEOUT == defaut


@pytest.mark.parametrize("valeur", ["nan", "inf", "-5"])
def test_numeric_settings_reject_readable_nonsense(monkeypatch, valeur):
    """Les valeurs qui cassent vraiment le comportement sont lisibles :
    `float("nan")` reussit, et un seuil NaN rend toute comparaison fausse
    — le garde-fou cesse de rejeter quoi que ce soit sans rien dire. Un
    timeout negatif, lui, est accepte tel quel par httpx."""
    import config as config_mod
    defaut = config_mod.MODERNIZE_TIMEOUT
    with pytest.warns(RuntimeWarning):
        cfg = _recharger_config(monkeypatch, ALTO2TEI_MODERNIZE_TIMEOUT=valeur)
    assert cfg.MODERNIZE_TIMEOUT == defaut


def test_similarity_min_rejects_a_percentage(monkeypatch):
    """0.95 ecrit 95 : lisible, hors domaine, et le garde-fou rejetterait
    alors toute modernisation."""
    with pytest.warns(RuntimeWarning, match=r"outside \[0.0, 1.0\]"):
        cfg = _recharger_config(monkeypatch, ALTO2TEI_MODERNIZE_SIMILARITY_MIN="95")
    assert cfg.MODERNIZE_SIMILARITY_MIN == 0.8


def test_a_variable_set_but_empty_keeps_the_default(monkeypatch):
    """`export ALTO2TEI_MODERNIZE_URL="${URL_LABO}"` avec URL_LABO vide
    donnait une URL vide : la modernisation se desactivait en silence."""
    with pytest.warns(RuntimeWarning, match="set but empty"):
        cfg = _recharger_config(monkeypatch, ALTO2TEI_MODERNIZE_URL="")
    assert cfg.MODERNIZE_API["fra"] == "http://localhost:8011"


def test_booleans_refuse_a_value_they_cannot_read(monkeypatch):
    """"faux" n'est pas False : le lire comme tel desactiverait la phase
    en croyant obeir."""
    with pytest.warns(RuntimeWarning, match="not a boolean"):
        cfg = _recharger_config(monkeypatch, ALTO2TEI_NER="faux")
    assert cfg.NER_ENABLED is True

    cfg = _recharger_config(monkeypatch, ALTO2TEI_NER="0")
    assert cfg.NER_ENABLED is False


def test_divergence_threshold_has_a_single_home(monkeypatch):
    """Audit 2.13 : le seuil vivait dans src/modernize.py ET dans la prose
    de l'editorialDecl, libres de diverger. La prose le lit maintenant."""
    cfg = _recharger_config(monkeypatch, ALTO2TEI_MODERNIZE_SIMILARITY_MIN="0.93")
    assert cfg.MODERNIZE_SIMILARITY_MIN == 0.93

    # la prose vit desormais dans src/teiheader/prose.py, mais elle lit
    # toujours la meme valeur : c'est la seule chose qui compte ici
    prose = _recharger_prose().EDITORIAL_DECLARATIONS["normalization"]["text"]
    assert "0.93" in prose, prose

    # et le rejet des hallucinations bascule bien sur cette valeur : la
    # paire ci-dessous est a 0.904 de similarite, donc gardee au seuil
    # par defaut (0.8) et rejetee a 0.93.
    ancien = "Il eſtoit fort habile en la peinture"
    modernise = "Il était fort habile dans la peinture"
    assert _recharger_modernize()._is_divergent(ancien, modernise) is True

    monkeypatch.delenv("ALTO2TEI_MODERNIZE_SIMILARITY_MIN")
    _recharger_config(monkeypatch)
    assert _recharger_modernize()._is_divergent(ancien, modernise) is False


def test_config_holds_settings_and_not_the_encoding_schema():
    """Audit 4.10 : config.py melait des reglages, un schema d'encodage
    dont le code depend structurellement, et la prose editoriale. Un
    lecteur venu changer un repertoire de sortie ne doit pas enjamber
    cent lignes de correspondance TEI."""
    import config

    for schema in ("NER_ENTITY_TYPES", "NER_VOCABULARIES", "EDITORIAL_DECLARATIONS",
                   "POS_TAGSETS", "KEYWORDS_TAXONOMY", "LANGUAGE_DETECTION_DESCRIPTION"):
        assert not hasattr(config, schema), f"{schema} est reste dans config.py"

    # et il garde bien les vrais reglages
    for reglage in ("OCR_DIR", "OUTPUT_DIR", "PYHELLEN_URL", "MODERNIZE_TIMEOUT",
                    "NER_ENABLED", "RESPONSIBILITY"):
        assert hasattr(config, reglage), f"{reglage} a disparu de config.py"


def test_the_moved_blocks_are_reachable_where_they_now_live():
    from src.constants import KEYWORDS_TAXONOMY, POS_TAGSETS
    from src.enrichment.entity_schema import NER_ENTITY_TYPES, NER_VOCABULARIES
    from src.teiheader.prose import EDITORIAL_DECLARATIONS, LANGUAGE_DETECTION_DESCRIPTION

    assert "person" in NER_ENTITY_TYPES
    assert "materials" in NER_VOCABULARIES["art-vocabulary"]["categories"]
    assert "normalization" in EDITORIAL_DECLARATIONS
    assert POS_TAGSETS["freem"]["id"] and KEYWORDS_TAXONOMY["id"]
    assert LANGUAGE_DETECTION_DESCRIPTION


def test_ner_certainty_prose_has_a_single_home(monkeypatch):
    """Meme classe de defaut que l'audit 2.13, cote NER : la prose tapait
    « mid 0.6-0.85 » a la main quand le code emet @cert="medium" — chaque
    document annote documentait une valeur qu'aucun element ne porte. Les
    bandes se construisent desormais depuis NER_CERT_THRESHOLDS."""
    import config

    monkeypatch.setattr(
        config, "NER_CERT_THRESHOLDS", {"low": 0.0, "medium": 0.5, "high": 0.9}
    )
    prose = _recharger_prose().EDITORIAL_DECLARATIONS["interpretation"]["text"]
    assert "low < 0.5" in prose, prose
    assert "medium 0.5–0.9" in prose, prose
    assert "high >= 0.9" in prose, prose


def test_the_prose_names_the_cert_values_the_code_emits():
    """Les cles de la table SONT les valeurs emises (teidata.certainty) :
    la prose doit nommer celles-la, et pas une etiquette inventee."""
    from config import NER_CERT_THRESHOLDS

    prose = _recharger_prose().EDITORIAL_DECLARATIONS["interpretation"]["text"]
    for etiquette in NER_CERT_THRESHOLDS:
        assert etiquette in prose, f"{etiquette} absent de la prose : {prose}"
    assert "mid " not in prose, prose


def test_the_langusage_prose_lists_the_containers_actually_scanned():
    """La detection de langue tourne sur TEXT_CONTAINERS : <head> et
    <titlePart> l'ont rejointe (une page de titre est detectee comme le
    reste) sans que la phrase publiee le dise."""
    from src.constants import TEXT_CONTAINERS

    prose = _recharger_prose().LANGUAGE_DETECTION_DESCRIPTION
    assert ", ".join(TEXT_CONTAINERS) in prose, prose
