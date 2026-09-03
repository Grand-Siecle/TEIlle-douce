# Tests de schema/alto2tei.odd -- l'ODD du projet.
#
# Un ODD est une promesse : « la chaine emet ceci, avec ces valeurs ». Une
# promesse qu'aucun test ne tient derive en silence, et un schema qui a pris
# du retard sur le code est pire qu'aucun schema : il valide une sortie qu'il
# ne decrit plus.
#
# Ces tests croisent donc l'ODD avec ses trois sources de verite : les
# elements que le pipeline construit vraiment (releves dans le golden), la
# taxonomie SegmOnto de src/constants.py, et la table d'entites de
# src/enrichment/entity_schema.py.
#
# Run: venv/bin/python -m pytest tests/test_odd.py -q
import re
from pathlib import Path

import pytest
from lxml import etree

from src.constants import SEGMONTO_LINES, SEGMONTO_ZONES
from src.enrichment.entity_schema import NER_ENTITY_TYPES

RACINE = Path(__file__).resolve().parent.parent
ODD = RACINE / "schema" / "alto2tei.odd"
RNG = RACINE / "schema" / "alto2tei.rng"
SCH = RACINE / "schema" / "alto2tei.sch"
SVRL = RACINE / "schema" / "alto2tei.svrl.xsl"
GOLDEN = RACINE / "tests" / "fixtures" / "golden" / "LIV9001_court.tei.xml"

NS_TEI = "http://www.tei-c.org/ns/1.0"


@pytest.fixture(scope="module")
def odd():
    return etree.parse(str(ODD))


def _inventaire_declare(odd):
    """Les noms d'elements que la contrainte `inventaire-ferme` autorise."""
    for spec in odd.iter(f"{{{NS_TEI}}}constraintSpec"):
        if spec.get("ident") == "inventaire-ferme":
            test = " ".join(spec.iter("{http://purl.oclc.org/dsdl/schematron}assert")
                            .__next__().get("test").split())
            return set(re.findall(r"'([A-Za-z]+)'", test))
    raise AssertionError("contrainte `inventaire-ferme` introuvable dans l'ODD")


def _valist(odd, element, attribut):
    """Les valeurs autorisees pour *attribut* sur *element*."""
    for spec in odd.iter(f"{{{NS_TEI}}}elementSpec"):
        if spec.get("ident") != element:
            continue
        for att in spec.iter(f"{{{NS_TEI}}}attDef"):
            if att.get("ident") == attribut:
                return {v.get("ident")
                        for v in att.iter(f"{{{NS_TEI}}}valItem")}
    raise AssertionError(f"pas de valList pour {element}/@{attribut}")


# -----------------------------------------------------------
# L'ODD ne doit pas prendre de retard sur la chaine
# -----------------------------------------------------------

def test_l_inventaire_couvre_tout_ce_que_le_golden_contient():
    """Le golden est une sortie complete du pipeline : chacun de ses
    elements doit etre declare, sinon la validation le rejetterait."""
    odd = etree.parse(str(ODD))
    declares = _inventaire_declare(odd)
    presents = {etree.QName(e).localname
                for e in etree.parse(str(GOLDEN)).getroot().iter()
                if isinstance(e.tag, str)}
    manquants = presents - declares
    assert not manquants, (
        f"le golden contient des elements que l'ODD ne declare pas : "
        f"{sorted(manquants)}")


def test_l_inventaire_ne_declare_rien_qui_ne_soit_construit():
    """L'inverse : un element declare mais qu'aucun code n'emet est une
    promesse vide, et rend l'inventaire moins informatif qu'il n'en a
    l'air. Les elements des phases facultatives sont absents du golden
    court, donc on lit le code plutot que la sortie."""
    odd = etree.parse(str(ODD))
    declares = _inventaire_declare(odd)

    construits = set()
    motif = re.compile(r'(?:SubElement\([^,]+,\s*|etree\.Element\(\s*)"([A-Za-z]+)"')
    for source in (RACINE / "src").rglob("*.py"):
        construits.update(motif.findall(source.read_text(encoding="utf-8")))
    construits.update(v["tei_element"] for v in NER_ENTITY_TYPES.values())
    # <TEI> et les conteneurs poses par le squelette du header
    construits.update({"TEI", "text", "body", "front"})
    # Certains elements passent par un helper plutot que par un
    # SubElement litteral (<birth>, <death> dans csv_book.py) : leur
    # presence dans une sortie reelle vaut preuve d'emission.
    construits.update(etree.QName(e).localname
                      for e in etree.parse(str(GOLDEN)).getroot().iter()
                      if isinstance(e.tag, str))

    fantomes = declares - construits
    assert not fantomes, (
        f"l'ODD declare des elements que rien n'emet : {sorted(fantomes)}")


