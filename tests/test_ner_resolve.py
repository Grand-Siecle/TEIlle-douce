# -----------------------------------------------------------
# Characterization tests for teille_douce/enrichment/ner_resolve.py (Phase 9).
# Run: venv/bin/python -m pytest tests/test_ner_resolve.py -q
#
# Scope: group_mentions, link_local, write_entity_csvs,
# inject_header_entities, inject_editorial_declaration, resolve_entities.
#
# add_refs_to_body already has dedicated coverage in
# tests/test_ner_dual_anchor.py (dual <orig>/<reg> anchoring, backfill,
# multi-line entities, non-adjacent <w> runs) — it is only exercised here
# indirectly, as one step of the end-to-end resolve_entities() test.
#
# Namespace note (audit 4.2): real body/header construction (src/body,
# teille_douce/teiheader/default.py) uses BARE tags. ner_resolve.py always injects
# TEI-NAMESPACED elements (via its local _tei()/_sub() helpers) into that
# bare tree. Test fixtures below mirror that mismatch on purpose; `qlocal()`
# strips whichever form is present so assertions survive it.
# -----------------------------------------------------------
import csv

import pytest
from lxml import etree

from teille_douce.enrichment.entity_schema import NER_ENTITY_TYPES
from teille_douce.constants import XML_ID
from teille_douce.enrichment.ner_align import AlignedEntity, inject_entities
from teille_douce.enrichment.ner_resolve import _make_xml_id
from teille_douce.enrichment.ner_resolve import (
    ResolvedEntity,
    group_mentions,
    inject_editorial_declaration,
    inject_header_entities,
    link_local,
    resolve_entities,
    write_entity_csvs,
)

CERT = {"low": 0.0, "medium": 0.6, "high": 0.85}


def qlocal(el):
    """Local name of an element, regardless of whether it is namespaced."""
    return etree.QName(el).localname


def _w(text, lemma=None):
    """A standalone bare <w> element, optionally carrying @lemma."""
    el = etree.Element("w")
    el.text = text
    if lemma is not None:
        el.set("lemma", lemma)
    return el


def _mention(entity_type, text, confidence, model="camembert"):
    """A real AlignedEntity with a single <w> anchor whose @lemma equals
    `text`, so `_get_canonical_name` resolves deterministically to `text`."""
    return AlignedEntity(
        entity_type=entity_type,
        text=text,
        confidence=confidence,
        model=model,
        w_elements=[_w(text, lemma=text)],
    )


class FakePersonDB:
    """Minimal stand-in for the real PersonDatabase (backed by the
    gitignored metadata_personne.csv, unavailable in a fresh clone).
    Exposes exactly the interface link_local() relies on."""

    def __init__(self, people):
        self._people = people  # {pid: {"forename": ..., "surname": ...}}

    def __len__(self):
        return len(self._people)

    def __iter__(self):
        return iter(self._people)

    def __contains__(self, pid):
        return pid in self._people

    def get(self, pid):
        return self._people.get(pid)


# =============================================================================
# 1. group_mentions
# =============================================================================


def test_group_mentions_groups_by_type_and_normalized_form():
    m1 = _mention("person", "Poussin", 0.7)
    m2 = _mention("person", "POUSSIN", 0.6)  # same normalized form, different case
    m3 = _mention("place", "Poussin", 0.7)  # same text, different type -> own group

    resolved = group_mentions([m1, m2, m3])

    assert len(resolved) == 2
    person_group = next(r for r in resolved if r.entity_type == "person")
    place_group = next(r for r in resolved if r.entity_type == "place")
    assert len(person_group.mentions) == 2
    assert len(place_group.mentions) == 1


def test_group_mentions_canonical_is_most_confident():
    m1 = _mention("person", "poussin", 0.4)
    m2 = _mention("person", "Poussin", 0.9)  # highest confidence -> wins
    m3 = _mention("person", "POUSSIN", 0.2)

    resolved = group_mentions([m1, m2, m3])

    assert len(resolved) == 1
    assert resolved[0].canonical_name == "Poussin"
    assert len(resolved[0].mentions) == 3


def test_group_mentions_falls_back_to_raw_text_without_w_elements():
    """No <w> anchors at all (raw-text / reg-only mention): canonical name
    falls back to the AlignedEntity's own `.text`."""
    m = AlignedEntity(
        entity_type="artwork", text="La Joconde", confidence=0.8,
        model="gliner", w_elements=[],
    )
    resolved = group_mentions([m])
    assert resolved[0].canonical_name == "La Joconde"


def test_group_mentions_word_falls_back_to_text_when_lemma_missing():
    """Per-word fallback: a <w> with no @lemma, or with a foreign-language
    sentinel lemma ("@latin"), contributes its raw .text instead."""
    w1 = _w("pinxit")  # no @lemma attribute at all
    w2 = _w("fecit", lemma="@latin")  # sentinel lemma, not a real lemma
    m = AlignedEntity(
        entity_type="technique", text="pinxit fecit", confidence=0.7,
        model="camembert", w_elements=[w1, w2],
    )
    resolved = group_mentions([m])
    assert resolved[0].canonical_name == "pinxit fecit"


