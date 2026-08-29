# Run: venv/bin/python -m pytest tests/test_metadata_sources.py -q
#
# Targets: src/metadata/iiif.py (IIIFMapping) and src/metadata/csv_person.py
# (PersonDatabase + module-level singleton helpers). All fixtures are
# synthetic CSVs built under tmp_path - the real metadata_livre.csv /
# metadata_personne.csv are gitignored and absent from a fresh clone, so
# nothing here depends on them.
from pathlib import Path

import pytest

import config
from src.metadata.iiif import IIIFMapping
import src.metadata.csv_person as csv_person_module
from src.metadata.csv_person import (
    PersonDatabase,
    load_person_database,
    get_person_database,
)

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def reset_person_db_singleton():
    """src.metadata.csv_person._person_db is a module-level singleton.
    Reset it before AND after every test in this file so we never leak
    state into (or pick up state left by) other test modules such as
    tests/test_person_ids.py, which loads the real METADATA_PERSON_CSV
    indirectly through override_teiheader_from_csv()."""
    csv_person_module._person_db = None
    yield
    csv_person_module._person_db = None


# =============================================================================
# IIIFMapping.load_from_csv / get_url / has_mapping / count
# =============================================================================


def _write_iiif_csv(tmp_path, rows, name="mapping.csv"):
    path = tmp_path / name
    lines = [",".join(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def test_load_from_csv_stores_names_with_and_without_xml_and_skips_nan_url(tmp_path):
    csv_path = _write_iiif_csv(
        tmp_path,
        [
            ("https://iiif.example.org/vol/f1", "src1", "f1.xml"),
            ("https://iiif.example.org/vol/f2", "src1", "f2"),
            ("", "src1", "f3.xml"),  # empty URL -> read back as "nan" -> skipped
        ],
    )
    mapping = IIIFMapping()
    ok = mapping.load_from_csv(csv_path)

    assert ok is True
    # 2 valid rows, each stored under base name + ".xml" name -> 4 keys, count() == 2
    assert mapping.count() == 2
    assert mapping.has_mapping() is True
    assert mapping.mapping["f1"] == "https://iiif.example.org/vol/f1"
    assert mapping.mapping["f1.xml"] == "https://iiif.example.org/vol/f1"
    assert mapping.mapping["f2"] == "https://iiif.example.org/vol/f2"
    assert mapping.mapping["f2.xml"] == "https://iiif.example.org/vol/f2"
    # the "nan" URL row must not have produced any key
    assert "f3" not in mapping.mapping
    assert "f3.xml" not in mapping.mapping


def test_load_from_csv_missing_file_returns_false(tmp_path):
    mapping = IIIFMapping()
    ok = mapping.load_from_csv(tmp_path / "does_not_exist.csv")
    assert ok is False
    assert mapping.has_mapping() is False
    assert mapping.count() == 0


def test_load_from_csv_too_few_columns_returns_false(tmp_path):
    csv_path = _write_iiif_csv(tmp_path, [("url1", "src1"), ("url2", "src2")])
    mapping = IIIFMapping()
    ok = mapping.load_from_csv(csv_path)
    assert ok is False
    assert mapping.has_mapping() is False


def test_load_from_csv_unreadable_path_returns_false_via_exception(tmp_path):
    # A directory "exists" but pandas can't read it as a CSV -> exercises
    # the except branch and returns False instead of raising.
    mapping = IIIFMapping()
    ok = mapping.load_from_csv(tmp_path)
    assert ok is False


def test_get_url_exact_match_and_extension_forms_and_missing(tmp_path):
    csv_path = _write_iiif_csv(
        tmp_path,
        [
            ("https://iiif.example.org/vol/f1", "src1", "f1.xml"),
            ("https://iiif.example.org/vol/f2", "src1", "f2"),
        ],
    )
    mapping = IIIFMapping()
    mapping.load_from_csv(csv_path)

    assert mapping.get_url("f1.xml") == "https://iiif.example.org/vol/f1"
    assert mapping.get_url("f1") == "https://iiif.example.org/vol/f1"
    assert mapping.get_url("f2") == "https://iiif.example.org/vol/f2"
    assert mapping.get_url("f2.xml") == "https://iiif.example.org/vol/f2"
    assert mapping.get_url("unknown.xml") is None
    assert mapping.get_url("f3") is None


def test_has_mapping_and_count_on_fresh_empty_instance():
    mapping = IIIFMapping()
    assert mapping.has_mapping() is False
    assert mapping.count() == 0
    assert mapping.get_url("anything") is None


# =============================================================================
# IIIFMapping.detect_csv
# =============================================================================


def _write_detect_csv(tmp_path, name, rows):
    path = tmp_path / name
    lines = [",".join(row) for row in rows]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _alto_files(*names):
    return [Path(n) for n in names]


def test_detect_csv_returns_none_when_filename_matches_no_pattern(tmp_path):
    # "data.csv" contains none of iiif/mapping/manifest -> not even a candidate,
    # regardless of how well its content would match.
    _write_detect_csv(
        tmp_path,
        "data.csv",
        [("url", "src", "f1.xml")] * 5,
    )
    alto_files = _alto_files("f1.xml")
    assert IIIFMapping.detect_csv(tmp_path, alto_files) is None


def test_detect_csv_above_threshold_is_selected(tmp_path):
    alto_files = _alto_files("f1.xml", "f2.xml", "f3.xml", "f4.xml")
    rows = [("url", "src", n) for n in ["f1.xml", "f2.xml", "f3.xml", "f4.xml"]]
    rows += [("url", "src", n) for n in ["g1.xml", "g2.xml", "g3.xml", "g4.xml", "g5.xml", "g6.xml"]]
    assert len(rows) == 10  # 4/10 = 0.4 > IIIF_CSV_MIN_MATCH_RATE (0.3)
    csv_path = _write_detect_csv(tmp_path, "vol_iiif.csv", rows)

    result = IIIFMapping.detect_csv(tmp_path, alto_files)
    assert result == csv_path


def test_detect_csv_below_threshold_returns_none_silently(tmp_path):
    """The riskiest behaviour in scope: an ambiguous CSV (below
    IIIF_CSV_MIN_MATCH_RATE) is rejected with no error and no log visible
    to the caller - the document just loses all its IIIF links."""
    alto_files = _alto_files("f1.xml", "f2.xml", "f3.xml", "f4.xml")
    rows = [("url", "src", n) for n in ["f1.xml", "f2.xml"]]  # only 2 matches
    rows += [("url", "src", n) for n in [f"g{i}.xml" for i in range(8)]]
    assert len(rows) == 10  # 2/10 = 0.2 < IIIF_CSV_MIN_MATCH_RATE (0.3)
    _write_detect_csv(tmp_path, "vol_iiif.csv", rows)

    result = IIIFMapping.detect_csv(tmp_path, alto_files)
    assert result is None


def test_detect_csv_prefers_iiif_named_file_among_valid_candidates(tmp_path):
    alto_files = _alto_files("f1.xml", "f2.xml", "f3.xml", "f4.xml")
    matching_rows = [("url", "src", n) for n in ["f1.xml", "f2.xml", "f3.xml", "f4.xml"]]
    filler_rows = [("url", "src", n) for n in ["g1.xml", "g2.xml", "g3.xml", "g4.xml", "g5.xml", "g6.xml"]]
    rows = matching_rows + filler_rows

    # "a_mapping.csv" sorts alphabetically first but has no "iiif" in its name;
    # "z_iiif_map.csv" sorts alphabetically last but must still win because
    # detect_csv ranks "iiif"-named candidates ahead of everything else.
    _write_detect_csv(tmp_path, "a_mapping.csv", rows)
    iiif_named = _write_detect_csv(tmp_path, "z_iiif_map.csv", rows)

    result = IIIFMapping.detect_csv(tmp_path, alto_files)
    assert result == iiif_named


def test_detect_csv_skips_candidate_with_too_few_columns(tmp_path):
    path = tmp_path / "x_manifest.csv"
    path.write_text("url1,src1\nurl2,src2\n", encoding="utf-8")
    alto_files = _alto_files("f1.xml")
    assert IIIFMapping.detect_csv(tmp_path, alto_files) is None


def test_detect_csv_skips_candidate_larger_than_max_size(tmp_path, monkeypatch):
    import src.metadata.iiif as iiif_module

    alto_files = _alto_files("f1.xml")
    rows = [("url", "src", "f1.xml")] * 3
    _write_detect_csv(tmp_path, "big_iiif.csv", rows)

    # Force the size gate to reject any file, regardless of real size, to
    # exercise the "skip very large files" branch deterministically.
    monkeypatch.setattr(iiif_module, "IIIF_CSV_MAX_SIZE", 0)
    assert IIIFMapping.detect_csv(tmp_path, alto_files) is None


def test_detect_csv_swallows_exception_from_unreadable_candidate(tmp_path):
    # A directory whose name matches the glob pattern: pandas can't read it,
    # detect_csv must catch the error and keep going (returning None here
    # since there is no other candidate), not raise.
    (tmp_path / "broken_iiif.csv").mkdir()
    alto_files = _alto_files("f1.xml")
    assert IIIFMapping.detect_csv(tmp_path, alto_files) is None


# =============================================================================
# Audit §4.6 - is config.IIIF_URI actually wired into the pipeline?
# =============================================================================


def test_iiif_uri_dict_keys_are_never_read_by_the_pipeline():
    """config.IIIF_URI ships 5 keys (scheme, server, manifest_prefix,
    manifest_suffix, image_prefix). Tracing every consumer of the config
    dict handed down from main.build_config()/main.py's per-document
    override (config["iiifURI"] = dict(IIIF_URI, image_base=...)) shows
    only two keys are ever read out of it: "image_base" (computed
    separately by main._gallica_image_base() via a Gallica-specific regex
    on the manifest URL - NOT derived from IIIF_URI's own keys) and
    "view_number". Editing IIIF_URI's 5 keys in config.py therefore has
    zero observable effect on pipeline output. This test fails the day
    someone actually wires one of them in, which is the point."""
    assert set(config.IIIF_URI) == {
        "scheme",
        "server",
        "manifest_prefix",
        "manifest_suffix",
        "image_prefix",
    }

    # src/metadata/iiif.py (this test file's own target) never imports it either.
    iiif_source = (REPO_ROOT / "src" / "metadata" / "iiif.py").read_text(encoding="utf-8")
    assert "IIIF_URI" not in iiif_source

    consumer_sources = "".join(
        (REPO_ROOT / rel).read_text(encoding="utf-8")
        for rel in (
            "main.py",
            "src/tei.py",
            "src/sourcedoc/builder.py",
            "src/sourcedoc/attributes.py",
            "src/sourcedoc/elements.py",
        )
    )
    for key in config.IIIF_URI:
        # Look for the key used as an actual mapping lookup (quoted string
        # literal), not as an English word inside a comment/docstring
        # (e.g. "servers" in a comment would otherwise false-positive).
        assert f'"{key}"' not in consumer_sources, key
        assert f"'{key}'" not in consumer_sources, key


# =============================================================================
# PersonDatabase.load / _build_index / _parse_person_row
# =============================================================================

PERSON_CSV_HEADER = "BDD;Nom;Prenoms;ISNI;Label_categ"
PERSON_CSV_ROWS = [
    "PERS0001;Dupont;Jean;123456789;AUT|EDIT",
    "PERS0002;Martin;Claude;;LIBR",
    ";Nobody;Ghost;;XYZ",  # blank BDD -> skipped
    "PERS0004;;;;XYZ",  # no name, unmapped role code -> lowercased fallback
    "PERS0005;Rien; ;;",  # Prenoms is a single space (not NaN) -> safe_val's
    # "s == ''" branch after strip(); Label_categ empty -> safe_roles' "not raw" branch
]


def _write_person_csv(tmp_path, rows=None, header=PERSON_CSV_HEADER, name="metadata_personne.csv"):
    rows = PERSON_CSV_ROWS if rows is None else rows
    path = tmp_path / name
    content = "\n".join([header] + rows) + "\n"
    path.write_text(content, encoding="utf-8")
    return path


def test_load_missing_file_returns_false(tmp_path):
    db = PersonDatabase()
    assert db.load(tmp_path / "nope.csv") is False
    assert len(db) == 0


def test_load_unreadable_path_returns_false_via_exception(tmp_path):
    db = PersonDatabase()
    # tmp_path itself is a directory: exists() is True, but pd.read_csv on
    # a directory raises -> exercises the except branch.
    assert db.load(tmp_path) is False


def test_load_parses_isni_dot_zero_and_pipe_separated_roles(tmp_path):
    """Un artefact float LITTERAL dans le fichier ("123456789.0", trace d'un
    aller-retour tableur) est rogne. Pandas n'en cree plus lui-meme depuis
    dtype=str -- la ligne PERS0099 le stocke donc explicitement."""
    csv_path = _write_person_csv(
        tmp_path,
        rows=PERSON_CSV_ROWS + ["PERS0099;Tableur;Excel;123456789.0;AUT"],
    )
    db = PersonDatabase()
    assert db.load(csv_path) is True

    person = db.get("PERS0099")
    assert person is not None
    assert person["isni"] == "123456789"

    assert db.get("PERS0001")["roles"] == ["author", "editor"]


def test_load_keeps_literal_na_cells(tmp_path):
    """dtype=str seul ne suffit pas : sans keep_default_na=False, une cellule
    contenant litteralement 'NA' ou 'None' devient NaN et la valeur (voire la
    personne entiere quand c'est la colonne BDD) disparait en silence."""
    csv_path = _write_person_csv(
        tmp_path,
        rows=["PERS0010;NA;Jean;0000000123456789;AUT"],
    )
    db = PersonDatabase()
    assert db.load(csv_path) is True

    person = db.get("PERS0010")
    assert person is not None
    assert person["surname"] == "NA"
    assert person["isni"] == "0000000123456789"


def test_load_skips_blank_bdd_row(tmp_path):
    csv_path = _write_person_csv(tmp_path)
    db = PersonDatabase()
    db.load(csv_path)
    # 5 CSV rows, but the blank-BDD ("Ghost") row must not be indexed.
    assert len(db) == 4
    assert set(db) == {"PERS0001", "PERS0002", "PERS0004", "PERS0005"}


def test_load_maps_unmapped_role_code_to_lowercase_fallback(tmp_path):
    csv_path = _write_person_csv(tmp_path)
    db = PersonDatabase()
    db.load(csv_path)
    assert db.get("PERS0004")["roles"] == ["xyz"]


def test_load_blank_field_after_strip_and_blank_roles_return_none_and_empty_list(tmp_path):
    """A field that is a single space (not an actual pandas NaN) must still
    resolve to None via safe_val's post-strip empty-string check, and an
    empty Label_categ must yield an empty roles list rather than raising."""
    csv_path = _write_person_csv(tmp_path)
    db = PersonDatabase()
    db.load(csv_path)
    person = db.get("PERS0005")
    assert person["forename"] is None
    assert person["roles"] == []


def test_load_fails_loudly_when_bdd_column_missing(tmp_path):
    """Un CSV sans colonne BDD (mauvais delimiteur, en-tetes renommes) est
    traite comme un echec de chargement, pas comme une base vide valide :
    load() renvoyait True en silence et l'operateur ne voyait rien."""
    path = tmp_path / "no_bdd.csv"
    path.write_text("Nom;Prenoms\nDupont;Jean\n", encoding="utf-8")
    db = PersonDatabase()
    assert db.load(path) is False
    assert len(db) == 0
    assert bool(db) is False


def test_contains_and_len(tmp_path):
    csv_path = _write_person_csv(tmp_path)
    db = PersonDatabase(csv_path)
    assert len(db) == 4
    assert "PERS0001" in db
    assert "PERS9999" not in db


# =============================================================================
# PersonDatabase.get / get_display_name
# =============================================================================


def test_get_returns_none_for_falsy_or_unknown_id(tmp_path):
    db = PersonDatabase(_write_person_csv(tmp_path))
    assert db.get(None) is None
    assert db.get("") is None
    assert db.get("PERS9999") is None


def test_get_display_name_found_and_unknown_id_fallback(tmp_path):
    db = PersonDatabase(_write_person_csv(tmp_path))
    assert db.get_display_name("PERS0001") == "Jean Dupont"
    assert db.get_display_name("PERS9999") == "PERS9999"


def test_get_display_name_found_but_no_name_parts_falls_back_to_id(tmp_path):
    db = PersonDatabase(_write_person_csv(tmp_path))
    # PERS0004 exists in the DB but has no forename/surname.
    assert db.get_display_name("PERS0004") == "PERS0004"


# =============================================================================
# PersonDatabase.enrich_author_data
# =============================================================================


def test_enrich_author_data_found_populates_fields_and_defaults_role(tmp_path):
    db = PersonDatabase(_write_person_csv(tmp_path))
    data = db.enrich_author_data("PERS0001")

    assert data["xmlid"] == "PERS0001"
    assert data["name"] == "Jean Dupont"
    assert data["forename"] == "Jean"
    assert data["surname"] == "Dupont"
    assert data["isni"] == "123456789"
    # no role override -> defaults to the first parsed role
    assert data["role"] == "author"


def test_enrich_author_data_role_override_takes_precedence(tmp_path):
    db = PersonDatabase(_write_person_csv(tmp_path))
    data = db.enrich_author_data("PERS0001", role="printer")
    assert data["role"] == "printer"


def test_enrich_author_data_missing_id_falls_back_without_exception(tmp_path):
    db = PersonDatabase(_write_person_csv(tmp_path))
    data = db.enrich_author_data("PERS9999", role="author")

    assert data == {
        "xmlid": "PERS9999",
        "name": "PERS9999",
        "forename": None,
        "surname": None,
        "namelink": None,
        "isni": None,
        "ark": None,
        "birth_date": None,
        "death_date": None,
        "role": "author",
    }


# =============================================================================
# load_person_database() / get_person_database() module-level singleton
# =============================================================================


def test_get_person_database_is_none_before_any_load():
    assert get_person_database() is None


def test_load_person_database_singleton_ignores_later_csv_paths(tmp_path):
    """Surprising behaviour worth flagging: once the singleton is set,
    load_person_database() short-circuits on `if _person_db is None` and
    silently ignores any different csv_path passed on a later call."""
    csv1 = _write_person_csv(tmp_path, name="p1.csv")
    csv2 = _write_person_csv(
        tmp_path, rows=["PERS0099;Autre;Personne;;AUT"], name="p2.csv"
    )

    db1 = load_person_database(csv1)
    assert get_person_database() is db1
    assert "PERS0001" in db1

    db2 = load_person_database(csv2)
    assert db2 is db1  # same instance, csv2 was never loaded
    assert "PERS0099" not in db2
    assert "PERS0001" in db2
