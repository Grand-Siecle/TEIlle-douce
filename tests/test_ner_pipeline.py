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
    """run_ner enchaine extraction/inference, alignement et resolution :
    la sortie de chaque phase doit etre l'entree de la suivante (le seul
    defaut qu'une extraction en facade peut introduire), et le nom du
    document doit atteindre la phase 9 (les CSV d'entites vont dans son
    sous-dossier, audit 2.4)."""
    vus = {}
    BLOCKS, SPANS, ALIGNED = ["bloc"], ["span"], ["aligne"]

    import src.enrichment.ner_detect as detect_mod
    import src.enrichment.ner_align as align_mod
    import src.enrichment.ner_resolve as resolve_mod

    def faux_extract(root, containers):
        vus["extract"] = (root, containers)
        return BLOCKS

    def faux_detect(blocks, models, entity_types, ner_models, threshold, root=None):
        vus["detect"] = (blocks, models, entity_types, threshold, root)
        return SPANS

    def faux_align(blocks, spans, entity_types, cert_thresholds):
        vus["align"] = (blocks, spans, entity_types, cert_thresholds)
        return ALIGNED

    def faux_resolve(root, aligned, entity_types, db, out_dir, document_name,
                     cert_thresholds=None):
        vus["resolve"] = (root, aligned, out_dir, document_name)
        vus["resolve_cert"] = cert_thresholds
        return [_ent("person", "Poussin")]

    monkeypatch.setattr(detect_mod, "extract_ner_blocks", faux_extract)
    monkeypatch.setattr(detect_mod, "detect_entities", faux_detect)
    monkeypatch.setattr(align_mod, "align_and_inject", faux_align)
    monkeypatch.setattr(resolve_mod, "resolve_entities", faux_resolve)

    root = etree.Element("TEI")
    modeles = object()
    resolved = ner_pipeline.run_ner(
        root, None, "LIV0001", models=modeles,
        containers=["ab"], entity_types={"person": {}},
        confidence_threshold=0.7, cert_thresholds={"high": 0.9},
        output_dir="/tmp/entities",
    )

    # chainage : la sortie de chaque phase est bien l'entree de la suivante
    assert vus["extract"] == (root, ["ab"])
    assert vus["detect"][0] is BLOCKS and vus["detect"][1] is modeles
    assert vus["detect"][4] is root, "detect_entities doit recevoir root=root"
    assert vus["align"][0] is BLOCKS and vus["align"][1] is SPANS
    assert vus["resolve"][0] is root and vus["resolve"][1] is ALIGNED

    # les surcharges du appelant priment sur la config du module
    assert vus["detect"][2] == {"person": {}} and vus["detect"][3] == 0.7
    assert vus["align"][3] == {"high": 0.9}
    # le meme seuil doit atteindre la phase 9 : sans lui, les mentions
    # sont graduees d'un cote et les entites de l'autre
    assert vus["resolve_cert"] == {"high": 0.9}
    assert vus["resolve"][2] == "/tmp/entities"
    assert vus["resolve"][3] == "LIV0001"

    assert [e.canonical_name for e in resolved] == ["Poussin"]


def test_the_cert_thresholds_reach_the_entity_as_well_as_its_mentions():
    """Un seuil passe a run_ner graduait les mentions d'un cote et les
    entites de l'autre : le fichier portait deux certitudes pour une
    meme lecture."""
    from src.enrichment.ner_resolve import _entity_cert
    from src.enrichment.ner_align import AlignedEntity

    mention = AlignedEntity(
        entity_type="person", text="Poussin", confidence=0.6,
        model="camembert", w_elements=[],
    )
    entite = type("E", (), {"mentions": [mention]})()

    assert _entity_cert(entite, {"low": 0.0, "high": 0.5}) == "high"
    assert _entity_cert(entite, {"low": 0.0, "high": 0.9}) == "low"
