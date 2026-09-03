#!/usr/bin/env python3
"""
Compile schema/alto2tei.odd en schemas exploitables.

L'ODD est la source de verite : ce que la chaine emet, avec quelles valeurs
et sous quelles contraintes. Ce script en derive les trois artefacts
versionnes a cote de lui :

    schema/alto2tei.rng        modele de contenu, applique par lxml
    schema/alto2tei.sch        contraintes Schematron, forme lisible
    schema/alto2tei.svrl.xsl   les memes, precompilees, forme executable

Le troisieme existe parce que la TEI produit du Schematron en
queryBinding="xslt2", que lxml.isoschematron refuse (il n'implemente que
XPath 1.0). Precompiler la feuille SVRL ici permet a la validation de ne
dependre que de saxonche, sans la boite a outils complete.

Boite a outils, materialisee dans .odd-toolchain/ (gitignore) a des versions
epinglees -- une chaine de compilation qui bouge sous les pieds produirait
des diffs de schema sans changement d'ODD :

    p5subset.xml       la TEI P5 compilee, ce que @source resout
    Stylesheets/       les XSLT officielles odd2odd, odd2relax, extract-isosch
    schxslt/           le compilateur Schematron -> SVRL

Le moteur XSLT est SaxonC-HE via la roue pip `saxonche` : XSLT 2.0 sans JVM
ni ant, contrairement aux scripts shell livres avec les Stylesheets.

Usage :
    venv/bin/python scripts/build_odd.py            # compile
    venv/bin/python scripts/build_odd.py --check    # verifie sans ecrire
    venv/bin/python scripts/build_odd.py --refresh  # re-telecharge la boite

--check recompile dans un repertoire temporaire et compare aux artefacts
versionnes : il sort 1 si l'ODD a ete modifie sans etre recompile.
"""

import argparse
import re
import io
import shutil
import subprocess
import sys
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path

from lxml import etree

sys.path.insert(0, str(Path(__file__).resolve().parent))
from rng_simplify import simplifier  # noqa: E402

RACINE = Path(__file__).resolve().parent.parent
SCHEMA = RACINE / "schema"
ODD = SCHEMA / "alto2tei.odd"
OUTILS = RACINE / ".odd-toolchain"

# Versions epinglees. Les remonter est un changement delibere : il faut
# recompiler et relire le diff des schemas produits.
P5_VERSION = "4.12.0"
STYLESHEETS_TAG = "v7.61.0"
SCHXSLT_VERSION = "1.10.1"

P5_URL = f"https://www.tei-c.org/Vault/P5/{P5_VERSION}/xml/tei/odd/p5subset.xml"
STYLESHEETS_URL = (
    f"https://codeload.github.com/TEIC/Stylesheets/tar.gz/refs/tags/{STYLESHEETS_TAG}"
)
# GitHub bride les telechargements anonymes par periodes. Quand c'est le
# cas, la meme archive passe par l'API authentifiee de `gh`, s'il est
# installe et connecte -- sinon il ne reste qu'a reessayer plus tard.
STYLESHEETS_API = f"repos/TEIC/Stylesheets/tarball/{STYLESHEETS_TAG}"
SCHXSLT_URL = (
    f"https://codeberg.org/SchXslt/schxslt/releases/download/"
    f"v{SCHXSLT_VERSION}/schxslt-{SCHXSLT_VERSION}-xslt-only.zip"
)

P5 = OUTILS / "p5subset.xml"
STYLESHEETS = OUTILS / "Stylesheets"
SCHXSLT = OUTILS / f"schxslt-{SCHXSLT_VERSION}"

# La langue des messages d'erreur. extract-isosch.xsl s'ARRETE si les
# contraintes existent en plusieurs langues sans qu'on tranche -- et l'ODD
# est bilingue, donc ce parametre n'est pas optionnel.
LANG_MESSAGES = "en"


# -----------------------------------------------------------
# Boite a outils
# -----------------------------------------------------------

def _telecharger(url, cible):
    print(f"  telechargement {url}")
    with urllib.request.urlopen(url, timeout=300) as reponse:
        donnees = reponse.read()
    cible.write_bytes(donnees)
    return donnees


