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

Depuis le passage aux `uuid5` (audit 2.8) et au tri de la taxonomie (6.9),
la sortie est entierement deterministe : la comparaison au golden est un
vrai diff a l'identifiant pres — seule la date de generation est neutralisee.
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


# =============================================================================
# Execution du pipeline
# =============================================================================

def _executer_main(tmp_path, ocr_dir, args=(), **flags):
    """
    Execute main.py dans un repertoire de travail jetable, sans presumer du
    resultat. Renvoie (CompletedProcess, repertoire de sortie).

    Les chemins des CSV de metadonnees ne sont pas surchargeables par variable
    d'environnement (ils sont relatifs au repertoire courant), d'ou l'execution
    dans un tmp_path ou l'on a copie les CSV de fixture.
    """
    for csv in ("metadata_livre.csv", "metadata_personne.csv"):
        shutil.copy(FIXTURES / csv, tmp_path / csv)

    sortie = tmp_path / "out"
    env = {
        **os.environ,
        "ALTO2TEI_OCR_DIR": str(ocr_dir),
        "ALTO2TEI_OUTPUT_DIR": str(sortie),
        **{k: v for k, v in flags.items()},
    }
    env.update(_env_couverture_sous_processus())
    res = subprocess.run(
        [sys.executable, str(RACINE / "main.py"), *args],
        cwd=tmp_path, env=env, capture_output=True, text=True, timeout=900,
    )
    return res, sortie


def lancer_pipeline(tmp_path, **flags):
    """
    Execute main.py sur la fixture et exige un succes.

    Returns:
        Path: le fichier TEI produit.
    """
    res, sortie = _executer_main(tmp_path, ALTO_MIN, **flags)
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


def normaliser(chemin):
    """
    Rend la sortie comparable d'un run a l'autre.

    Seule la date de generation du fichier est neutralisee (les dates
    historiques sont conservees) : depuis le passage aux uuid5 (audit 2.8)
    et au tri de la taxonomie (audit 6.9), les identifiants et l'ordre des
    elements sont deterministes — la comparaison est un vrai golden-file,
    identifiants compris.

    (Historique : les uuid4 etaient remplaces par des jetons sequentiels,
    et un parametre `sans_taxonomie` retirait le bloc <taxonomy> instable.)
    """
    texte = Path(chemin).read_text(encoding="utf-8")
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
    Non-regression : deux executions (deux processus, deux PYTHONHASHSEED)
    doivent produire le meme TEI a l'octet pres, identifiants et taxonomie
    compris — seule la date de generation est neutralisee (audit 2.8, 6.9).
    """
    assert normaliser(tei_court_bis) == normaliser(tei_court)


@pytest.mark.e2e
def test_court_ordre_de_la_taxonomie_stable(tei_court, tei_court_bis):
    """
    L'ordre des <catDesc> de la taxonomie SegmOnto doit etre reproductible
    d'un processus a l'autre (audit 6.9, corrige par un tri explicite).
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
    assert normaliser(tei_court) == GOLDEN.read_text(encoding="utf-8")


@pytest.mark.e2e
def test_court_un_document_casse_ne_tue_pas_le_run(tmp_path):
    """
    Audit 2.1/2.10 : un document corrompu est signale, compte dans le bilan et
    fait sortir avec un code non nul — mais les documents suivants sont
    convertis normalement. Le document casse est nomme pour passer AVANT le
    document sain dans l'ordre de traitement : la reussite du sain prouve que
    la boucle a continue apres l'echec.
    """
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    casse = ocr / "LIV9000_reconciled" / "content" / "data" / "doc_1"
    casse.mkdir(parents=True)
    # Irrecuperable meme par le mode recovery de libxml2 : la page unique du
    # document est sautee, donc "toutes les pages inutilisables" -> echec du
    # document (un XML simplement malforme serait recupere avec warning).
    (casse / "f1.xml").write_text("\x00\x01pas du xml\x02", encoding="utf-8")

    res, sortie = _executer_main(tmp_path, ocr, **MODE_COURT)

    # le document sain, traite apres le casse, est bien produit
    assert (sortie / f"{DOCUMENT}.tei.xml").exists(), (
        f"--- stdout ---\n{res.stdout[-3000:]}\n--- stderr ---\n{res.stderr[-3000:]}"
    )
    # le document casse n'a pas de sortie
    assert not (sortie / "LIV9000_reconciled.tei.xml").exists()
    # bilan : echec signale nominalement, code retour non nul
    assert res.returncode != 0
    assert "LIV9000_reconciled" in res.stdout
    assert "1/2" in res.stdout
    # audit 2.10 : le log du run est horodate (pipeline_YYYYMMDD_HHMMSS.log),
    # un prochain run n'ecrasera donc pas la trace de cet echec
    assert list(tmp_path.glob("pipeline_*.log")), sorted(tmp_path.iterdir())


@pytest.mark.e2e
def test_court_skip_existing_saute_le_converti_et_traite_le_reste(tmp_path):
    """
    Audit 2.5 : avec --skip-existing et deux documents dont un deja
    converti, le converti est saute (sa sentinelle ressort intacte) et
    l'autre est reellement traite dans le meme run — le vrai chemin de
    reprise, pas seulement le retour anticipe "rien a faire".
    """
    ocr = tmp_path / "ocr"
    shutil.copytree(ALTO_MIN, ocr)
    # Second document : copie du premier sous un autre nom
    shutil.copytree(ocr / DOCUMENT, ocr / "LIV9002_reconciled")

    sortie = tmp_path / "out"
    sortie.mkdir()
    sentinelle = sortie / f"{DOCUMENT}.tei.xml"
    sentinelle.write_text("<sentinelle/>", encoding="utf-8")

    res, _ = _executer_main(tmp_path, ocr, args=("--skip-existing",), **MODE_COURT)

    assert res.returncode == 0, res.stdout[-2000:] + res.stderr[-2000:]
    # le document deja converti n'a pas ete retraite
    assert sentinelle.read_text(encoding="utf-8") == "<sentinelle/>"
    assert "skip-existing" in res.stdout
    # l'autre document, lui, a ete converti dans ce meme run
    assert (sortie / "LIV9002_reconciled.tei.xml").exists(), res.stdout[-2000:]


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