# =============================================================================
# 2. link_local
# =============================================================================


def test_link_local_matches_full_name():
    db = FakePersonDB({"PERS0001": {"forename": "Nicolas", "surname": "Poussin"}})
    ent = ResolvedEntity(
        entity_type="person", canonical_name="Nicolas Poussin",
        xml_id="pers-a", mentions=[_mention("person", "Nicolas Poussin", 0.9)],
    )
    link_local([ent], db)
    assert ent.local_match == "PERS0001"


def test_link_local_matches_surname_only():
    db = FakePersonDB({"PERS0002": {"forename": "Nicolas", "surname": "Poussin"}})
    ent = ResolvedEntity(
        entity_type="person", canonical_name="Poussin",
        xml_id="pers-b", mentions=[],
    )
    link_local([ent], db)
    assert ent.local_match == "PERS0002"


def test_link_local_matches_surname_within_compound_name():
    db = FakePersonDB({"PERS0003": {"forename": "Charles", "surname": "Le Brun"}})
    ent = ResolvedEntity(
        entity_type="person", canonical_name="Charles Le Brun premier peintre du roi",
        xml_id="pers-c", mentions=[],
    )
    link_local([ent], db)
    assert ent.local_match == "PERS0003"


def test_link_local_no_match():
    db = FakePersonDB({"PERS0004": {"forename": "Nicolas", "surname": "Poussin"}})
    ent = ResolvedEntity(
        entity_type="person", canonical_name="Jean Rembrandt",
        xml_id="pers-d", mentions=[],
    )
    link_local([ent], db)
    assert ent.local_match is None


def test_link_local_empty_database_noop():
    ent = ResolvedEntity(
        entity_type="person", canonical_name="Poussin",
        xml_id="pers-e", mentions=[],
    )
    link_local([ent], FakePersonDB({}))
    assert ent.local_match is None


def test_link_local_none_database_noop():
    ent = ResolvedEntity(
        entity_type="person", canonical_name="Poussin",
        xml_id="pers-f", mentions=[],
    )
    link_local([ent], None)
    assert ent.local_match is None


def test_link_local_surname_word_order_not_checked():
    """Documented quirk, not a fix: `surname_in_name` only checks that every
    surname word individually appears somewhere among the canonical name's
    words — it does not require adjacency or matching order. A canonical
    name that merely contains the same words, rearranged and separated by
    other text, still matches. Real false-positive risk on common-word
    surnames."""
    db = FakePersonDB({"PERS0005": {"forename": "", "surname": "Le Brun"}})
    ent = ResolvedEntity(
        entity_type="person", canonical_name="Brun sur le pont",
        xml_id="pers-g", mentions=[],
    )
    link_local([ent], db)
    assert ent.local_match == "PERS0005"  # accepted as a match by current code


# =============================================================================
# 3. write_entity_csvs
# =============================================================================


def test_write_entity_csvs_writes_columns_and_rows(tmp_path):
    entities = [
        ResolvedEntity(
            entity_type="person", canonical_name="Nicolas Poussin", xml_id="pers-h",
            mentions=[_mention("person", "Poussin", 0.9)], local_match="PERS0001",
        ),
        ResolvedEntity(
            entity_type="person", canonical_name="Charles Le Brun", xml_id="pers-i",
            mentions=[_mention("person", "Le Brun", 0.8)],  # no local match
        ),
    ]

    write_entity_csvs(entities, NER_ENTITY_TYPES, tmp_path, "LIV0001_reconciled")

    csv_path = tmp_path / "LIV0001_reconciled" / "entities_persons.csv"
    assert csv_path.exists()
    with open(csv_path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter=";")
        rows = list(reader)
        assert reader.fieldnames == ["xml_id", "canonical_name", "mention_count", "local_id"]

    assert len(rows) == 2
    by_name = {r["canonical_name"]: r for r in rows}
    assert by_name["Nicolas Poussin"]["local_id"] == "PERS0001"
    assert by_name["Nicolas Poussin"]["mention_count"] == "1"
    assert by_name["Charles Le Brun"]["local_id"] == ""


def test_write_entity_csvs_skips_types_without_csv_file(tmp_path):
    """date/material/technique-with-no-csv configs must not crash and must
    not produce a file (config.NER_ENTITY_TYPES["date"]["csv_file"] is None)."""
    entities = [
        ResolvedEntity(
            entity_type="date", canonical_name="1659", xml_id="date-a",
            mentions=[_mention("date", "1659", 0.9)],
        ),
    ]
    write_entity_csvs(entities, NER_ENTITY_TYPES, tmp_path, "LIV0001_reconciled")
    assert list((tmp_path / "LIV0001_reconciled").iterdir()) == []


