# -----------------------------------------------------------
# Tests ciblés pour src/enrichment/ner_detect.py (Phase 7 :
# extraction de blocs de texte et inférence NER).
# -----------------------------------------------------------
"""
Périmètre EXCLUSIF : extract_ner_blocks, detect_entities, NERBlock, NERSpan,
_chunk_text, _run_camembert, _run_gliner (et _build_label_map en passant,
utilisé par plusieurs de ces fonctions).

Aucun modèle réel n'est chargé (pas de NERModels, pas de torch/flair/gliner
instanciés) : on passe des doublures exploitant le duck typing utilisé par
le module lui-même :
  - _run_camembert reçoit un callable ``model(batch_texts) -> list[list[dict]]``
  - _run_gliner reçoit un objet exposant ``.inference(texts, labels, threshold=...)``
  - detect_entities reçoit un objet factice portant les attributs
    ``.camembert`` (callable) et ``.gliner`` (objet à ``.inference``).

filter_spans / extract_title_from_tei (src/enrichment/ner_filter.py) sont
appelés tels quels par detect_entities : ce sont de vraies fonctions pures,
sans dépendance modèle, donc on les laisse s'exécuter normalement dans les
tests d'intégration.
"""

from lxml import etree

from src.constants import NS_XML
from src.enrichment.ner_detect import (
    NERBlock,
    NERSpan,
    extract_ner_blocks,
    detect_entities,
    _build_label_map,
    _chunk_text,
    _run_camembert,
    _run_gliner,
)

NS_TEI_URI = "http://www.tei-c.org/ns/1.0"
XML_LANG = f"{{{NS_XML}}}lang"


def qlocal(el):
    """Nom local d'un élément, namespace ou non (convention du dépôt)."""
    return etree.QName(el).localname


# =============================================================================
# 1. extract_ner_blocks — dispatch par langue / présence de <choice>
# =============================================================================


def test_extract_ner_blocks_dispatch_fra_nonfra_and_raw():
    xml = f"""
    <TEI xmlns="{NS_TEI_URI}">
      <text><body>
        <ab xml:lang="fra">
          <choice>
            <orig><w>Bonjour</w><w>le</w><w>monde</w></orig>
            <reg type="modernized">Bonjour, le monde !</reg>
          </choice>
        </ab>
        <ab xml:lang="lat">
          <choice>
            <orig><w>Roma</w></orig>
            <reg type="modernized">Roma</reg>
          </choice>
        </ab>
        <note xml:lang="fra">Texte simple sans balisage.</note>
      </body></text>
    </TEI>
    """
    root = etree.fromstring(xml.encode())
    blocks = extract_ner_blocks(root, {"ab", "note"})

    assert len(blocks) == 4

    fra_orig, fra_reg, lat_orig, raw_note = blocks

    # Conteneur français avec <choice> -> bloc orig (CamemBERT) ET reg (GLiNER)
    assert fra_orig.source == "orig"
    assert fra_orig.lang == "fra"
    assert fra_orig.text == "Bonjour le monde"
    assert qlocal(fra_orig.container) == "ab"

    assert fra_reg.source == "reg"
    assert fra_reg.lang == "fra"
    assert fra_reg.text == "Bonjour, le monde !"

    # Conteneur non-français avec <choice> -> orig seul, pas de bloc reg
    assert lat_orig.source == "orig"
    assert lat_orig.lang == "lat"
    assert lat_orig.text == "Roma"
    assert not any(b.lang == "lat" and b.source == "reg" for b in blocks)

    # Conteneur sans <choice> -> bloc raw
    assert raw_note.source == "raw"
    assert raw_note.lang == "fra"
    assert raw_note.text == "Texte simple sans balisage."
    assert qlocal(raw_note.container) == "note"