def _archive_stylesheets():
    """L'archive des Stylesheets, par le chemin anonyme puis, s'il est
    bride, par l'API authentifiee de gh."""
    try:
        print(f"  telechargement {STYLESHEETS_URL}")
        with urllib.request.urlopen(STYLESHEETS_URL, timeout=600) as reponse:
            return reponse.read()
    except (urllib.error.URLError, TimeoutError) as echec:
        if not shutil.which("gh"):
            raise SystemExit(
                f"telechargement des Stylesheets impossible ({echec}).\n"
                "GitHub bride parfois les telechargements anonymes : reessayer\n"
                "plus tard, ou installer gh (https://cli.github.com) et s'y "
                "connecter."
            )
        print(f"  anonyme bride ({echec}) -- reprise via gh api")
        acheve = subprocess.run(["gh", "api", STYLESHEETS_API],
                                capture_output=True)
        if acheve.returncode != 0:
            # Le cas le plus frequent est un gh installe mais pas
            # connecte : son explication est dans stderr, et la taire
            # laisserait une trace d'appel a la place du diagnostic.
            raise SystemExit(
                "gh api a echoue pour les Stylesheets :\n"
                + (acheve.stderr.decode("utf-8", "replace").strip()
                   or f"code de sortie {acheve.returncode}")
                + "\n(se connecter avec `gh auth login`, ou reessayer plus tard)"
            )
        return acheve.stdout


def _extraire_stylesheets(archive):
    """L'archive GitHub emballe tout dans un repertoire horodate ; on le
    deplie sous .odd-toolchain/Stylesheets."""
    with tempfile.TemporaryDirectory() as tmp:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
            tar.extractall(tmp, filter="data")
        racines = list(Path(tmp).iterdir())
        if len(racines) != 1:
            raise SystemExit(f"archive Stylesheets inattendue : {racines}")
        shutil.move(str(racines[0]), str(STYLESHEETS))


def preparer_outils(refresh=False):
    """Materialise p5subset.xml, les Stylesheets et SchXslt, aux versions
    epinglees. Ne retelecharge que ce qui manque, sauf si refresh."""
    if refresh and OUTILS.exists():
        shutil.rmtree(OUTILS)
    OUTILS.mkdir(exist_ok=True)

    if not P5.exists():
        print(f"TEI P5 {P5_VERSION}")
        _telecharger(P5_URL, P5)

    if not (STYLESHEETS / "odds" / "odd2odd.xsl").exists():
        print(f"TEI Stylesheets {STYLESHEETS_TAG}")
        if STYLESHEETS.exists():
            shutil.rmtree(STYLESHEETS)
        _extraire_stylesheets(_archive_stylesheets())

    if not (SCHXSLT / "2.0" / "pipeline-for-svrl.xsl").exists():
        print(f"SchXslt {SCHXSLT_VERSION}")
        with urllib.request.urlopen(SCHXSLT_URL, timeout=300) as reponse:
            archive = zipfile.ZipFile(io.BytesIO(reponse.read()))
        archive.extractall(OUTILS)

    manquants = [
        chemin for chemin in (P5, STYLESHEETS / "odds" / "odd2odd.xsl",
                              SCHXSLT / "2.0" / "pipeline-for-svrl.xsl")
        if not chemin.exists()
    ]
    if manquants:
        raise SystemExit(
            "boite a outils incomplete : " + ", ".join(str(m) for m in manquants)
        )


# -----------------------------------------------------------
# Compilation
# -----------------------------------------------------------

def _processeur():
    try:
        from saxonche import PySaxonProcessor
    except ImportError:
        raise SystemExit(
            "saxonche est absent : pip install -r requirements-dev.txt\n"
            "(SaxonC-HE, moteur XSLT 2.0 ; aucune JVM requise)"
        )
    return PySaxonProcessor


def _transformer(proc, feuille, source, sortie, **params):
    xslt = proc.new_xslt30_processor()
    for nom, valeur in params.items():
        # Les noms de parametres XSLT prennent des points ; on les ecrit
        # avec des tirets bas cote Python et on les retablit ici.
        xslt.set_parameter(
            nom.replace("__", "."),
            proc.make_boolean_value(valeur) if isinstance(valeur, bool)
            else proc.make_string_value(valeur),
        )
    executable = xslt.compile_stylesheet(stylesheet_file=str(feuille))
    executable.transform_to_file(source_file=str(source), output_file=str(sortie))
    if not Path(sortie).exists():
        raise SystemExit(f"{feuille.name} n'a rien produit pour {source}")