# =============================================================================
# 4. inject_header_entities
# =============================================================================


def _header_root():
    root = etree.Element("TEI")
    header = etree.SubElement(root, "teiHeader")
    return root, header


def test_every_inferred_list_lands_in_standoff():
    """Audit 1.9 : <particDesc> et <settingDesc> disent ce que l'editeur
    affirme du texte. Y ranger une liste devinee par un modele rendait
    les deux indistinguables — le header portait deux <listPerson>, une
    curee depuis le CSV et une inferee. Le standOff est l'endroit TEI de
    l'annotation detachee."""
    root, header = _header_root()
    person = ResolvedEntity(
        entity_type="person", canonical_name="Nicolas Poussin", xml_id="pers-j",
        mentions=[_mention("person", "Poussin", 0.9)],
    )
    place = ResolvedEntity(
        entity_type="place", canonical_name="Rome", xml_id="place-a",
        mentions=[_mention("place", "Rome", 0.9)],
    )

    inject_header_entities(root, [person, place], NER_ENTITY_TYPES)

    standoff = next(c for c in root if qlocal(c) == "standOff")
    # rien d'infere ne s'est glisse dans le profileDesc
    profile_desc = next((c for c in header if qlocal(c) == "profileDesc"), None)
    if profile_desc is not None:
        assert not [c for c in profile_desc if qlocal(c) in ("particDesc", "settingDesc")]

    list_person = next(c for c in standoff if qlocal(c) == "listPerson")
    assert list_person.get("source") == "#ner-auto"
    person_item = list_person[0]
    assert qlocal(person_item) == "person"
    assert person_item.get(XML_ID) == "pers-j"
    pers_name = next(c for c in person_item if qlocal(c) == "persName")
    assert pers_name.text == "Nicolas Poussin"

    list_place = next(c for c in standoff if qlocal(c) == "listPlace")
    place_item = list_place[0]
    assert place_item.get(XML_ID) == "place-a"
    place_name = next(c for c in place_item if qlocal(c) == "placeName")
    assert place_name.text == "Rome"


def test_inject_header_entities_second_pass_replaces_not_appends():
    """Audit 6.13, meme classe de reentree que la declaration editoriale :
    un second passage sur le meme arbre doit REMPLACER la liste auto
    (source='#ner-auto'), pas en ajouter une soeur dont les items
    reporteraient les memes xml:id — TEI invalide."""
    root, header = _header_root()
    person = ResolvedEntity(
        entity_type="person", canonical_name="Nicolas Poussin", xml_id="pers-j",
        mentions=[_mention("person", "Poussin", 0.9)],
    )

    inject_header_entities(root, [person], NER_ENTITY_TYPES)
    inject_header_entities(root, [person], NER_ENTITY_TYPES)

    standoff = next(c for c in root if qlocal(c) == "standOff")
    lists = [c for c in standoff if qlocal(c) == "listPerson"]
    assert len(lists) == 1, f"attendu une seule listPerson auto, obtenu {len(lists)}"

    ids = [el.get(XML_ID) for el in root.iter() if el.get(XML_ID)]
    assert len(ids) == len(set(ids)), "xml:id dupliques apres double passage"


def test_inject_header_entities_routes_standoff():
    root, header = _header_root()
    artwork = ResolvedEntity(
        entity_type="artwork", canonical_name="La Joconde", xml_id="artwork-a",
        mentions=[_mention("artwork", "La Joconde", 0.9)],
    )

    inject_header_entities(root, [artwork], NER_ENTITY_TYPES)

    stand_off = next(c for c in root if qlocal(c) == "standOff")
    list_object = next(c for c in stand_off if qlocal(c) == "listObject")
    object_item = list_object[0]
    assert object_item.get(XML_ID) == "artwork-a"
    # TEI exige le niveau <objectIdentifier> : sans lui, tout fichier
    # portant une entite oeuvre echouait a la validation tei_all.
    identifier = next(c for c in object_item if qlocal(c) == "objectIdentifier")
    object_name = next(c for c in identifier if qlocal(c) == "objectName")
    assert object_name.text == "La Joconde"


def test_inject_header_entities_event_uses_label_element():
    root, header = _header_root()
    event = ResolvedEntity(
        entity_type="event", canonical_name="Le Sac de Rome", xml_id="event-a",
        mentions=[_mention("event", "sac", 0.9)],
    )

    inject_header_entities(root, [event], NER_ENTITY_TYPES)

    stand_off = next(c for c in root if qlocal(c) == "standOff")
    list_event = next(c for c in stand_off if qlocal(c) == "listEvent")
    event_item = list_event[0]
    assert event_item.get(XML_ID) == "event-a"
    label = event_item[0]
    assert qlocal(label) == "label"
    assert label.text == "Le Sac de Rome"


