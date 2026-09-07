# Tests de schema/teille-douce.odd -- l'ODD du projet.
#
# Un ODD est une promesse : « la chaine emet ceci, avec ces valeurs ». Une
# promesse qu'aucun test ne tient derive en silence, et un schema qui a pris
# du retard sur le code est pire qu'aucun schema : il valide une sortie qu'il
# ne decrit plus.
#
# Ces tests croisent donc l'ODD avec ses trois sources de verite : les
# elements que le pipeline construit vraiment (releves dans le golden), la
# taxonomie SegmOnto de teille_douce/constants.py, et la table d'entites de
# teille_douce/enrichment/entity_schema.py.
#
# Run: venv/bin/python -m pytest tests/test_odd.py -q
import re
from pathlib import Path

import pytest
from lxml import etree

from teille_douce.constants import SEGMONTO_LINES, SEGMONTO_ZONES
from teille_douce.enrichment.entity_schema import NER_ENTITY_TYPES

RACINE = Path(__file__).resolve().parent.parent
ODD = RACINE / "schema" / "teille-douce.odd"
RNG = RACINE / "schema" / "teille-douce.rng"
SCH = RACINE / "schema" / "teille-douce.sch"
SVRL = RACINE / "schema" / "teille-douce.svrl.xsl"
GOLDEN = RACINE / "tests" / "fixtures" / "golden" / "LIV9001_court.tei.xml"

NS_TEI = "http://www.tei-c.org/ns/1.0"


def _inventaire_declare(odd):
    """Les noms d'elements que la contrainte `inventaire-ferme` autorise."""
    for spec in odd.iter(f"{{{NS_TEI}}}constraintSpec"):
        if spec.get("ident") == "inventaire-ferme":
            test = " ".join(spec.iter("{http://purl.oclc.org/dsdl/schematron}assert")
                            .__next__().get("test").split())
            return set(re.findall(r"'([A-Za-z]+)'", test))
    raise AssertionError("contrainte `inventaire-ferme` introuvable dans l'ODD")


