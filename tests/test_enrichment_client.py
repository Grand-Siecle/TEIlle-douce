# Tests de teille_douce/enrichment/client.py::tag_texts -- envoi concurrent avec
# keep-alive (audit 3.3) et disjoncteur (audit 2.7). Aucun reseau : le
# transport httpx est un MockTransport.
#
# Run: venv/bin/python -m pytest tests/test_enrichment_client.py -q
import httpx
from dataclasses import replace

import pytest

from teille_douce.settings import get_settings, set_settings

from teille_douce.enrichment import client as client_mod
from teille_douce.enrichment.client import tag_texts


@pytest.fixture(autouse=True)
def _restore_settings():
    """Settings are process-wide now; hand the next file an untouched one."""
    before = get_settings()
    yield
    set_settings(before)


def _setting(**overrides):
    """Install overrides for the current case."""
    set_settings(replace(get_settings(), **overrides))


def _reponse_ok(texte):
    """Reponse PyHellen minimale : un token par mot du texte."""
    return {
        "result": [
            {"form": mot, "lemma": mot.lower(), "POS": "NOM",
             "morph": "", "treated": mot}
            for mot in texte.split()
        ]
    }


def test_tag_texts_ok_and_order_preserved():
    def handler(request):
        texte = request.read().decode()
        import json
        payload = json.loads(texte)
        return httpx.Response(200, json=_reponse_ok(payload["text"]))

    outcomes = tag_texts(
        [("Bonjour monde", "modele-fr"), ("Salve munde", "modele-la")],
        transport=httpx.MockTransport(handler),
    )

    assert [o[0] for o in outcomes] == ["ok", "ok"]
    statut, tokens, misaligned = outcomes[0]
    assert [t.form for t in tokens] == ["Bonjour", "monde"]
    assert misaligned == 0
    assert [t.form for t in outcomes[1][1]] == ["Salve", "munde"]


def test_tag_texts_isolated_failure_does_not_stop_the_rest():
    compteur = {"n": 0}

    def handler(request):
        compteur["n"] += 1
        if compteur["n"] == 1:
            return httpx.Response(500, text="boom")
        import json
        payload = json.loads(request.read().decode())
        return httpx.Response(200, json=_reponse_ok(payload["text"]))

    outcomes = tag_texts(
        [("premier texte", "m"), ("second texte", "m")],
        transport=httpx.MockTransport(handler),
    )

    statuts = [o[0] for o in outcomes]
    assert statuts.count("ok") == 1
    assert statuts.count("error") == 1


def test_tag_texts_circuit_breaker_opens_after_consecutive_failures(monkeypatch):
    """Audit 2.7 : apres N echecs consecutifs, plus aucune requete n'est
    envoyee — un serveur fige ne se transforme plus en heures de timeouts."""
    _setting(pyhellen_max_consecutive_failures=3)
    # concurrence 1 pour rendre l'ordre d'execution deterministe
    _setting(pyhellen_concurrency=1)
    envoyees = {"n": 0}

    def handler(request):
        envoyees["n"] += 1
        return httpx.Response(500, text="fige")

    outcomes = tag_texts(
        [(f"texte {i}", "m") for i in range(10)],
        transport=httpx.MockTransport(handler),
    )

    assert envoyees["n"] == 3, "le disjoncteur doit couper apres 3 echecs"
    assert [o[0] for o in outcomes[:3]] == ["error", "error", "error"]
    assert all(o[0] == "breaker" for o in outcomes[3:])


def test_tag_texts_success_resets_the_failure_counter(monkeypatch):
    _setting(pyhellen_max_consecutive_failures=3)
    _setting(pyhellen_concurrency=1)
    # echec, echec, succes, echec, echec : jamais 3 consecutifs
    reponses = iter([500, 500, 200, 500, 500])

    def handler(request):
        code = next(reponses)
        if code == 200:
            import json
            payload = json.loads(request.read().decode())
            return httpx.Response(200, json=_reponse_ok(payload["text"]))
        return httpx.Response(code, text="instable")

    outcomes = tag_texts(
        [(f"texte {i}", "m") for i in range(5)],
        transport=httpx.MockTransport(handler),
    )

    assert [o[0] for o in outcomes] == ["error", "error", "ok", "error", "error"]
    assert not any(o[0] == "breaker" for o in outcomes)