def test_materials_get_the_target_list_they_lacked():
    """Audit 1.9 : sans liste cible, une annotation <material> ne pointait
    vers rien — deux occurrences de « marbre » restaient deux chaines sans
    lien. TEI n'a pas de <listMaterial> : une <list type="materials"> du
    standOff en tient lieu."""
    root, header = _header_root()
    material = ResolvedEntity(
        entity_type="material", canonical_name="huile sur toile", xml_id="mat-a",
        mentions=[_mention("material", "huile", 0.9)],
    )
    person = ResolvedEntity(
        entity_type="person", canonical_name="Nicolas Poussin", xml_id="pers-k",
        mentions=[_mention("person", "Poussin", 0.9)],
    )

    inject_header_entities(root, [material, person], NER_ENTITY_TYPES)

    standoff = next(c for c in root if qlocal(c) == "standOff")
    materials = [
        c for c in standoff
        if qlocal(c) == "list" and c.get("type") == "materials"
    ]
    assert len(materials) == 1
    item = materials[0][0]
    assert item.get(XML_ID) == "mat-a"
    assert "huile" in "".join(item.itertext())
    assert any(qlocal(c) == "listPerson" for c in standoff)


def test_a_type_without_a_target_list_is_still_skipped():
    """date reste sans liste : sa valeur EST son identite (@when), une
    entree de liste n'ajouterait rien a pointer."""
    root, header = _header_root()
    date = ResolvedEntity(
        entity_type="date", canonical_name="1659", xml_id="date-a",
        mentions=[_mention("date", "1659", 0.9)],
    )

    inject_header_entities(root, [date], NER_ENTITY_TYPES)

    assert not [e for e in root.iter() if e.get(XML_ID) == "date-a"]


# =============================================================================
# 5. inject_editorial_declaration
# =============================================================================


def _header_with_filedesc():
    root = etree.Element("TEI")
    header = etree.SubElement(root, "teiHeader")
    file_desc = etree.SubElement(header, "fileDesc")
    title_stmt = etree.SubElement(file_desc, "titleStmt")
    etree.SubElement(title_stmt, "title").text = "Test document"
    return root, file_desc


def test_inject_editorial_declaration_creates_edition_stmt_after_title_stmt():
    root, file_desc = _header_with_filedesc()

    inject_editorial_declaration(root)

    children_local = [qlocal(c) for c in file_desc]
    assert children_local == ["titleStmt", "editionStmt"]

    edition_stmt = file_desc[1]
    resp_stmts = [c for c in edition_stmt if qlocal(c) == "respStmt"]
    assert len(resp_stmts) == 1
    assert resp_stmts[0].get(XML_ID) == "ner-auto"
    resp = next(c for c in resp_stmts[0] if qlocal(c) == "resp")
    assert resp.text == "Automatic named entity recognition"


def test_inject_editorial_declaration_reuses_existing_edition_stmt_container():
    """A second call must not duplicate the <editionStmt> container or its
    methodology prose: exactly one <editionStmt> and one <edition> paragraph
    must exist regardless of how many times injection runs."""
    root, file_desc = _header_with_filedesc()

    inject_editorial_declaration(root)
    inject_editorial_declaration(root)

    edition_stmts = [c for c in file_desc if qlocal(c) == "editionStmt"]
    assert len(edition_stmts) == 1
    editions = [c for c in edition_stmts[0] if qlocal(c) == "edition"]
    assert len(editions) == 1


def test_inject_editorial_declaration_no_duplicate_resp_stmt_on_second_call():
    """Audit 6.13 : un second passage sur le meme arbre (reprise,
    retraitement partiel) ne doit pas dupliquer le respStmt ner-auto."""
    root, file_desc = _header_with_filedesc()

    inject_editorial_declaration(root)
    inject_editorial_declaration(root)

    edition_stmt = next(c for c in file_desc if qlocal(c) == "editionStmt")
    resp_stmts = [c for c in edition_stmt if qlocal(c) == "respStmt"]
    assert len(resp_stmts) == 1, f"expected exactly 1 respStmt after 2 calls, got {len(resp_stmts)}"


# =============================================================================
# 6. resolve_entities — end-to-end orchestration
# =============================================================================


def _sub(parent, tag, text=None, **attrs):
    el = etree.SubElement(parent, tag)
    if text is not None:
        el.text = text
    for k, v in attrs.items():
        el.set(k, v)
    return el