def test_extract_ner_blocks_ignores_und_and_missing_lang():
    xml = f"""
    <TEI xmlns="{NS_TEI_URI}">
      <text><body>
        <ab xml:lang="und">
          <choice><orig><w>Ignore</w></orig></choice>
        </ab>
        <ab>
          <choice><orig><w>SansAttribut</w></orig></choice>
        </ab>
        <note xml:lang="fra">Un seul bloc valide.</note>
      </body></text>
    </TEI>
    """
    root = etree.fromstring(xml.encode())
    blocks = extract_ner_blocks(root, {"ab", "note"})

    # xml:lang="und" explicite ET conteneur sans xml:lang (défaut "und")
    # doivent tous deux être ignorés.
    assert len(blocks) == 1
    assert blocks[0].source == "raw"
    assert blocks[0].text == "Un seul bloc valide."


def test_extract_ner_blocks_no_body_returns_empty_list():
    xml = f'<TEI xmlns="{NS_TEI_URI}"><text><div>pas de body ici</div></text></TEI>'
    root = etree.fromstring(xml.encode())
    assert extract_ner_blocks(root, {"ab", "note"}) == []


def test_extract_ner_blocks_skips_empty_choice_and_empty_raw_container():
    xml = f"""
    <TEI xmlns="{NS_TEI_URI}">
      <text><body>
        <ab xml:lang="fra">
          <choice><orig/></choice>
        </ab>
        <note xml:lang="fra">   </note>
      </body></text>
    </TEI>
    """
    root = etree.fromstring(xml.encode())
    blocks = extract_ner_blocks(root, {"ab", "note"})
    # <orig> vide (pas de <w>) et pas de <reg> -> aucun bloc pour le <ab>.
    # <note> avec seulement des espaces -> aucun bloc raw non plus.
    assert blocks == []


# =============================================================================
# 2. extract_ner_blocks — cartes d'offsets char_to_w / char_to_reg
# =============================================================================


def test_extract_ner_blocks_offset_maps_point_to_correct_elements():
    xml = f"""
    <TEI xmlns="{NS_TEI_URI}">
      <text><body>
        <ab xml:lang="fra">
          <choice>
            <orig><w>Un</w><w>deux</w></orig>
            <reg type="modernized">Un, deux</reg>
          </choice>
          <choice>
            <orig><w>trois</w></orig>
            <reg type="modernized">trois!</reg>
          </choice>
        </ab>
      </body></text>
    </TEI>
    """
    root = etree.fromstring(xml.encode())
    blocks = extract_ner_blocks(root, {"ab", "note"})
    orig_block = next(b for b in blocks if b.source == "orig")
    reg_block = next(b for b in blocks if b.source == "reg")

    assert orig_block.text == "Un deux trois"
    # char_to_w doit pointer vers les bons <w>, offsets globaux (2e <choice>
    # décalé après le premier).
    for start, end, w_elem in orig_block.char_to_w:
        assert orig_block.text[start:end] == w_elem.text
    assert [w.text for _, _, w in orig_block.char_to_w] == ["Un", "deux", "trois"]
    assert orig_block.char_to_w[0][:2] == (0, 2)
    assert orig_block.char_to_w[1][:2] == (3, 7)
    assert orig_block.char_to_w[2][:2] == (8, 13)

    assert reg_block.text == "Un, deux trois!"
    for start, end, reg_elem in reg_block.char_to_reg:
        assert reg_block.text[start:end] == reg_elem.text
    assert [r.text for _, _, r in reg_block.char_to_reg] == ["Un, deux", "trois!"]
    assert reg_block.char_to_reg[0][:2] == (0, 8)
    assert reg_block.char_to_reg[1][:2] == (9, 15)


