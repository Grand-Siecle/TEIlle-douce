# Tests de src/enrichment/ner_pipeline.py -- la facade des phases 7-9,
# sans charger les modeles NER (torch/transformers/gliner sont bouchonnes).
#
# Run: venv/bin/python -m pytest tests/test_ner_pipeline.py -q
from lxml import etree

from src.enrichment import ner_pipeline
from src.enrichment.ner_resolve import ResolvedEntity


def _ent(entity_type, name, mentions=1):
    return ResolvedEntity(
        entity_type=entity_type, canonical_name=name,
        xml_id=f"{entity_type}-x", mentions=[object()] * mentions,
    )


def test_summarize_counts_by_type_and_mentions():
    resume = ner_pipeline.summarize([
        _ent("person", "Poussin", mentions=3),
        _ent("place", "Rome", mentions=1),
    ])
    assert resume.startswith("2 entities (")
    assert "1 person" in resume and "1 place" in resume
    assert resume.endswith("4 mentions")


def test_summarize_returns_none_without_entities():
    assert ner_pipeline.summarize([]) is None
    assert ner_pipeline.summarize(None) is None


def test_get_models_loads_once_per_run(monkeypatch):
    """Les modeles NER coutent cher a charger et sont identiques d'un
    document a l'autre : la facade les met en cache pour le run."""
    charges = []

    class FauxModeles:
        def __init__(self, config):
            charges.append(config)

    import src.enrichment.ner_models as ner_models_mod
    monkeypatch.setattr(ner_models_mod, "NERModels", FauxModeles)
    ner_pipeline.reset_models()
    try:
        premier = ner_pipeline.get_models()
        second = ner_pipeline.get_models()
        assert premier is second
        assert len(charges) == 1, "les modeles ont ete recharges"
    finally:
        ner_pipeline.reset_models()


def test_run_ner_chains_the_three_phases(monkeypatch):
    """run_ner enchaine extraction/inference, alignement et resolution en
    passant le nom du document (les CSV d'entites vont dans son
    sous-dossier, audit 2.4)."""
    appels = {}

    import src.enrichment.ner_detect as detect_mod
    import src.enrichment.ner_align as align_mod
    import src.enrichment.ner_resolve as resolve_mod

    monkeypatch.setattr(detect_mod, "extract_ner_blocks",
                        lambda root, containers: appels.setdefault("blocks", ["b"]))
    monkeypatch.setattr(detect_mod, "detect_entities",
                        lambda blocks, models, *a, **k: appels.setdefault("spans", ["s"]))
    monkeypatch.setattr(align_mod, "align_and_inject",
                        lambda blocks, spans, *a: appels.setdefault("aligned", ["a"]))

    def faux_resolve(root, aligned, types, db, out_dir, document_name):
        appels["document_name"] = document_name
        return [_ent("person", "Poussin")]

    monkeypatch.setattr(resolve_mod, "resolve_entities", faux_resolve)

    root = etree.Element("TEI")
    resolved = ner_pipeline.run_ner(root, None, "LIV0001", models=object())

    assert appels["document_name"] == "LIV0001"
    assert [e.canonical_name for e in resolved] == ["Poussin"]