def _build_integration_tree():
    """A minimal TEI tree using the SAME bare-tag construction style as the
    real pipeline (teille_douce/teiheader/default.py, teille_douce/body/builder.py):
    profileDesc pre-exists as a bare container, exactly as DefaultTree
    leaves it — inject_header_entities must find and reuse it rather than
    creating a duplicate namespaced one."""
    root = etree.Element("TEI")
    header = _sub(root, "teiHeader")
    file_desc = _sub(header, "fileDesc")
    title_stmt = _sub(file_desc, "titleStmt")
    _sub(title_stmt, "title", text="Integration fixture")
    _sub(header, "profileDesc")
    _sub(header, "encodingDesc")

    text_el = _sub(root, "text")
    body = _sub(text_el, "body")

    ab1 = _sub(body, "ab")
    s1 = _sub(ab1, "s")
    w1 = _sub(s1, "w", text="Poussin", lemma="Poussin")

    ab2 = _sub(body, "ab")
    s2 = _sub(ab2, "s")
    w2 = _sub(s2, "w", text="Poussin", lemma="Poussin")

    ab3 = _sub(body, "ab")
    s3 = _sub(ab3, "s")
    w3 = _sub(s3, "w", text="Rome", lemma="Rome")

    return root, w1, w2, w3


def test_resolve_entities_end_to_end_integration(tmp_path):
    root, w1, w2, w3 = _build_integration_tree()

    # Two independent mentions of the same person (repeated detection),
    # one mention of a place.
    m1 = AlignedEntity(entity_type="person", text="Poussin", confidence=0.9,
                        model="camembert", w_elements=[w1])
    m2 = AlignedEntity(entity_type="person", text="Poussin", confidence=0.85,
                        model="camembert", w_elements=[w2])
    m3 = AlignedEntity(entity_type="place", text="Rome", confidence=0.95,
                        model="gliner", w_elements=[w3])
    aligned = [m1, m2, m3]

    # Phase 8 (already-injected wrappers, as they'd be by the time Phase 9 runs)
    inject_entities(aligned, CERT, NER_ENTITY_TYPES)

    person_db = FakePersonDB({"PERS0001": {"forename": "Nicolas", "surname": "Poussin"}})

    resolved = resolve_entities(
        root, aligned, NER_ENTITY_TYPES, person_db, tmp_path, "LIV0001_reconciled"
    )

    # --- grouping ---
    assert len(resolved) == 2
    person_ent = next(e for e in resolved if e.entity_type == "person")
    place_ent = next(e for e in resolved if e.entity_type == "place")
    assert len(person_ent.mentions) == 2
    assert person_ent.local_match == "PERS0001"

    # --- CSV written into the per-document subfolder (audit 2.4) ---
    doc_dir = tmp_path / "LIV0001_reconciled"
    with open(doc_dir / "entities_persons.csv", newline="", encoding="utf-8") as f:
        person_rows = list(csv.DictReader(f, delimiter=";"))
    assert person_rows[0]["canonical_name"] == person_ent.canonical_name
    assert person_rows[0]["local_id"] == "PERS0001"

    with open(doc_dir / "entities_places.csv", newline="", encoding="utf-8") as f:
        place_rows = list(csv.DictReader(f, delimiter=";"))
    assert place_rows[0]["canonical_name"] == "Rome"

    # --- standOff populated ---
    header = next(e for e in root.iter() if qlocal(e) == "teiHeader")
    standoff = next(c for c in root if qlocal(c) == "standOff")
    list_person = next(c for c in standoff if qlocal(c) == "listPerson")
    assert list_person[0].get(XML_ID) == person_ent.xml_id

    list_place = next(c for c in standoff if qlocal(c) == "listPlace")
    assert list_place[0].get(XML_ID) == place_ent.xml_id

    # editorial declaration injected exactly once
    file_desc = next(c for c in header if qlocal(c) == "fileDesc")
    edition_stmts = [c for c in file_desc if qlocal(c) == "editionStmt"]
    assert len(edition_stmts) == 1

    # --- @ref posed in body ---
    body = next(e for e in root.iter() if qlocal(e) == "body")
    pers_names = [e for e in body.iter() if qlocal(e) == "persName" and e.get("resp") == "#ner-auto"]
    assert len(pers_names) == 2
    assert all(p.get("ref") == f"#{person_ent.xml_id}" for p in pers_names)

    place_names = [e for e in body.iter() if qlocal(e) == "placeName" and e.get("resp") == "#ner-auto"]
    assert len(place_names) == 1
    assert place_names[0].get("ref") == f"#{place_ent.xml_id}"


# =============================================================================
# 7. Audit 2.4 — entity CSVs must survive across documents
# =============================================================================


