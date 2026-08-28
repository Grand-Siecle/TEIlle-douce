"""
Tests de bout en bout du pipeline, sur la fixture ALTO minimale versionnee.

Deux modes, correspondant aux deux marqueurs pytest :

  e2e       mode court : TEI de base, sans NER, sans enrichissement, sans
            modernisation. Aucune dependance reseau, ~1 s. Tourne partout.
  e2e_full  mode complet : toutes les annotations linguistiques. Exige les
            services PyHellen et VieuxParler ; s'auto-ignore (skip) avec un
            motif explicite s'ils ne repondent pas.

La fixture est `tests/fixtures/alto_min/` (8 pages, 175 Ko) — voir
`scripts/build_test_fixture.py` pour sa provenance et son mode de regeneration.

Le pipeline produit aujourd'hui des `uuid4` differents a chaque execution
(rapport d'audit 2.8), ce qui interdit toute comparaison directe. Les tests
normalisent donc les identifiants avant comparaison : la meme suite deviendra
un vrai golden-file, sans reecriture, le jour ou les `uuid5` seront en place.
"""

import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

import pytest
from lxml import etree

RACINE = Path(__file__).resolve().parent.parent
FIXTURES = RACINE / "tests" / "fixtures"
ALTO_MIN = FIXTURES / "alto_min"
GOLDEN = FIXTURES / "golden" / "LIV9001_court.tei.xml"
DOCUMENT = "LIV9001_reconciled"

XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
# Les prefixes sont a casse mixte (zone_, zoneLine_, line_, cert_...) : on ne
# normalise que la partie hexadecimale, le prefixe reste en place et reste lisible.
UUID_RE = re.compile(r"(?<![0-9a-zA-Z])[0-9a-f]{32}(?![0-9a-zA-Z])")


# =============================================================================
# Execution du pipeline
# =============================================================================

def lancer_pipeline(tmp_path, **flags):
    """
    Execute main.py sur la fixture, dans un repertoire de travail jetable.

    Les chemins des CSV de metadonnees ne sont pas surchargeables par variable
    d'environnement (ils sont relatifs au repertoire courant), d'ou l'execution
    dans un tmp_path ou l'on a copie les CSV de fixture.

    Returns:
        Path: le fichier TEI produit.
    """
    for csv in ("metadata_livre.csv", "metadata_personne.csv"):
        shutil.copy(FIXTURES / csv, tmp_path / csv)

    sortie = tmp_path / "out"
    env = {
        **os.environ,
        "ALTO2TEI_OCR_DIR": str(ALTO_MIN),
        "ALTO2TEI_OUTPUT_DIR": str(sortie),
        **{k: v for k, v in flags.items()},
    }
    env.update(_env_couverture_sous_processus())
    res = subprocess.run(
        [sys.executable, str(RACINE / "main.py")],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=900,
    )
    produit = sortie / f"{DOCUMENT}.tei.xml"
    assert produit.exists(), (
        f"aucune sortie produite (code {res.returncode})\n"
        f"--- stdout ---\n{res.stdout[-3000:]}\n--- stderr ---\n{res.stderr[-3000:]}"
    )
    return produit


def _env_couverture_sous_processus():
    """
    Variables faisant mesurer le sous-processus par coverage, s'il y a une
    mesure en cours dans le parent. Hors mesure, renvoie un dict vide et le
    sous-processus tourne normalement.
    """
    # `coverage` n'est dans sys.modules que si le parent tourne sous
    # `coverage run` ; sous un pytest ordinaire, on ne touche a rien.
    if "coverage" not in sys.modules:
        return {}

    chemin = str(Path(__file__).resolve().parent / "_subprocess_coverage")
    pythonpath = os.environ.get("PYTHONPATH")
    return {
        "COVERAGE_PROCESS_START": str(RACINE / "pyproject.toml"),
        "PYTHONPATH": f"{chemin}{os.pathsep}{pythonpath}" if pythonpath else chemin,
        # Le sous-processus tourne avec cwd=tmp_path : sans chemin absolu, il
        # ecrirait ses donnees dans le repertoire temporaire, qui disparait.
        "COVERAGE_FILE": os.environ.get("COVERAGE_FILE", str(RACINE / ".coverage")),
    }


