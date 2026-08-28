"""
Tests de non-regression des constats du rapport d'audit (docs/rapport_audit.md, section 5).

Chaque test decrit le comportement attendu APRES correctif et porte
`xfail(strict=True)` : il est compte `xfailed` tant que le bug est present, et
devient un ECHEC le jour ou le correctif est applique -- ce qui force a retirer
le marqueur. Un test qui passe ici sans marqueur = constat traite.
"""

import pytest
from lxml import etree

from src.constants import NS_ALTO, SEGMONTO_ZONES, XML_ID
from src.metadata.csv_person import CSV_DELIMITER, PersonDatabase
from src.sourcedoc.elements import SurfaceTree


def qlocal(el):
    """Nom local du tag, robuste aux deux conventions de namespace (audit 4.2)."""
    return etree.QName(el).localname


# =============================================================================
# 5.1 -- @corresp pendants : MarginTextZone et GraphicZone absents de la taxonomie
# =============================================================================

# Types de zone que le pipeline produit reellement et pour lesquels
# `sourcedoc/attributes.py` pose un @corresp="#<Type>". Chacun doit avoir une
# entree dans SEGMONTO_ZONES, sinon `teiheader/full.py::segmonto_taxonomy`
# n'emet pas le <catDesc> correspondant et le @corresp pointe dans le vide.
# Mesure sur tei_test/LIV0039a_reconciled.tei.xml : 215 #MarginTextZone
# + 3 #GraphicZone pendants, contre 0 pour les six types declares.
ZONES_PRODUITES_PAR_LE_PIPELINE = {
    "MainZone",
    "MarginTextZone",
    "NumberingZone",
    "QuireMarksZone",
    "RunningTitleZone",
    "TitlePageZone",
    "GraphicZone",
    "DropCapitalZone",
}


def test_every_zone_type_produced_by_the_pipeline_is_declared_in_segmonto_zones():
    """Audit 5.1 : chaque zone produite doit avoir sa cible dans la taxonomie."""
    non_declares = sorted(ZONES_PRODUITES_PAR_LE_PIPELINE - set(SEGMONTO_ZONES))
    assert non_declares == [], (
        f"zones produites mais jamais declarees dans la taxonomie : {non_declares} "
        "-> autant de @corresp pendants dans chaque TEI"
    )


# =============================================================================
# 5.2 -- base de personnes vide confondue avec base absente
# =============================================================================

COLONNES_PERSONNE = ["BDD", "ARK", "ISNI", "Prenoms", "Nom", "Sexe", "RoleName"]


def _ecrire_csv_personne(chemin, lignes):
    contenu = [CSV_DELIMITER.join(COLONNES_PERSONNE)]
    contenu += [CSV_DELIMITER.join(l) for l in lignes]
    chemin.write_text("\n".join(contenu) + "\n", encoding="utf-8")
    return chemin


def test_empty_person_database_is_not_confused_with_a_missing_one(tmp_path):
    """
    `override_teiheader_from_csv` garde la construction du <listPerson> derriere
    `if all_person_ids and person_db:`. Faute de `__bool__`, Python retombe sur
    `__len__` : une base chargee mais vide est fausse, et tout le <listPerson>
    saute -- y compris pour des personnes qui ne viennent pas de cette base.
    Consequence mesuree : tests/test_person_ids.py echoue sur un clone frais,
    ou metadata_personne.csv (gitignore) est absent.
    """
    db_vide = PersonDatabase(_ecrire_csv_personne(tmp_path / "personnes.csv", []))

    assert len(db_vide) == 0, "prerequis du test : la base doit bien etre vide"
    assert bool(db_vide) is True, (
        "une base chargee mais vide doit rester vraie : seule une base ABSENTE "
        "devrait etre fausse"
    )


# =============================================================================
# 5.3 -- ISNI : zeros de tete manges par l'inference de type de pandas
# =============================================================================

def test_isni_leading_zeros_are_preserved(tmp_path):
    """
    Quand la colonne ISNI est entierement numerique, pandas l'infere en int64 et
    "0000000123456789" devient 123456789 avant meme le nettoyage du module.
    Les ISNI reels font 16 chiffres et sont frequemment zero-pades.
    """
    isni_source = "0000000123456789"
    csv = _ecrire_csv_personne(
        tmp_path / "personnes.csv",
        [
            ["PERS0001", "", isni_source, "Jean", "Dupont", "M", ""],
            ["PERS0002", "", "0000000987654321", "Marie", "Martin", "F", ""],
        ],
    )
    personne = PersonDatabase(csv).get("PERS0001")

    assert personne is not None, "prerequis du test : la personne doit etre chargee"
    assert personne["isni"] == isni_source, (
        f"ISNI corrompu a la lecture : {personne['isni']!r} au lieu de {isni_source!r}"
    )


# =============================================================================
# 5.4 -- certitudes divergentes pour le meme glyphe (GC vs WC)
# =============================================================================

ALTO_GLYPHE = """<?xml version="1.0" encoding="UTF-8"?>
<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout><Page ID="p1" WIDTH="100" HEIGHT="100">
    <PrintSpace><TextBlock ID="tb1"><TextLine ID="tl1">
      <String ID="s1" CONTENT="h" WC="0.87">
        <Glyph ID="g1" CONTENT="h" GC="0.5" WC="0.66"/>
      </String>
    </TextLine></TextBlock></PrintSpace>
  </Page></Layout>
</alto>
"""


def test_glyph_zone_and_char_report_the_same_certainty():
    """
    Pour un meme <Glyph>, `zone4` prend l'attribut GC et `car` prend WC : la
    sortie TEI porte deux degres de certitude differents pour le meme caractere.
    """
    root = etree.fromstring(ALTO_GLYPHE.encode())
    tree = SurfaceTree("DOC1", "f1", root)

    glyph_zone = tree.zone4(etree.Element("zone"), "tb1", "tl1", "s1", {"type": "Glyph"}, "g1", 1)
    glyph_el = root.find('.//a:Glyph[@ID="g1"]', namespaces=NS_ALTO)
    c_el = tree.car(glyph_zone, glyph_el, "tb1", "tl1", "s1", "g1", 1)

    degre_zone = [c for c in glyph_zone if qlocal(c) == "certainty"][0].get("degree")
    degre_car = [c for c in c_el if qlocal(c) == "certainty"][0].get("degree")

    assert degre_zone == degre_car, (
        f"le meme glyphe porte deux certitudes : zone={degre_zone} (GC), c={degre_car} (WC)"
    )