def test_extract_ner_blocks_handles_punctuation_and_skips_empty_tokens():
    """<pc> join="left" vs. default spacing, empty <w>/<pc> silently skipped."""
    xml = f"""
    <TEI xmlns="{NS_TEI_URI}">
      <text><body>
        <ab xml:lang="fra">
          <choice>
            <orig><w>Un</w><w/><pc/><pc>,</pc><w>deux</w><pc join="left">!</pc></orig>
            <reg type="modernized">   </reg>
          </choice>
          <choice>
            <orig><w>trois</w></orig>
            <reg type="modernized">Bonjour</reg>
          </choice>
        </ab>
      </body></text>
    </TEI>
    """
    root = etree.fromstring(xml.encode())
    blocks = extract_ner_blocks(root, {"ab", "note"})
    orig_block = next(b for b in blocks if b.source == "orig")
    reg_block = next(b for b in blocks if b.source == "reg")

    # Empty <w/> silently skipped; "," gets a space before it (no join
    # attribute); "!" (join="left") sticks directly to "deux".
    assert orig_block.text == "Un , deux! trois"
    assert [w.text for _, _, w in orig_block.char_to_w] == ["Un", "deux", "trois"]

    # Whitespace-only <reg> in the first <choice> contributes nothing (no
    # stray leading space in the second choice's text either).
    assert reg_block.text == "Bonjour"
    assert reg_block.char_to_reg[0][:2] == (0, 7)


# =============================================================================
# 3. _chunk_text
# =============================================================================


def test_chunk_text_short_text_yields_single_unsplit_chunk():
    text = "un deux trois"
    chunks = list(_chunk_text(text, max_words=10, overlap_words=2))
    assert chunks == [(text, 0)]


def test_chunk_text_exact_word_count_limit_not_split():
    text = "un deux trois"  # exactement 3 mots
    chunks = list(_chunk_text(text, max_words=3, overlap_words=1))
    assert chunks == [(text, 0)]


def test_chunk_text_long_text_creates_overlapping_windows():
    text = "a b c d e f g"  # 7 mots, 1 caractère chacun
    chunks = list(_chunk_text(text, max_words=3, overlap_words=1))

    assert chunks == [("a b c", 0), ("c d e", 4), ("e f g", 8)]
    # Le recouvrement (mot "c", "e") apparaît bien dans deux fenêtres
    # consécutives, et chaque chunk se recolle correctement au texte
    # original via son offset.
    for chunk_text, offset in chunks:
        assert text[offset : offset + len(chunk_text)] == chunk_text


# =============================================================================
# 4. _run_camembert
# =============================================================================


def test_run_camembert_filters_below_confidence_threshold():
    label_map = _build_label_map({"person": {"camembert_label": "PER"}})
    blocks = [NERBlock(lang="fra", text="Jean visite Paris", source="orig", container=None)]

    def fake_model(batch_texts):
        return [[
            {"entity_group": "PER", "score": 0.95, "start": 0, "end": 4},
            {"entity_group": "PER", "score": 0.10, "start": 12, "end": 17},
        ]]

    results = _run_camembert(blocks, fake_model, label_map, threshold=0.6)
    assert len(results) == 1
    assert len(results[0]) == 1
    assert results[0][0].text == "Jean"
    assert results[0][0].confidence == 0.95


def test_run_camembert_ignores_label_absent_from_map():
    label_map = _build_label_map({"person": {"camembert_label": "PER"}})
    blocks = [NERBlock(lang="fra", text="Jean visite Paris", source="orig", container=None)]

    def fake_model(batch_texts):
        return [[
            {"entity_group": "PER", "score": 0.9, "start": 0, "end": 4},
            {"entity_group": "MISC", "score": 0.99, "start": 12, "end": 17},  # pas dans label_map
        ]]

    results = _run_camembert(blocks, fake_model, label_map, threshold=0.5)
    assert len(results[0]) == 1
    assert results[0][0].label == "PER"