def normaliser(chemin, sans_taxonomie=False):
    """
    Rend la sortie comparable d'un run a l'autre.

    Remplace chaque uuid par un jeton sequentiel attribue dans l'ordre du
    document (les references `#uuid` suivent, la substitution etant textuelle
    et les uuid uniques), et neutralise la date de generation.

    `sans_taxonomie` retire en plus le bloc <taxonomy>, dont l'ordre varie d'un
    processus a l'autre (audit 6.9) : cela permet de verifier la stabilite de
    tout le reste du document sans que ce defaut connu ne masque les autres. Le
    defaut lui-meme est epingle par un test dedie, et le contenu de la taxonomie
    est couvert par les tests unitaires de src/teiheader/.
    """
    if sans_taxonomie:
        arbre = etree.parse(str(chemin))
        for tax in list(arbre.iter("{*}taxonomy")):
            tax.getparent().remove(tax)
        texte = etree.tostring(arbre, encoding="utf-8").decode("utf-8")
    else:
        texte = Path(chemin).read_text(encoding="utf-8")

    correspondance = {}
    for uuid in UUID_RE.findall(texte):
        correspondance.setdefault(uuid, f"{len(correspondance) + 1:04d}")
    for uuid, jeton in correspondance.items():
        texte = texte.replace(uuid, jeton)

    # Date de generation du fichier (les dates historiques sont conservees).
    texte = re.sub(r'when="20\d\d-\d\d-\d\d"', 'when="DATE-GENERATION"', texte)
    return texte


def local(el):
    """Nom local du tag, robuste aux deux conventions de namespace (audit 4.2)."""
    return etree.QName(el).localname


# =============================================================================
# Disponibilite des services (mode complet)
# =============================================================================

def service_disponible(url, timeout=2.0):
    import urllib.error
    import urllib.request
    try:
        urllib.request.urlopen(url, timeout=timeout)
        return True
    except urllib.error.HTTPError:
        return True          # le serveur repond, meme si l'URL sondee n'existe pas
    except Exception:
        return False


def services_manquants():
    from config import MODERNIZE_API, PYHELLEN_URL
    manquants = []
    if not service_disponible(PYHELLEN_URL):
        manquants.append(f"PyHellen ({PYHELLEN_URL})")
    for lang, url in MODERNIZE_API.items():
        if not service_disponible(url):
            manquants.append(f"VieuxParler {lang} ({url})")
    return manquants


# =============================================================================
# Mode court
# =============================================================================

MODE_COURT = {"ALTO2TEI_NER": "0", "ALTO2TEI_ENRICHMENT": "0", "ALTO2TEI_MODERNIZE": "0"}


@pytest.fixture(scope="module")
def tei_court(tmp_path_factory):
    return lancer_pipeline(tmp_path_factory.mktemp("e2e_court"), **MODE_COURT)


@pytest.mark.e2e
def test_court_produit_un_tei_bien_forme(tei_court):
    racine = etree.parse(str(tei_court)).getroot()
    assert local(racine) == "TEI"
    assert racine.find(".//{*}teiHeader") is not None
    assert racine.find(".//{*}sourceDoc") is not None
    assert racine.find(".//{*}body") is not None


@pytest.mark.e2e
def test_court_couvre_toutes_les_structures_du_corps(tei_court):
    """La fixture est calibree pour exercer chaque forme produite par build_body."""
    body = etree.parse(str(tei_court)).find(".//{*}body")
    presents = {local(e) for e in body.iter()}
    for attendu in ("pb", "lb", "ab", "note", "fw", "hi", "foreign"):
        assert attendu in presents, f"<{attendu}> absent du corps produit"


@pytest.mark.e2e
def test_court_une_page_par_fichier_alto(tei_court):
    arbre = etree.parse(str(tei_court))
    alto = sorted((ALTO_MIN / DOCUMENT / "content" / "data" / "doc_1").glob("*.xml"))
    assert len(arbre.findall(".//{*}surface")) == len(alto)
    assert len(arbre.findall(".//{*}body//{*}pb")) == len(alto)