def compiler(destination):
    """Produit les trois artefacts dans *destination*.

    Tout est compile dans un repertoire temporaire et n'arrive dans
    *destination* qu'une fois les quatre etapes reussies. Ecrire au fil de
    l'eau laissait, si une etape echouait, un .rng neuf a cote d'un .sch et
    d'un .svrl.xsl de la compilation precedente -- et, dans le cas du
    controle des motifs impossibles, exactement le schema incompilable que
    ce controle existe pour empecher."""
    PySaxonProcessor = _processeur()
    destination.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        atelier = Path(tmp)
        rng = atelier / "alto2tei.rng"
        sch = atelier / "alto2tei.sch"
        svrl = atelier / "alto2tei.svrl.xsl"
        compile_odd = atelier / "alto2tei.compiled.odd"
        with PySaxonProcessor(license=False) as proc:
            # 1. ODD -> ODD compile : les moduleRef sont resolus contre la
            #    P5, les elementSpec mode="change" fusionnes dans leur
            #    definition d'origine.
            print("odd2odd")
            _transformer(proc, STYLESHEETS / "odds" / "odd2odd.xsl",
                         ODD, compile_odd, defaultSource=str(P5))

            # 2. -> RelaxNG
            print("odd2relax")
            _transformer(proc, STYLESHEETS / "odds" / "odd2relax.xsl",
                         compile_odd, rng)

            # 2 bis. Les classes TEI que l'elagage a videes sortent d'ici
            # en <notAllowed/>. libxml2 ne les reduit pas utilement -- il
            # y passait plus de dix minutes sans terminer -- alors on
            # applique nous-memes les regles de la spec (RELAX NG 4.19
            #    et 4.20).
            print("simplification des notAllowed")
            grammaire = etree.parse(str(rng))
            elimines = simplifier(grammaire)
            grammaire.write(str(rng), encoding="UTF-8", xml_declaration=True)
            restants = len(list(grammaire.iter(
                "{http://relaxng.org/ns/structure/1.0}notAllowed")))
            print(f"  {elimines} motifs elimines, {restants} restants")
            if restants:
                raise SystemExit(
                    f"{restants} <notAllowed/> ont survecu a la simplification :\n"
                    "le schema serait incompilable par libxml2. Voir "
                    "scripts/rng_simplify.py."
                )

            # 3. -> Schematron ISO
            print(f"extract-isosch (lang={LANG_MESSAGES})")
            _transformer(proc, STYLESHEETS / "odds" / "extract-isosch.xsl",
                         compile_odd, sch, lang=LANG_MESSAGES)

            # 4. -> feuille SVRL executable
            # SchXslt horodate la feuille qu'il produit ; sans cela,
            # deux compilations du meme ODD donneraient deux fichiers
            # differents et --check crierait a la derive a chaque fois.
            print("schxslt")
            _transformer(proc, SCHXSLT / "2.0" / "pipeline-for-svrl.xsl",
                         sch, svrl, schxslt__compile__metadata=False)

        produits = []
        for provisoire in (rng, sch, svrl):
            definitif = destination / provisoire.name
            shutil.copy2(provisoire, definitif)
            produits.append(definitif)

    for produit in produits:
        chemin = (produit.relative_to(RACINE)
                  if produit.is_relative_to(RACINE) else produit)
        print(f"  {chemin} ({produit.stat().st_size / 1024:.0f} Ko)")
    return tuple(produits)


HORODATAGE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def sans_horodatage(texte):
    """Le texte sans la date de generation que les Stylesheets y ecrivent.

    Elles datent le schema produit ; deux compilations du meme ODD
    different donc d'une ligne. C'est la seule difference toleree, comme
    la date de generation l'est dans la comparaison du golden E2E."""
    return HORODATAGE.sub("HORODATAGE", texte)


def verifier():
    """Recompile a cote et compare : sort 1 si les artefacts versionnes ne
    correspondent plus a l'ODD."""
    with tempfile.TemporaryDirectory() as tmp:
        produits = compiler(Path(tmp))
        derive = []
        for produit in produits:
            versionne = SCHEMA / produit.name
            if not versionne.exists():
                derive.append(f"{produit.name} : absent de schema/")
            elif sans_horodatage(produit.read_text(encoding="utf-8")) != \
                    sans_horodatage(versionne.read_text(encoding="utf-8")):
                derive.append(f"{produit.name} : ne correspond plus a l'ODD")
    if derive:
        print("\nDERIVE :")
        for ligne in derive:
            print(f"  {ligne}")
        print("\nRecompiler : venv/bin/python scripts/build_odd.py")
        return 1
    print("\nLes schemas versionnes correspondent a l'ODD.")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    parser.add_argument("--check", action="store_true",
                        help="verifie que les schemas versionnes correspondent a l'ODD")
    parser.add_argument("--refresh", action="store_true",
                        help="re-telecharge la boite a outils")
    args = parser.parse_args(argv)

    if not ODD.exists():
        raise SystemExit(f"ODD introuvable : {ODD}")

    preparer_outils(refresh=args.refresh)
    if args.check:
        return verifier()
    compiler(SCHEMA)
    return 0


if __name__ == "__main__":
    sys.exit(main())