def test_write_entity_csvs_preserves_entities_across_documents(tmp_path):
    """docs/rapport_audit.md #2.4: write_entity_csvs opens each CSV in mode
    "w" with a fixed filename, inside whatever output_dir it is given. In
    production that directory is the single global entities/ folder
    (config.NER_OUTPUT_DIR), passed unchanged for every document processed
    in the same run (see resolve_entities() call in main.py's per-document
    loop). Writing document 2's entities therefore truncates and replaces
    whatever document 1 just wrote, instead of accumulating alongside it —
    exactly what the downstream reconciliation repo cannot tolerate, since
    it needs the entities of ALL documents, not just the last one processed.

    Expected fix (per the audit): a per-document subfolder, or append mode
    plus a `document_id` column. Either way, entities from BOTH documents
    processed against the same shared output_dir must remain retrievable
    afterwards — which is exactly what this test checks, searching the
    whole output tree (rglob) so it is agnostic to which fix shape lands.
    """
    entity_types_config = {"person": {"csv_file": "entities_persons.csv"}}

    doc1_entity = ResolvedEntity(
        entity_type="person", canonical_name="Nicolas Poussin", xml_id="pers-doc1",
        mentions=[_mention("person", "Poussin", 0.9)],
    )
    doc2_entity = ResolvedEntity(
        entity_type="person", canonical_name="Charles Le Brun", xml_id="pers-doc2",
        mentions=[_mention("person", "Le Brun", 0.9)],
    )

    # Same shared output_dir for both calls, mirroring main.py invoking
    # resolve_entities(..., NER_OUTPUT_DIR, doc_name) once per document in one
    # run. Only the document name differs -- which is precisely what keeps the
    # two documents' CSVs apart.
    write_entity_csvs([doc1_entity], entity_types_config, tmp_path, "LIV0001_reconciled")
    write_entity_csvs([doc2_entity], entity_types_config, tmp_path, "LIV0002_reconciled")

    rows = []
    for csv_path in tmp_path.rglob("entities_persons.csv"):
        with open(csv_path, newline="", encoding="utf-8") as f:
            rows.extend(csv.DictReader(f, delimiter=";"))

    names = {row["canonical_name"] for row in rows}
    assert names == {"Nicolas Poussin", "Charles Le Brun"}, (
        f"entities from both documents must survive; got {names}"
    )


# =============================================================================
# Identifiants deterministes (audit 2.8)
# =============================================================================

def test_make_xml_id_is_deterministic_and_scoped():
    """Audit 2.8 : la meme entite (type + nom normalise) recoit le meme
    xml:id a chaque run — et deux entites distinctes des ids distincts."""
    a = _make_xml_id("person", "jean dupont")
    assert a == _make_xml_id("person", "jean dupont")
    assert a.startswith("pers-")
    assert a != _make_xml_id("place", "jean dupont")
    assert a != _make_xml_id("person", "jeanne dupont")


# =============================================================================
# Le chemin de retour : de l'entite aux passages qui la nomment (audit 1.9)
# =============================================================================

def test_link_mentions_writes_the_way_back():
    """Le corps pointait vers le standOff ; rien ne pointait en sens
    inverse, donc lire la liste d'entites obligeait a parcourir tout le
    texte pour savoir ou un nom apparait."""
    from teille_douce.enrichment.ner_resolve import link_mentions

    root = etree.fromstring(
        b'<TEI><text><body><div>'
        b'<ab><persName resp="#ner-auto" ref="#pers-a">Poussin</persName>'
        b' et <persName resp="#ner-auto" ref="#pers-a">le Poussin</persName>'
        b' a <placeName resp="#ner-auto" ref="#place-b">Rome</placeName></ab>'
        b'</div></body></text></TEI>'
    )

    assert link_mentions(root) == 2

    standoff = next(c for c in root if qlocal(c) == "standOff")
    link_grp = next(c for c in standoff if qlocal(c) == "linkGrp")
    assert link_grp.get("type") == "mentions"
    cibles = sorted(l.get("target") for l in link_grp)
    assert cibles == ["#pers-a #pers-a-m1 #pers-a-m2", "#place-b #place-b-m1"]

    # chaque mention porte l'identifiant par lequel on la designe
    mentions = [e for e in root.iter() if e.get("resp") == "#ner-auto"]
    assert [m.get(XML_ID) for m in mentions] == [
        "pers-a-m1", "pers-a-m2", "place-b-m1",
    ]


def test_link_mentions_rebuilds_rather_than_appends():
    from teille_douce.enrichment.ner_resolve import link_mentions

    root = etree.fromstring(
        b'<TEI><text><body><div><ab>'
        b'<persName resp="#ner-auto" ref="#pers-a">Poussin</persName>'
        b'</ab></div></body></text></TEI>'
    )
    link_mentions(root)
    link_mentions(root)

    standoff = next(c for c in root if qlocal(c) == "standOff")
    groupes = [c for c in standoff if qlocal(c) == "linkGrp"]
    assert len(groupes) == 1
    assert len(groupes[0]) == 1