def test_run_camembert_falls_back_to_entity_key_when_no_entity_group():
    label_map = _build_label_map({"person": {"camembert_label": "PER"}})
    blocks = [NERBlock(lang="fra", text="Jean visite Paris", source="orig", container=None)]

    def fake_model(batch_texts):
        return [[{"entity": "PER", "score": 0.9, "start": 0, "end": 4}]]

    results = _run_camembert(blocks, fake_model, label_map, threshold=0.5)
    assert len(results[0]) == 1
    assert results[0][0].entity_type == "person"


def test_run_camembert_exception_in_one_batch_does_not_break_the_rest():
    label_map = _build_label_map({"person": {"camembert_label": "PER"}})
    blocks = [
        NERBlock(lang="fra", text="Jean visite Paris", source="orig", container=None),
        NERBlock(lang="fra", text="Ceci va planter", source="orig", container=None),
        NERBlock(lang="fra", text="Marie aime Lyon", source="orig", container=None),
    ]

    def fake_model(batch_texts):
        text = batch_texts[0]
        if text == blocks[1].text:
            raise RuntimeError("boom")
        if text == blocks[0].text:
            return [[{"entity_group": "PER", "score": 0.95, "start": 0, "end": 4}]]
        if text == blocks[2].text:
            return [[{"entity_group": "PER", "score": 0.80, "start": 0, "end": 5}]]
        raise AssertionError("unexpected batch content")

    # batch_size=1 force un appel modèle par bloc, donc une exception
    # isolée sur le bloc du milieu.
    results = _run_camembert(blocks, fake_model, label_map, threshold=0.6, batch_size=1)

    assert len(results) == 3
    assert [s.text for s in results[0]] == ["Jean"]
    assert results[1] == []  # bloc dont le batch a levé une exception
    assert [s.text for s in results[2]] == ["Marie"]


# =============================================================================
# 5. _run_gliner
# =============================================================================


class _FakeGliner:
    """Doublure de modèle GLiNER : .inference(texts, labels, threshold)."""

    def __init__(self, preds_by_text):
        self._preds_by_text = preds_by_text
        self.calls = []

    def inference(self, texts, labels, threshold=None):
        self.calls.append((list(texts), list(labels), threshold))
        return [self._preds_by_text.get(t, []) for t in texts]


def test_run_gliner_dedupes_entities_found_in_two_overlapping_windows():
    label_map = _build_label_map({"person": {"gliner_label": "person name"}})
    blocks = [NERBlock(lang="lat", text="a b c d e f g", source="orig", container=None)]

    # max_words=3, overlap_words=1 -> fenêtres ("a b c", 0), ("c d e", 4), ("e f g", 8)
    # (vérifié indépendamment dans les tests _chunk_text ci-dessus).
    # "c" (span global (4, 5)) apparaît dans les deux premières fenêtres avec
    # des scores différents -> une seule entité doit survivre, avec la
    # confiance la plus haute.
    preds_by_text = {
        "a b c": [{"label": "person name", "start": 4, "end": 5, "score": 0.5}],
        "c d e": [{"label": "person name", "start": 0, "end": 1, "score": 0.9}],
        "e f g": [{"label": "person name", "start": 0, "end": 1, "score": 0.99}],
    }
    model = _FakeGliner(preds_by_text)

    results = _run_gliner(
        blocks, model, ["person name"], label_map, threshold=0.3,
        batch_size=16, max_words=3, overlap_words=1,
    )

    assert len(results) == 1
    spans = results[0]
    # Deux entités distinctes seulement : le doublon "c" a été fusionné.
    assert len(spans) == 2
    by_span = {(s.start_char, s.end_char): s for s in spans}
    assert (4, 5) in by_span and (8, 9) in by_span
    kept_c = by_span[(4, 5)]
    assert kept_c.text == "c"
    assert kept_c.confidence == 0.9  # le score le plus haut des deux doublons
    assert by_span[(8, 9)].text == "e"
    # Résultats triés par position de départ.
    assert [s.start_char for s in spans] == sorted(s.start_char for s in spans)


