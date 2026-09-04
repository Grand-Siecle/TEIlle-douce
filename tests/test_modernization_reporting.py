# Ce que la phase de modernisation dit d'elle-meme quand elle echoue.
#
# Audit 2.7 a ferme ce trou cote PyHellen (enrich_body rend des
# compteurs, main.py les affiche) et l'a laisse ouvert cote
# VieuxParler : modernize_body rendait 0, et main.py n'imprimait rien
# en dessous de 1. Un service mort en cours de run ecrivait donc tous
# les documents suivants sans un seul <choice>, chacun annonce comme
# converti, et le processus sortait 0.
#
# Aucun appel reseau : modernize_texts est remplace par un bouchon.
#
# Run: venv/bin/python -m pytest tests/test_modernization_reporting.py -q
import pytest
from lxml import etree

from teille_douce import modernize as modernize_mod
from teille_douce.body import builder
from teille_douce.tei import TEI


def qlocal(el):
    """Nom local du tag -- le projet melange tags nus et tags avec
    namespace dans le meme arbre (audit SS4.2)."""
    return etree.QName(el).localname


def _tei_avec_deux_lignes():
    """Un TEI minimal, non enrichi : deux lignes dans un <ab>."""
    tree = TEI("doc_test", [])
    tree.root = etree.fromstring(
        '<TEI><text><body><div>'
        '<ab corresp="#z1"><lb corresp="l1"/>Il eſtoit peintre'
        '<lb corresp="l2"/>de ſon meſtier</ab>'
        '</div></body></text></TEI>'
    )
    return tree


# =============================================================================
# 1. modernize_body -- une panne doit se lire dans ce qu'elle rend
# =============================================================================

def test_a_service_that_raises_is_reported_as_unavailable(monkeypatch):
    """VieuxParler qui meurt en cours de run : le document repart sans un
    seul <choice> et doit le DIRE, pas rendre le meme 0 qu'un document
    sans rien a moderniser."""
    def _explose(*args, **kwargs):
        raise ConnectionError("VieuxParler injoignable")

    monkeypatch.setattr(modernize_mod, "modernize_texts", _explose)

    stats = _tei_avec_deux_lignes().modernize_body()

    assert stats["server_unavailable"] is True
    assert stats["lines_modernized"] == 0


def test_a_service_that_returns_nothing_is_reported_as_unavailable(monkeypatch):
    """Tous les lots en echec : modernize_texts rend None. Le silence
    d'un service est une perte de donnees, pas un document sans matiere."""
    monkeypatch.setattr(modernize_mod, "modernize_texts", lambda *a, **k: None)

    stats = _tei_avec_deux_lignes().modernize_body()

    assert stats["server_unavailable"] is True
    assert stats["lines_modernized"] == 0


def test_a_document_with_nothing_to_modernize_is_not_a_failure(monkeypatch):
    """L'autre moitie de l'exigence : un service qui repond et ne change
    rien ne doit pas declencher d'alerte."""
    monkeypatch.setattr(
        modernize_mod, "modernize_texts", lambda textes, **k: list(textes)
    )

    stats = _tei_avec_deux_lignes().modernize_body()

    assert stats["server_unavailable"] is False
    assert stats["lines_modernized"] == 0
    assert stats["containers_failed"] == 0


def test_a_successful_modernization_still_counts_its_lines(monkeypatch):
    """Le compte des lignes reste disponible : il alimente la ligne de
    console 'Modernisation: N lines'."""
    monkeypatch.setattr(
        modernize_mod,
        "modernize_texts",
        lambda textes, **k: ["Il etait peintre", "de son metier"],
    )

    tree = _tei_avec_deux_lignes()
    stats = tree.modernize_body()

    assert stats["lines_modernized"] == 2
    assert stats["server_unavailable"] is False
    ab = tree.root.find(".//ab")
    assert [qlocal(c) for c in ab].count("choice") == 2


# =============================================================================
# 2. modernize_texts -- « rien a envoyer » n'est pas « rien recu »
# =============================================================================

def test_nothing_worth_sending_returns_the_originals_not_none():
    """Des lignes sans matiere (chiffres, ponctuation) ne partent pas a
    l'API. Rendre None comme un lot en echec rendait les deux cas
    indiscernables pour l'appelant, qui doit alerter sur l'un seulement."""
    textes = ["123", "  ", "-- ."]

    assert modernize_mod.modernize_texts(textes) == textes


# =============================================================================
# 3. _rebuild_with_modernization -- un conteneur laisse intact se compte
# =============================================================================

def test_a_container_left_untouched_is_counted_as_failed():
    """Un conteneur qui porte autre chose que <s>/<lb> est laisse tel
    quel plutot que reconstruit sans son contenu : c'est le bon choix,
    mais la modernisation de ce conteneur est perdue et doit remonter."""
    container = etree.fromstring(
        '<ab><lb corresp="l1"/><s xml:id="s1"><w>Il</w></s>'
        '<choice><orig>x</orig><reg>y</reg></choice></ab>'
    )
    groups = builder._parse_line_groups(container)
    stats = {"containers_failed": 0}

    compte = builder._rebuild_with_modernization(
        container, groups, {"l1": "Il"}, stats=stats
    )

    assert compte == 0
    assert stats["containers_failed"] == 1
    # et le conteneur est bien reste intact
    assert [qlocal(c) for c in container] == ["lb", "s", "choice"]


def test_apply_modernization_enriched_passes_the_failure_up():
    """Le compteur doit traverser l'entree publique, sinon rien ne le lit."""
    root = etree.fromstring(
        '<TEI><text><body><div>'
        '<ab><lb corresp="l1"/><s xml:id="s1"><w>Il</w></s>'
        '<choice><orig>x</orig><reg>y</reg></choice></ab>'
        '</div></body></text></TEI>'
    )
    stats = {"containers_failed": 0}

    compte = builder.apply_modernization_enriched(root, {"l1": "Il"}, stats=stats)

    assert compte == 0
    assert stats["containers_failed"] == 1
