# Tests des surcharges d'environnement de config.py (audit 2.13) et de
# la source unique du seuil de divergence.
#
# config est un module a effets de bord a l'import : chaque cas le
# recharge avec un environnement prepare.
#
# Run: venv/bin/python -m pytest tests/test_config_env.py -q
import importlib
import sys
from pathlib import Path

import pytest


def _recharger_config(monkeypatch, **env):
    for cle, valeur in env.items():
        monkeypatch.setenv(cle, valeur)
    import config
    return importlib.reload(config)


@pytest.fixture(autouse=True)
def _config_propre():
    """Rendre a la suite un config non pollue par les surcharges."""
    yield
    import config
    importlib.reload(config)


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


def test_divergence_threshold_has_a_single_home(monkeypatch):
    """Audit 2.13 : le seuil vivait dans src/modernize.py ET dans la prose
    de l'editorialDecl, libres de diverger. La prose le lit maintenant."""
    cfg = _recharger_config(monkeypatch, ALTO2TEI_MODERNIZE_SIMILARITY_MIN="0.93")
    assert cfg.MODERNIZE_SIMILARITY_MIN == 0.93

    prose = cfg.EDITORIAL_DECLARATIONS["normalization"]["text"]
    assert "0.93" in prose, prose

    # et le code applique bien la meme valeur
    import src.modernize
    importlib.reload(src.modernize)
    assert src.modernize.MODERNIZE_SIMILARITY_MIN == 0.93