def test_an_entity_carries_the_certainty_of_its_own_existence():
    """Un lecteur du standOff seul ne pouvait pas distinguer un nom lu une
    fois avec un score fragile d'un nom trouve trente fois."""
    root, header = _header_root()
    sure = ResolvedEntity(
        entity_type="person", canonical_name="Nicolas Poussin", xml_id="pers-sur",
        mentions=[_mention("person", "Poussin", 0.95), _mention("person", "Poussin", 0.4)],
    )
    fragile = ResolvedEntity(
        entity_type="person", canonical_name="Titi", xml_id="pers-fragile",
        mentions=[_mention("person", "Titi", 0.3)],
    )

    inject_header_entities(root, [sure, fragile], NER_ENTITY_TYPES)

    items = {
        el.get(XML_ID): el.get("cert")
        for el in root.iter() if qlocal(el) == "person"
    }
    assert items["pers-sur"] == "high"
    assert items["pers-fragile"] == "low"


def test_a_type_configured_under_the_profile_desc_still_lands_there():
    """Le standOff est le defaut des listes inferees, pas une contrainte :
    un type configure vers le profileDesc doit continuer d'y aller."""
    root, header = _header_root()
    config = dict(NER_ENTITY_TYPES)
    config["person"] = {**config["person"], "tei_parent": "particDesc"}
    person = ResolvedEntity(
        entity_type="person", canonical_name="Nicolas Poussin", xml_id="pers-p",
        mentions=[_mention("person", "Poussin", 0.9)],
    )

    inject_header_entities(root, [person], config)

    profile_desc = next(c for c in header if qlocal(c) == "profileDesc")
    partic = next(c for c in profile_desc if qlocal(c) == "particDesc")
    assert [qlocal(c) for c in partic] == ["listPerson"]


def test_link_mentions_on_a_document_without_annotations():
    from teille_douce.enrichment.ner_resolve import link_mentions

    sans_corps = etree.fromstring(b"<TEI><teiHeader/></TEI>")
    assert link_mentions(sans_corps) == 0

    sans_entites = etree.fromstring(
        b"<TEI><text><body><div><ab>texte nu</ab></div></body></text></TEI>"
    )
    assert link_mentions(sans_entites) == 0
    assert not [c for c in sans_entites if qlocal(c) == "standOff"]


def test_link_mentions_keeps_an_identifier_a_mention_already_has():
    """Les fragments d'une entite coupee par une ligne portent deja un
    xml:id, pose a l'ancrage : le lien doit designer celui-la."""
    from teille_douce.enrichment.ner_resolve import link_mentions

    root = etree.fromstring(
        b'<TEI><text><body><div><ab>'
        b'<persName xml:id="ent-deja" resp="#ner-auto" ref="#pers-a">Poussin</persName>'
        b'</ab></div></body></text></TEI>'
    )

    link_mentions(root)

    lien = next(c for c in root.iter() if qlocal(c) == "link")
    assert lien.get("target") == "#pers-a #ent-deja"


def test_a_mention_written_on_both_layers_counts_once():
    """L'ancrage double injecte la meme occurrence dans <orig> et dans
    <reg> : compter les elements donnerait deux passages la ou la page en
    porte un."""
    from teille_douce.enrichment.ner_resolve import link_mentions

    root = etree.fromstring(
        b'<TEI><text><body><div><ab><choice>'
        b'<orig><persName resp="#ner-auto" ref="#pers-a">Poussin</persName></orig>'
        b'<reg><persName resp="#ner-auto" ref="#pers-a">Poussin</persName></reg>'
        b'</choice></ab></div></body></text></TEI>'
    )

    link_mentions(root)

    lien = next(c for c in root.iter() if qlocal(c) == "link")
    assert lien.get("target") == "#pers-a #pers-a-m1"


def test_a_mention_cut_by_a_line_break_counts_once():
    """Une mention coupee par une ligne donne une enveloppe par fragment,
    chainees par @next/@prev : c'est un passage, pas deux."""
    from teille_douce.enrichment.ner_resolve import link_mentions

    root = etree.fromstring(
        b'<TEI><text><body><div><ab>'
        b'<persName xml:id="e1" resp="#ner-auto" ref="#pers-a" next="#e2">Pous</persName>'
        b'<lb/>'
        b'<persName xml:id="e2" resp="#ner-auto" ref="#pers-a" prev="#e1">sin</persName>'
        b'</ab></div></body></text></TEI>'
    )

    link_mentions(root)

    lien = next(c for c in root.iter() if qlocal(c) == "link")
    assert lien.get("target") == "#pers-a #e1"


def test_a_ref_pointer_list_does_not_leak_into_an_xml_id():
    """@ref est une liste de pointeurs en TEI, et csv_book en ecrit
    d'externes (geonames) a cote des notres."""
    from teille_douce.enrichment.ner_resolve import link_mentions

    root = etree.fromstring(
        b'<TEI><text><body><div><ab>'
        b'<placeName resp="#ner-auto" ref="#place-a https://geonames.org/1">Rome</placeName>'
        b'</ab></div></body></text></TEI>'
    )

    link_mentions(root)

    mention = next(e for e in root.iter() if e.get("resp") == "#ner-auto")
    assert mention.get(XML_ID) == "place-a-m1"
    assert " " not in mention.get(XML_ID)