@pytest.mark.e2e
def test_court_le_header_est_alimente_par_le_csv(tei_court):
    """Sans les CSV de fixture, le header resterait en placeholders."""
    arbre = etree.parse(str(tei_court))
    titres = [t.text for t in arbre.iter("{*}title") if t.text]
    assert any("peinture" in (t or "").lower() for t in titres), titres
    assert any(
        (i.text or "").startswith("ark:/12148/bpt6k9001") for i in arbre.iter("{*}idno")
    )


@pytest.mark.e2e
def test_court_detecte_le_multilinguisme(tei_court):
    """La fixture melange francais et latin : langUsage doit refleter les deux."""
    arbre = etree.parse(str(tei_court))
    langues = {l.get("ident") for l in arbre.iter("{*}language")}
    assert {"fra", "lat"} <= langues, langues


@pytest.mark.e2e
def test_court_passe_le_validateur_du_projet(tei_court):
    res = subprocess.run(
        [sys.executable, str(RACINE / "scripts" / "validate_tei.py"), str(tei_court)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stdout + res.stderr


@pytest.fixture(scope="module")
def tei_court_bis(tmp_path_factory):
    """Seconde execution, pour les comparaisons de stabilite."""
    return lancer_pipeline(tmp_path_factory.mktemp("e2e_court_bis"), **MODE_COURT)


@pytest.mark.e2e
def test_court_est_stable_entre_deux_executions(tei_court, tei_court_bis):
    """
    Non-regression : deux executions doivent produire le meme TEI une fois les
    identifiants normalises. C'est ce test qui deviendra un golden-file exact
    quand les uuid5 remplaceront les uuid4 (audit 2.8).

    La taxonomie est retiree avant comparaison : son ordre est instable pour
    une raison distincte, epinglee par le test suivant.
    """
    assert normaliser(tei_court_bis, sans_taxonomie=True) == normaliser(
        tei_court, sans_taxonomie=True
    )


@pytest.mark.e2e
@pytest.mark.xfail(
    strict=True,
    reason="audit 6.9 -- full.py:224/230 iterent sur un set, l'ordre depend de PYTHONHASHSEED",
)
def test_court_ordre_de_la_taxonomie_stable(tei_court, tei_court_bis):
    """
    L'ordre des <catDesc> de la taxonomie SegmOnto doit etre reproductible.
    Aujourd'hui il varie d'un processus a l'autre, ce qui bruiterait tout diff
    entre deux sorties meme apres le passage aux uuid5.
    """
    ordre = lambda f: [
        c.get(XML_ID) for c in etree.parse(str(f)).iter("{*}catDesc")
    ]
    assert ordre(tei_court) == ordre(tei_court_bis)


@pytest.mark.e2e
def test_court_correspond_au_golden(tei_court):
    """
    Compare a la sortie de reference versionnee. Regenerer avec :
        venv/bin/python scripts/build_test_fixture.py --golden
    """
    if not GOLDEN.exists():
        pytest.skip(f"golden absent : {GOLDEN.relative_to(RACINE)}")
    # Taxonomie neutralisee ici aussi : sans cela le golden serait instable
    # tant que l'audit 6.9 n'est pas corrige.
    assert normaliser(tei_court, sans_taxonomie=True) == GOLDEN.read_text(
        encoding="utf-8"
    )


# =============================================================================
# Mode complet
# =============================================================================

@pytest.mark.e2e_full
def test_complet_produit_les_annotations_linguistiques(tmp_path):
    manquants = services_manquants()
    if manquants:
        pytest.skip("services indisponibles : " + ", ".join(manquants))

    tei = lancer_pipeline(
        tmp_path,
        ALTO2TEI_NER="1", ALTO2TEI_ENRICHMENT="1", ALTO2TEI_MODERNIZE="1",
    )
    arbre = etree.parse(str(tei))
    presents = {local(e) for e in arbre.find(".//{*}body").iter()}

    assert "s" in presents, "aucune phrase <s> : l'enrichissement n'a rien produit"
    assert "w" in presents, "aucun token <w>"
    assert {"choice", "orig", "reg"} <= presents, "aucune modernisation encodee"
    assert any(
        w.get("lemma") or w.get("pos") for w in arbre.iter("{*}w")
    ), "aucun <w> ne porte de lemme ni de categorie"