# -----------------------------------------------------------
# Les listes fermees et leurs sources de verite
# -----------------------------------------------------------

def test_les_types_de_zone_couvrent_la_taxonomie_segmonto():
    """zone/@type porte les etiquettes de zone ET de ligne : la chaine
    ecrit une <zone> par ligne, typee avec l'etiquette de ligne."""
    valeurs = _valist(etree.parse(str(ODD)), "zone", "type")
    manquantes = (set(SEGMONTO_ZONES) | set(SEGMONTO_LINES)) - valeurs
    assert not manquantes, (
        f"SegmOnto a des etiquettes que l'ODD ignore : {sorted(manquantes)}")


def test_les_types_de_zone_declarent_les_replis_alto():
    """Sans TAGREFS SegmOnto, src/sourcedoc/attributes.py reprend le nom de
    l'element ALTO. Ces valeurs ne sont pas SegmOnto, et l'ODD doit les
    declarer plutot que de les faire echouer."""
    valeurs = _valist(etree.parse(str(ODD)), "zone", "type")
    assert {"TextBlock", "TextLine", "Space"} <= valeurs


def test_les_types_de_rs_suivent_la_table_des_entites():
    valeurs = _valist(etree.parse(str(ODD)), "rs", "type")
    attendus = {v["tei_element_attrs"]["type"]
                for v in NER_ENTITY_TYPES.values()
                if v.get("tei_element") == "rs" and v.get("tei_element_attrs")}
    assert valeurs == attendus, (valeurs, attendus)


# -----------------------------------------------------------
# Les artefacts derives suivent l'ODD
# -----------------------------------------------------------

def test_les_schemas_derives_sont_versionnes():
    for produit in (RNG, SCH, SVRL):
        assert produit.exists(), (
            f"{produit.name} manque : venv/bin/python scripts/build_odd.py")


def test_le_relaxng_ne_contient_aucun_motif_impossible():
    """Un <notAllowed/> survivant rendrait le schema incompilable par
    libxml2 (scripts/rng_simplify.py explique pourquoi)."""
    grammaire = etree.parse(str(RNG))
    restants = list(grammaire.iter(
        "{http://relaxng.org/ns/structure/1.0}notAllowed"))
    assert not restants, f"{len(restants)} <notAllowed/> dans le RelaxNG"


def test_le_schematron_porte_les_contraintes_de_l_odd():
    """extract-isosch attribue a chaque contrainte la langue de son
    ancetre et jette les autres : une balise xml:lang mal placee dans
    l'ODD suffit a vider le Schematron de nos regles sans rien casser
    d'autre. C'est arrive."""
    odd = etree.parse(str(ODD))
    idents = {s.get("ident") for s in odd.iter(f"{{{NS_TEI}}}constraintSpec")}
    schematron = SCH.read_text(encoding="utf-8")
    absentes = {i for i in idents if i not in schematron}
    assert not absentes, (
        f"contraintes ecrites dans l'ODD mais absentes du Schematron : "
        f"{sorted(absentes)}")
    assert len(idents) >= 9, idents


def test_la_verification_de_derive_ignore_l_horodatage_de_generation():
    """Les Stylesheets datent le schema qu'elles produisent : deux
    compilations du meme ODD different d'une ligne. --check doit voir la
    derive reelle sans crier sur cette ligne-la -- meme convention que la
    normalisation du golden E2E."""
    import sys
    sys.path.insert(0, str(RACINE / "scripts"))
    from build_odd import sans_horodatage

    a = "<!-- This file generated 2026-09-03T09:05:15Z by 'extract-isosch.xsl'. -->"
    b = "<!-- This file generated 2026-09-03T09:05:25Z by 'extract-isosch.xsl'. -->"
    assert sans_horodatage(a) == sans_horodatage(b)
    assert sans_horodatage(a) != sans_horodatage(a.replace("extract", "autre"))
