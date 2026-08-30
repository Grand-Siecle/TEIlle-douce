# Tests unitaires de src/enrichment/pipeline.py -- les trois passes de
# l'orchestrateur (preparation, tagging, mutation DOM), sans service
# PyHellen (les phases reseau sont bouchonnees).
#
# Run: venv/bin/python -m pytest tests/test_enrichment_pipeline.py -q
from lxml import etree

from src.enrichment import pipeline
from src.enrichment.client import NLPToken


def _tok(form="mot"):
    return NLPToken(form=form, lemma="", pos="", morph="",
                    treated=form, is_punctuation=False)


def _stats():
    return {
        "containers_found": 0, "containers_enriched": 0,
        "containers_skipped": 0, "containers_failed": 0,
        "tokens_total": 0, "sentences_total": 0,
    }


def _job_pour(monkeypatch, corresp="#zone_1", index=0, texte="Titre courant repete"):
    monkeypatch.setattr(pipeline, "get_model", lambda lang: "modele-factice")
    c = etree.fromstring(f'<fw corresp="{corresp}"><lb/>{texte}</fw>')
    return pipeline._prepare_container(c, index, _stats())


def test_prepare_container_builds_requests_and_skips_short_text(monkeypatch):
    job = _job_pour(monkeypatch)
    assert job is not None
    assert len(job.requests) == 1
    texte, modele, base, lang = job.requests[0]
    assert modele == "modele-factice"
    assert texte.strip() == texte

    stats = _stats()
    monkeypatch.setattr(pipeline, "get_model", lambda lang: "modele-factice")
    court = etree.fromstring('<ab corresp="#z"><lb/>ab</ab>')
    assert pipeline._prepare_container(court, 0, stats) is None
    assert stats["containers_skipped"] == 1


def test_finish_container_scopes_sentence_ids_by_container_index(monkeypatch):
    """Le scope des ids de phrase doit pairer @corresp avec l'index du
    conteneur : @corresp seul n'est pas unique (deux <fw> consecutifs d'une
    meme zone portent le meme @corresp), et deux scopes identiques
    produiraient des xml:id de phrase dupliques."""
    scopes = []

    def capture_segment(aligned, id_scope=""):
        scopes.append(id_scope)
        return []

    monkeypatch.setattr(pipeline, "segment_sentences", capture_segment)
    monkeypatch.setattr(pipeline, "align_tokens", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "rebuild_container", lambda *a, **k: None)

    for index in (3, 4):
        job = _job_pour(monkeypatch, index=index)
        job.outcomes = [("ok", [_tok()], 0)]
        pipeline._finish_container(job, _stats())

    assert len(scopes) == 2
    assert scopes[0] != scopes[1], (
        "meme @corresp -> les scopes doivent differer par l'index de conteneur"
    )
    assert "#zone_1" in scopes[0] and scopes[0].startswith("3\x1f")
    assert scopes[1].startswith("4\x1f")


def test_finish_container_fails_past_the_misaligned_token_threshold(monkeypatch):
    """Audit 2.12 : au-dela du seuil de tokens introuvables (ancres au
    curseur), le conteneur est marque en echec et son DOM reste intact —
    mieux vaut un conteneur non enrichi qu'annote au mauvais endroit."""
    def rebuild_interdit(*a, **k):
        raise AssertionError("le conteneur en echec ne doit pas etre reconstruit")

    monkeypatch.setattr(pipeline, "rebuild_container", rebuild_interdit)

    job = _job_pour(monkeypatch)
    # 3 tokens sur 10 introuvables -> 30 % > seuil de 20 %
    job.outcomes = [("ok", [_tok() for _ in range(10)], 3)]

    stats = _stats()
    assert pipeline._finish_container(job, stats) is None
    assert stats["containers_failed"] == 1
    assert stats["containers_enriched"] == 0


def test_finish_container_tolerates_misaligned_tokens_below_threshold(monkeypatch):
    """Sous le seuil, on enrichit quand meme (bruit OCR tolere) mais le
    repli est journalise en warning."""
    monkeypatch.setattr(pipeline, "align_tokens", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "segment_sentences", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "rebuild_container", lambda *a, **k: None)

    job = _job_pour(monkeypatch)
    job.outcomes = [("ok", [_tok() for _ in range(10)], 1)]

    stats = _stats()
    pipeline._finish_container(job, stats)
    assert stats["containers_failed"] == 0
    assert stats["containers_enriched"] == 1