def test_run_gliner_indexes_multiple_blocks_independently():
    label_map = _build_label_map({
        "person": {"gliner_label": "person name"},
        "organization": {"gliner_label": "organization"},
    })
    blocks = [
        NERBlock(lang="lat", text="a b c d e f g", source="orig", container=None),
        NERBlock(lang="lat", text="hello world", source="orig", container=None),
    ]
    preds_by_text = {
        "a b c": [],
        "c d e": [],
        "e f g": [],
        "hello world": [{"label": "organization", "start": 0, "end": 5, "score": 0.8}],
    }
    model = _FakeGliner(preds_by_text)

    results = _run_gliner(
        blocks, model, ["person name", "organization"], label_map, threshold=0.3,
        batch_size=16, max_words=3, overlap_words=1,
    )

    assert results[0] == []
    assert len(results[1]) == 1
    assert results[1][0].text == "hello"
    assert results[1][0].entity_type == "organization"


def test_run_gliner_ignores_label_absent_from_map():
    label_map = _build_label_map({"person": {"gliner_label": "person name"}})
    blocks = [NERBlock(lang="lat", text="Roma", source="orig", container=None)]
    model = _FakeGliner({"Roma": [{"label": "unknown label", "start": 0, "end": 4, "score": 0.9}]})

    results = _run_gliner(
        blocks, model, ["person name"], label_map, threshold=0.3,
        batch_size=16, max_words=50, overlap_words=5,
    )
    assert results[0] == []


def test_run_gliner_batch_exception_isolates_failing_chunk_only():
    label_map = _build_label_map({"person": {"gliner_label": "person name"}})
    blocks = [
        NERBlock(lang="lat", text="Marcus venit", source="orig", container=None),
        NERBlock(lang="lat", text="explode here", source="orig", container=None),
        NERBlock(lang="lat", text="Livia manet", source="orig", container=None),
    ]

    def inference(texts, labels, threshold=None):
        text = texts[0]
        if text == "explode here":
            raise RuntimeError("boom")
        if text == "Marcus venit":
            return [[{"label": "person name", "start": 0, "end": 6, "score": 0.9}]]
        if text == "Livia manet":
            return [[{"label": "person name", "start": 0, "end": 5, "score": 0.95}]]
        raise AssertionError("unexpected batch content")

    model = _FakeGliner({})
    model.inference = inference  # override pour lever une exception ciblée

    # batch_size=1 : un chunk par appel modèle, donc l'exception ne touche
    # que le chunk/bloc fautif — les deux autres blocs restent peuplés.
    # C'est le point à vérifier : l'exception n'avale PAS tout le lot de
    # blocs, seulement celui dont le batch a échoué.
    results = _run_gliner(
        blocks, model, ["person name"], label_map, threshold=0.3,
        batch_size=1, max_words=50, overlap_words=5,
    )

    assert [s.text for s in results[0]] == ["Marcus"]
    assert results[1] == []
    assert [s.text for s in results[2]] == ["Livia"]


# =============================================================================
# 6. detect_entities — routage, filtrage de longueur, titre + filter_spans
# =============================================================================


def _models_config():
    return {
        "camembert": {"languages": ["fra"], "batch_size": 32},
        "gliner": {"batch_size": 16, "max_words": 240, "overlap_words": 30},
    }


def _entity_types_config():
    return {
        "person": {"camembert_label": "PER", "gliner_label": "person name"},
        "place": {"camembert_label": "LOC", "gliner_label": "place name"},
    }


class _FakeModels:
    """Doublure de NERModels : porte juste .camembert et .gliner."""

    def __init__(self, camembert=None, gliner=None):
        self.camembert = camembert
        self.gliner = gliner