def elements_que_le_code_peut_emettre():
    """Tout nom d'element que `teille_douce/` peut poser dans un document.

    Le premier inventaire etait releve dans le golden, produit avec les
    trois phases facultatives coupees : quinze elements manquaient, et le
    schema rejetait toute sortie reellement annotee. Les sources sont donc
    prises la ou elles decident, pas la ou elles se voient :

    - un motif large sur `teille_douce/` — le nom d'element est le second argument
      des constructeurs comme des helpers (`_sub`, `_find_or_create`) ;
    - la table d'entites, dont plusieurs cles portent des noms d'elements
      (`tei_list`, `tei_parent`… : c'est la que vivent standOff et
      listPlace) ;
    - les declarations editoriales, dont les CLES sont des noms
      d'elements (`normalization`, `segmentation`) ;
    - le golden, pour ce qu'un helper construit sans litteral lisible.
    """
    from teille_douce.teiheader.prose import editorial_declarations

    noms = set(editorial_declarations())
    for entree in NER_ENTITY_TYPES.values():
        for cle, valeur in entree.items():
            if cle.startswith("tei_") and isinstance(valeur, str) and valeur.isalpha():
                noms.add(valeur)

    motif = re.compile(r'\(\s*[A-Za-z_.\[\]"\']+\s*,\s*"([A-Za-z]+)"')
    for source in (RACINE / "teille_douce").rglob("*.py"):
        noms.update(motif.findall(source.read_text(encoding="utf-8")))
    noms.update(re.findall(r'etree\.Element\(\s*"([A-Za-z]+)"',
                           "\n".join(f.read_text(encoding="utf-8")
                                     for f in (RACINE / "teille_douce").rglob("*.py"))))

    noms.update(etree.QName(e).localname
                for e in etree.parse(str(GOLDEN)).getroot().iter()
                if isinstance(e.tag, str))
    # <TEI> et les conteneurs poses par le squelette
    noms.update({"TEI", "text", "body", "front"})
    return noms


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

    construits = elements_que_le_code_peut_emettre()
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
    """Sans TAGREFS SegmOnto, teille_douce/sourcedoc/attributes.py reprend le nom de
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
            f"{produit.name} manque : teille-douce odd build")


def test_le_relaxng_ne_contient_aucun_motif_impossible():
    """Un <notAllowed/> survivant rendrait le schema incompilable par
    libxml2 (teille_douce/odd/simplify.py explique pourquoi)."""
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
    # Un plancher laisserait disparaitre une regle sans bruit : c'est le
    # nombre exact qui est verifie.
    assert len(idents) == 11, sorted(idents)

    # Et le contenu, pas seulement l'ident : modifier un XPath en gardant
    # son nom passait inapercu.
    for spec in odd.iter(f"{{{NS_TEI}}}constraintSpec"):
        for regle in spec.iter("{http://purl.oclc.org/dsdl/schematron}rule"):
            contexte = " ".join(regle.get("context").split())
            assert contexte in " ".join(schematron.split()), (
                f"contexte absent du Schematron produit : {contexte}")


def test_la_verification_de_derive_ignore_l_horodatage_de_generation():
    """Les Stylesheets datent le schema qu'elles produisent : deux
    compilations du meme ODD different d'une ligne. --check doit voir la
    derive reelle sans crier sur cette ligne-la -- meme convention que la
    normalisation du golden E2E."""
    from teille_douce.odd import without_the_date

    a = "<!-- This file generated 2026-09-03T09:05:15Z by 'extract-isosch.xsl'. -->"
    b = "<!-- This file generated 2026-09-03T09:05:25Z by 'extract-isosch.xsl'. -->"
    assert without_the_date(a) == without_the_date(b)
    assert without_the_date(a) != without_the_date(a.replace("extract", "autre"))


# -----------------------------------------------------------
# Ce que la revue de la PR a mis au jour
# -----------------------------------------------------------

def test_l_etiquette_segmonto_est_orthographiee_comme_le_corpus():
    """SEGMONTO_ZONES portait "DigitizationArtefactzone", z minuscule ; les
    273 occurrences du corpus s'ecrivent "DigitizationArtefactZone". Le
    <zone> emis reprend le libelle brut de l'ALTO et pointe vers
    #DigitizationArtefactZone, mais la taxonomie du header n'est batie que
    sur les cles de SEGMONTO_ZONES : la categorie manquait, et le @corresp
    pendait. Verifie sur tei_output/LIV0011."""
    assert "DigitizationArtefactZone" in SEGMONTO_ZONES
    assert "DigitizationArtefactzone" not in SEGMONTO_ZONES
    assert SEGMONTO_ZONES["DigitizationArtefactZone"].endswith(
        "DigitizationArtefactZone")


def test_l_inventaire_couvre_les_phases_facultatives():
    """L'inventaire etait bati sur le golden, produit avec les trois phases
    coupees : il ignorait standOff, listPlace, normalization, segmentation
    et onze autres elements, si bien que le schema rejetait toute sortie
    reellement annotee. La table d'entites et les declarations editoriales
    sont les sources qui le disent sans faire tourner la chaine."""
    declares = _inventaire_declare(etree.parse(str(ODD)))

    from teille_douce.teiheader.prose import editorial_declarations
    attendus = set(editorial_declarations())          # <normalization>, <segmentation>…
    for entree in NER_ENTITY_TYPES.values():
        for cle, valeur in entree.items():
            if cle.startswith("tei_") and isinstance(valeur, str) and valeur.isalpha():
                attendus.add(valeur)                # standOff, listPlace, place…
    attendus |= {"linkGrp", "link"}                 # ner_resolve.py, liens de mention
    attendus |= {"editionStmt", "edition"}          # utils/xml.py

    manquants = sorted(attendus - declares)
    assert not manquants, (
        f"elements emis par les phases facultatives, absents de l'inventaire : "
        f"{manquants}")


# Ce que chaque phase facultative ajoute au document, greffe a l'endroit
# ou la chaine le pose. Le golden est produit avec les trois phases
# coupees : sans ces greffes, aucun test ne verrait que le schema rejette
# une sortie annotee -- ce qui etait le cas.
GREFFES = {
    "modernisation : <normalization> dans l'editorialDecl":
        ("<editorialDecl>", "<editorialDecl><normalization><p>x</p></normalization>"),
    "enrichissement : <segmentation> dans l'editorialDecl":
        ("<editorialDecl>", "<editorialDecl><segmentation><p>x</p></segmentation>"),
    "modernisation : <choice><orig>/<reg> dans le corps":
        ("<ab ", "<ab n='greffe'><choice><orig>ma¬in</orig><reg>main</reg></choice></ab><ab "),
    "enrichissement : <s>/<w>/<pc> dans le corps":
        ("<ab ", "<ab n='greffe'><s><w lemma='x' pos='NOMcom'>mot</w><pc>.</pc></s></ab><ab "),
    "NER : <standOff> et ses listes":
        ("<text>", "<standOff><listPlace><place xml:id='p1'><placeName>Rome</placeName>"
                   "</place></listPlace><listOrg><org xml:id='o1'><orgName>Academie</orgName>"
                   "</org></listOrg><listEvent><event xml:id='e1'><label>Salon</label></event>"
                   "</listEvent><listObject><object xml:id='ob1'>"
                   # TEI exige que le nom soit dans <objectIdentifier> ;
                   # entity_schema.py le sait (tei_item_wrapper).
                   "<objectIdentifier><objectName>Tableau</objectName>"
                   "</objectIdentifier></object></listObject>"
                   "<linkGrp type='mentions'>"
                   "<link target='#p1 #o1'/></linkGrp></standOff><text>"),
    "NER : entites dans le corps":
        ("<ab ", "<ab n='greffe'><persName resp='#ner-auto' cert='high'>Poussin</persName>"
                 "<rs type='technique' resp='#ner-auto' cert='low'>burin</rs>"
                 "<material>huile</material></ab><ab "),
    "agent automatique declare dans l'<editionStmt>":
        ("</titleStmt>", "</titleStmt><editionStmt><edition>x</edition>"
                         "<respStmt xml:id='ner-auto'><resp>NER</resp>"
                         "<name>GLiNER</name></respStmt></editionStmt>"),
}


@pytest.mark.parametrize("cas", list(GREFFES))
def test_le_schema_accepte_ce_que_chaque_phase_produit(cas):
    """L'inventaire etait releve sur le golden, produit avec NER,
    enrichissement et modernisation coupes : quinze elements manquaient et
    `validate_tei.py --odd` echouait sur tout document reellement annote.
    Le E2E complet le verrait, mais il exige deux services locaux et il est
    deselectionne par defaut ; ces greffes tiennent le controle sans eux."""
    avant, apres = GREFFES[cas]
    texte = GOLDEN.read_text(encoding="utf-8").replace(
        'when="DATE-GENERATION"', 'when="2026-09-03"')
    assert avant in texte, f"motif de greffe absent du golden : {avant!r}"
    texte = texte.replace(avant, apres, 1)

    relaxng = etree.RelaxNG(etree.parse(str(RNG)))
    doc = etree.fromstring(texte.encode())
    assert relaxng.validate(doc), "\n".join(
        f"L{e.line}: {e.message}" for e in list(relaxng.error_log)[:6])


def test_a_derivative_that_cannot_be_read_is_drift_and_not_a_traceback():
    """A `teille-douce.rng` re-saved in latin-1 — the case the saxonche
    widening's own comment names — raises `UnicodeDecodeError` inside
    `verify`, which came out as a traceback with exit 1. But 1 is what
    this function returns for real drift, so a CI could not tell a
    damaged derivative from an edited one."""
    import tempfile
    from pathlib import Path

    from teille_douce.odd import build

    with tempfile.TemporaryDirectory() as tmp:
        schema = Path(tmp) / "schema"
        schema.mkdir()
        (schema / "teille-douce.rng").write_bytes("préface".encode("latin-1"))
        produced = Path(tmp) / "produced"
        produced.mkdir()
        (produced / "teille-douce.rng").write_text("x", encoding="utf-8")

        def compile_nothing(destination, **_):
            return [produced / "teille-douce.rng"]

        # `compile_odd` downloads a hundred megabytes of toolchain; only
        # the READING of the derivatives is under test here.
        real = build.compile_odd
        build.compile_odd = compile_nothing
        try:
            code = build.verify(toolchain=object(), schema=schema,
                                say=lambda *_: None)
        finally:
            build.compile_odd = real

    assert code == 1