def test_finish_container_failed_tagging_leaves_dom_untouched(monkeypatch):
    """Un echec HTTP (ou un disjoncteur ouvert) marque le conteneur en
    echec sans toucher au DOM."""
    def rebuild_interdit(*a, **k):
        raise AssertionError("pas de rebuild sur echec de tagging")

    monkeypatch.setattr(pipeline, "rebuild_container", rebuild_interdit)

    for outcome in (("error", "HTTP 500"), ("breaker", "circuit open"), None):
        job = _job_pour(monkeypatch)
        job.outcomes = [outcome]
        stats = _stats()
        assert pipeline._finish_container(job, stats) is None
        assert stats["containers_failed"] == 1


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


def test_enrich_body_wires_the_three_passes(monkeypatch):
    """enrich_body : preparation -> tagging groupe -> finition, avec les
    resultats redistribues au bon conteneur."""
    monkeypatch.setattr(pipeline, "check_server", lambda: True)
    monkeypatch.setattr(pipeline, "get_model", lambda lang: "modele")
    monkeypatch.setattr(pipeline, "align_tokens", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "segment_sentences", lambda *a, **k: [])
    monkeypatch.setattr(pipeline, "rebuild_container", lambda *a, **k: None)
    monkeypatch.setattr(pipeline, "chain_cross_container", lambda s: None)

    captures = {}

    def fake_tag_texts(requests, progress_callback=None):
        captures["requests"] = list(requests)
        return [("ok", [_tok()], 0) for _ in requests]

    monkeypatch.setattr(pipeline, "tag_texts", fake_tag_texts)

    root = etree.fromstring(
        '<TEI><text><body><div>'
        '<ab corresp="#z1"><lb/>Premier conteneur assez long</ab>'
        '<ab corresp="#z2"><lb/>Second conteneur assez long</ab>'
        '</div></body></text></TEI>'
    )
    stats = pipeline.enrich_body(root)

    assert len(captures["requests"]) == 2
    assert stats["containers_found"] == 2
    assert stats["containers_enriched"] == 2
    assert stats["containers_failed"] == 0


def test_finish_container_gate_is_per_block_not_diluted(monkeypatch):
    """La cascade d'erreurs d'alignement ne franchit pas la frontiere d'un
    bloc (le curseur est par appel) : un court bloc latin entierement
    desaligne doit condamner le conteneur meme si un grand bloc francais
    propre ferait passer le ratio global sous le seuil."""
    def rebuild_interdit(*a, **k):
        raise AssertionError("un bloc entierement desaligne doit faire echouer")

    monkeypatch.setattr(pipeline, "rebuild_container", rebuild_interdit)

    job = _job_pour(monkeypatch)
    # bloc 1 : 40 tokens propres ; bloc 2 : 4 tokens tous desalignes.
    # ratio global = 4/44 = 9 % (sous le seuil), ratio du bloc 2 = 100 %.
    job.requests = [
        (job.requests[0][0], "modele-fr", 0, "fra"),
        ("citation latine", "modele-la", 100, "lat"),
    ]
    job.outcomes = [
        ("ok", [_tok() for _ in range(40)], 0),
        ("ok", [_tok() for _ in range(4)], 4),
    ]

    stats = _stats()
    assert pipeline._finish_container(job, stats) is None
    assert stats["containers_failed"] == 1


def test_enrich_body_reports_an_unreachable_server(monkeypatch):
    """Audit 2.7 : un serveur mort APRES la sonde de demarrage laissait
    tous les compteurs a zero — le document paraissait simplement 'non
    enrichi' au lieu de 'enrichissement perdu'."""
    monkeypatch.setattr(pipeline, "check_server", lambda: False)

    root = etree.fromstring(
        '<TEI><text><body><div><ab corresp="#z1"><lb/>Texte</ab></div></body></text></TEI>'
    )
    stats = pipeline.enrich_body(root)

    assert stats["server_unavailable"] is True
    assert stats["containers_enriched"] == 0