def test_detect_entities_routes_by_language_and_source():
    blocks = [
        NERBlock(lang="fra", text="Jean aime Paris", source="orig", container=None),   # -> CamemBERT
        NERBlock(lang="lat", text="Roma pulchra est", source="orig", container=None),  # -> GLiNER (langue)
        NERBlock(lang="fra", text="Jean aime Paris.", source="reg", container=None),   # -> GLiNER (source reg)
    ]

    cam_calls = []

    def fake_camembert(batch_texts):
        cam_calls.append(list(batch_texts))
        out = []
        for t in batch_texts:
            if t == "Jean aime Paris":
                out.append([{"entity_group": "PER", "score": 0.9, "start": 0, "end": 4}])
            else:
                out.append([])
        return out

    gli_texts_seen = []

    class Gliner:
        def inference(self, texts, labels, threshold=None):
            gli_texts_seen.extend(texts)
            out = []
            for t in texts:
                if t == "Roma pulchra est":
                    out.append([{"label": "place name", "start": 0, "end": 4, "score": 0.9}])
                else:
                    out.append([])
            return out

    models = _FakeModels(camembert=fake_camembert, gliner=Gliner())
    results = detect_entities(
        blocks, models, _entity_types_config(), _models_config(), threshold=0.5, root=None,
    )

    assert len(results) == 3
    assert [s.text for s in results[0]] == ["Jean"]
    assert results[0][0].model == "camembert"
    assert [s.text for s in results[1]] == ["Roma"]
    assert results[1][0].model == "gliner"
    assert results[2] == []

    # Le bloc français en source "orig" seul a été envoyé à CamemBERT ;
    # le bloc latin et le bloc "reg" sont partis vers GLiNER.
    assert cam_calls == [["Jean aime Paris"]]
    assert set(gli_texts_seen) == {"Roma pulchra est", "Jean aime Paris."}


def test_detect_entities_skips_blocks_shorter_than_minimum_length():
    blocks = [
        NERBlock(lang="fra", text="Hi", source="raw", container=None),  # < 10 caractères
        NERBlock(lang="fra", text="Jean visite Paris", source="orig", container=None),
    ]

    cam_texts_seen = []

    def fake_camembert(batch_texts):
        cam_texts_seen.extend(batch_texts)
        return [[{"entity_group": "PER", "score": 0.9, "start": 0, "end": 4}] for _ in batch_texts]

    models = _FakeModels(camembert=fake_camembert, gliner=_FakeGliner({}))
    results = detect_entities(
        blocks, models, _entity_types_config(), _models_config(), threshold=0.5, root=None,
    )

    assert results[0] == []  # bloc trop court, jamais envoyé à un modèle
    assert [s.text for s in results[1]] == ["Jean"]
    assert "Hi" not in cam_texts_seen


def test_detect_entities_filters_spans_matching_document_title():
    xml = f"""
    <TEI xmlns="{NS_TEI_URI}">
      <teiHeader><fileDesc><titleStmt><title>Paris</title></titleStmt></fileDesc></teiHeader>
      <text><body><ab xml:lang="fra">x</ab></body></text>
    </TEI>
    """
    root = etree.fromstring(xml.encode())
    blocks = [NERBlock(lang="fra", text="Jean aime Paris", source="orig", container=None)]

    def fake_camembert(batch_texts):
        return [[
            {"entity_group": "PER", "score": 0.9, "start": 0, "end": 4},   # "Jean"
            {"entity_group": "LOC", "score": 0.9, "start": 10, "end": 15},  # "Paris" == titre
        ]]

    models = _FakeModels(camembert=fake_camembert, gliner=_FakeGliner({}))
    results = detect_entities(
        blocks, models, _entity_types_config(), _models_config(), threshold=0.5, root=root,
    )

    # "Paris" correspond exactement au titre du document -> filtré par
    # filter_spans(title="Paris") ; "Jean" survit.
    assert [s.text for s in results[0]] == ["Jean"]


def test_detect_entities_empty_blocks_returns_empty_list_without_touching_models():
    results = detect_entities([], None, {}, {}, threshold=0.5, root=None)
    assert results == []