def test_the_materials_and_techniques_lists_do_not_delete_each_other():
    """Les deux sont une <list> nue, distinguees par @type seul : matcher
    sur le nom d'element supprimait celle qu'on venait de construire, et
    laissait les @ref de ses entites pointer vers rien."""
    root, header = _header_root()
    material = ResolvedEntity(
        entity_type="material", canonical_name="huile", xml_id="mat-a",
        mentions=[_mention("material", "huile", 0.9)],
    )
    technique = ResolvedEntity(
        entity_type="technique", canonical_name="glacis", xml_id="tech-a",
        mentions=[_mention("technique", "glacis", 0.9)],
    )

    inject_header_entities(root, [material, technique], NER_ENTITY_TYPES)
    inject_header_entities(root, [material, technique], NER_ENTITY_TYPES)

    standoff = next(c for c in root if qlocal(c) == "standOff")
    listes = {c.get("type"): c for c in standoff if qlocal(c) == "list"}
    assert set(listes) == {"materials", "techniques"}
    assert [el.get(XML_ID) for el in listes["materials"]] == ["mat-a"]
    assert [el.get(XML_ID) for el in listes["techniques"]] == ["tech-a"]


def test_the_open_vocabularies_are_declared_where_they_are_used():
    """<list type="materials"> nommait un vocabulaire que rien ne
    decrivait : un lecteur ne pouvait pas distinguer un terme infere d'un
    descripteur controle."""
    root, header = _header_root()
    etree.SubElement(header, "encodingDesc")
    material = ResolvedEntity(
        entity_type="material", canonical_name="huile", xml_id="mat-a",
        mentions=[_mention("material", "huile", 0.9)],
    )

    inject_header_entities(root, [material], NER_ENTITY_TYPES)

    taxonomies = [e for e in root.iter() if qlocal(e) == "taxonomy"]
    assert [t.get(XML_ID) for t in taxonomies] == ["art-vocabulary"]
    categories = [c for c in taxonomies[0] if qlocal(c) == "category"]
    assert [c.get(XML_ID) for c in categories] == ["materials"]
    assert "ouvert" in categories[0][0].text
    # la liste designe la categorie qui la decrit
    liste = next(e for e in root.iter() if qlocal(e) == "list")
    assert liste.get("ana") == "#materials"
    # rien n'est declare pour une categorie que le document ne porte pas
    assert "techniques" not in [c.get(XML_ID) for c in categories]


def test_declaring_a_vocabulary_twice_writes_it_once():
    root, header = _header_root()
    etree.SubElement(header, "encodingDesc")
    material = ResolvedEntity(
        entity_type="material", canonical_name="huile", xml_id="mat-a",
        mentions=[_mention("material", "huile", 0.9)],
    )
    technique = ResolvedEntity(
        entity_type="technique", canonical_name="glacis", xml_id="tech-a",
        mentions=[_mention("technique", "glacis", 0.9)],
    )

    inject_header_entities(root, [material, technique], NER_ENTITY_TYPES)
    inject_header_entities(root, [material, technique], NER_ENTITY_TYPES)

    taxonomies = [e for e in root.iter() if qlocal(e) == "taxonomy"]
    assert len(taxonomies) == 1
    categories = [c.get(XML_ID) for c in taxonomies[0] if qlocal(c) == "category"]
    assert sorted(categories) == ["materials", "techniques"]


def test_a_vocabulary_cannot_be_declared_without_an_encoding_desc():
    """Le header d'un arbre construit a la main peut ne pas en avoir :
    la liste doit sortir quand meme, sans declaration."""
    root, header = _header_root()
    material = ResolvedEntity(
        entity_type="material", canonical_name="huile", xml_id="mat-a",
        mentions=[_mention("material", "huile", 0.9)],
    )

    inject_header_entities(root, [material], NER_ENTITY_TYPES)

    assert not [e for e in root.iter() if qlocal(e) == "taxonomy"]
    assert [e for e in root.iter() if qlocal(e) == "list"]


def test_an_unknown_vocabulary_pointer_declares_nothing():
    from teille_douce.enrichment.ner_resolve import _declare_vocabulary

    root, header = _header_root()
    etree.SubElement(header, "encodingDesc")
    assert _declare_vocabulary(root, None) is None
    assert _declare_vocabulary(root, "#inconnu") is None


def test_inject_header_entities_without_a_header_does_nothing():
    root = etree.Element("TEI")
    person = ResolvedEntity(
        entity_type="person", canonical_name="Poussin", xml_id="pers-z",
        mentions=[],
    )
    inject_header_entities(root, [person], NER_ENTITY_TYPES)
    assert len(root) == 0


def test_resolve_entities_without_entities_returns_early(tmp_path):
    assert resolve_entities(
        etree.Element("TEI"), [], NER_ENTITY_TYPES, None, tmp_path, "LIV0001"
    ) == []
