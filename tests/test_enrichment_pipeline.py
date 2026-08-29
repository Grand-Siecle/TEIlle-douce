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
    monkeypatch.setattr(pipeline, "_tag_blocks", lambda *a, **k: ["tok"])
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
