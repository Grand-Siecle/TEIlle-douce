# Tests unitaires de src/enrichment/pipeline.py -- parties testables sans
# service PyHellen (les phases reseau sont bouchonnees).
#
# Run: venv/bin/python -m pytest tests/test_enrichment_pipeline.py -q
from lxml import etree

from src.enrichment import pipeline


def test_process_container_scopes_sentence_ids_by_container_index(monkeypatch):
    """Le scope des ids de phrase doit pairer @corresp avec l'index du
    conteneur : @corresp seul n'est pas unique (deux <fw> consecutifs d'une
    meme zone portent le meme @corresp), et deux scopes identiques
    produiraient des xml:id de phrase dupliques."""
    scopes = []

    def capture_segment(aligned, id_scope=""):
        scopes.append(id_scope)
        return []

    monkeypatch.setattr(pipeline, "segment_sentences", capture_segment)
    monkeypatch.setattr(pipeline, "get_model", lambda lang: "modele-factice")
    monkeypatch.setattr(pipeline, "_tag_blocks", lambda *a, **k: (["tok"], 0))
    monkeypatch.setattr(pipeline, "align_tokens", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "rebuild_container", lambda *a, **k: None)

    stats = {
        "containers_found": 0, "containers_enriched": 0,
        "containers_skipped": 0, "containers_failed": 0,
        "tokens_total": 0, "sentences_total": 0,
    }
    # Deux conteneurs partageant le meme @corresp (cas <fw> reel)
    fw = '<fw corresp="#zone_1"><lb corresp="#l{n}"/>Titre courant repete</fw>'
    c1 = etree.fromstring(fw.format(n=1))
    c2 = etree.fromstring(fw.format(n=2))

    pipeline._process_container(c1, stats, container_index=3)
    pipeline._process_container(c2, stats, container_index=4)

    assert len(scopes) == 2
    assert scopes[0] != scopes[1], (
        "meme @corresp -> les scopes doivent differer par l'index de conteneur"
    )
    assert "#zone_1" in scopes[0] and scopes[0].startswith("3\x1f")
    assert scopes[1].startswith("4\x1f")


def _stats():
    return {
        "containers_found": 0, "containers_enriched": 0,
        "containers_skipped": 0, "containers_failed": 0,
        "tokens_total": 0, "sentences_total": 0,
    }


def test_container_fails_past_the_misaligned_token_threshold(monkeypatch):
    """Audit 2.12 : au-dela du seuil de tokens introuvables (ancres au
    curseur), le conteneur est marque en echec et son DOM reste intact —
    mieux vaut un conteneur non enrichi qu'annote au mauvais endroit."""
    monkeypatch.setattr(pipeline, "get_model", lambda lang: "modele")
    # 3 tokens sur 10 introuvables -> 30 % > seuil de 20 %
    monkeypatch.setattr(pipeline, "_tag_blocks", lambda *a, **k: (["t"] * 10, 3))

    def rebuild_interdit(*a, **k):
        raise AssertionError("le conteneur en echec ne doit pas etre reconstruit")

    monkeypatch.setattr(pipeline, "rebuild_container", rebuild_interdit)

    stats = _stats()
    c = etree.fromstring('<ab corresp="#zone_1"><lb/>Texte suffisant ici</ab>')
    assert pipeline._process_container(c, stats) is None
    assert stats["containers_failed"] == 1
    assert stats["containers_enriched"] == 0


def test_container_tolerates_misaligned_tokens_below_threshold(monkeypatch):
    """Sous le seuil, on enrichit quand meme (bruit OCR tolere) mais le
    repli est journalise en warning."""
    monkeypatch.setattr(pipeline, "get_model", lambda lang: "modele")
    monkeypatch.setattr(pipeline, "_tag_blocks", lambda *a, **k: (["t"] * 10, 1))
    monkeypatch.setattr(pipeline, "align_tokens", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "segment_sentences", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "rebuild_container", lambda *a, **k: None)

    stats = _stats()
    c = etree.fromstring('<ab corresp="#zone_1"><lb/>Texte suffisant ici</ab>')
    pipeline._process_container(c, stats)
    assert stats["containers_failed"] == 0
    assert stats["containers_enriched"] == 1


def test_align_tokens_counts_cursor_fallbacks():
    """Audit 2.12 : _align_tokens compte les tokens introuvables (places
    au curseur) au lieu de les avaler en silence."""
    from src.enrichment.client import _align_tokens

    raw = [
        {"form": "Bonjour", "lemma": "", "pos": "", "morph": "",
         "treated": "Bonjour", "is_punctuation": False},
        {"form": "INTROUVABLE", "lemma": "", "pos": "", "morph": "",
         "treated": "INTROUVABLE", "is_punctuation": False},
        {"form": "monde", "lemma": "", "pos": "", "morph": "",
         "treated": "monde", "is_punctuation": False},
    ]
    tokens, misaligned = _align_tokens(raw, "Bonjour tout le monde")
    assert misaligned == 1
    assert [t.form for t in tokens] == ["Bonjour", "INTROUVABLE", "monde"]

    tokens, misaligned = _align_tokens(raw[:1] + raw[2:], "Bonjour monde")
    assert misaligned == 0
