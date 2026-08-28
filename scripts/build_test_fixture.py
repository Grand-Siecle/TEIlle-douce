#!/usr/bin/env python3
"""
Construit la fixture ALTO minimale versionnee sous tests/fixtures/alto_min/.

Part de 6 pages reelles du corpus (dossier OCR/, non versionne) et les allege
jusqu'a quelques dizaines de Ko, en preservant tout ce que le pipeline doit
savoir traiter. Les 6 pages ont ete choisies par couverture minimale des labels
SegmOnto presents dans le corpus : 11 labels distincts en 5 pages, plus une page
apportant un appel de note et une page portant une cesure par trait d'union.

Ce script n'est utile que pour REGENERER la fixture ; il exige le corpus prive.
La fixture produite, elle, est versionnee et suffit a faire tourner les tests.

Usage : venv/bin/python scripts/build_test_fixture.py
"""

import re
import shutil
from pathlib import Path

from lxml import etree

NS_ALTO = "http://www.loc.gov/standards/alto/ns-v4#"
NS = {"a": NS_ALTO}

RACINE = Path(__file__).resolve().parent.parent
SOURCE = RACINE / "OCR"
CIBLE = RACINE / "tests" / "fixtures" / "alto_min" / "LIV9001_reconciled"

# (chemin relatif dans OCR_test, nom cible, ce que la page apporte)
PAGES = [
    ("LIV0039b_reconciled/content/data/doc_1/f874.xml", "f1.xml",
     "MainZone, MarginTextZone, DropCapitalZone, NumberingZone, QuireMarksZone, RunningTitleZone, default, cesure ¬"),
    ("LIV0039b_reconciled/content/data/doc_1/f533.xml", "f2.xml", "GraphicZone"),
    ("LIV0044_reconciled/content/data/doc_1/f3.xml", "f3.xml", "TitlePageZone"),
    ("LIV0044_reconciled/content/data/doc_1/f188.xml", "f4.xml", "appel de note + MarginTextZone"),
    ("LIV0044_reconciled/content/data/doc_1/f190.xml", "f5.xml", "StampZone"),
    ("LIV0039b_reconciled/content/data/doc_1/f141.xml", "f6.xml", "CustomZone, cesure ¬"),
    ("LIV0039a_reconciled/content/data/doc_1/f346.xml", "f7.xml", "cesure par trait d'union simple (mainte-)"),
    # Seule page du corpus (54 documents) portant un label de ligne autre que
    # DefaultLine : un unique HeadingLine, qui exerce la branche <hi> de
    # body/builder.py:430. DropCapitalLine n'est applique nulle part.
    ("LIV0042_reconciled/content/data/doc_1/f560.xml", "f8.xml", "HeadingLine -> branche <hi>"),
]

# Une ligne est conservee d'office si son texte porte un trait qu'on veut tester.
INTERESSANT = re.compile(r"[¬*†‡]|-$")
LIGNES_PAR_BLOC = 2      # lignes ordinaires conservees par bloc
INTERESSANTES_PAR_BLOC = 2  # + au plus une ligne portant un trait a tester
POINTS_POLYGONE = 4      # coordonnees conservees par Polygon
GLYPHES_PAR_STRING = 2   # pour garder la couverture GC/WC sans le volume


def alleger_polygone(el):
    for poly in el.iter(f"{{{NS_ALTO}}}Polygon"):
        pts = (poly.get("POINTS") or "").split()
        if len(pts) > POINTS_POLYGONE * 2:
            poly.set("POINTS", " ".join(pts[: POINTS_POLYGONE * 2]))


def texte_de_ligne(line):
    return " ".join(s.get("CONTENT", "") for s in line.iter(f"{{{NS_ALTO}}}String"))


def carte_tags(racine):
    return {
        t.get("ID"): t.get("LABEL")
        for t in racine.iter(f"{{{NS_ALTO}}}OtherTag")
        if t.get("ID")
    }


def ligne_typee(ligne, tags):
    """Vrai si la ligne porte un label SegmOnto autre que la ligne par defaut.

    Ces lignes sont rarissimes dans le corpus (un seul HeadingLine sur
    54 documents) mais commandent la branche <hi> de body/builder.py :
    elles sont conservees d'office, quel que soit le plafond.
    """
    for ref in (ligne.get("TAGREFS") or "").split():
        label = tags.get(ref, "")
        if label.endswith("Line") and label != "DefaultLine":
            return True
    return False


def alleger_page(chemin_src, nom_cible, numero):
    arbre = etree.parse(str(chemin_src), etree.XMLParser(huge_tree=True, remove_blank_text=False))
    racine = arbre.getroot()

    # Le nom d'image doit suivre le renumerotage.
    for fn in racine.iter(f"{{{NS_ALTO}}}fileName"):
        fn.text = f"{Path(nom_cible).stem}.jpg"
    for page in racine.iter(f"{{{NS_ALTO}}}Page"):
        page.set("PHYSICAL_IMG_NR", str(numero - 1))

    tags = carte_tags(racine)
    for bloc in racine.iter(f"{{{NS_ALTO}}}TextBlock"):
        lignes = bloc.findall(f"a:TextLine", namespaces=NS)
        gardees, ordinaires, speciales = [], 0, 0
        for ligne in lignes:
            if ligne_typee(ligne, tags):
                gardees.append(ligne)
                continue
            special = bool(INTERESSANT.search(texte_de_ligne(ligne)))
            if special and speciales < INTERESSANTES_PAR_BLOC:
                gardees.append(ligne)
                speciales += 1
            elif not special and ordinaires < LIGNES_PAR_BLOC:
                gardees.append(ligne)
                ordinaires += 1
        for ligne in lignes:
            if ligne not in gardees:
                bloc.remove(ligne)

    # Glyphes : on n'en garde que quelques-uns, ils font tout le volume.
    for s in racine.iter(f"{{{NS_ALTO}}}String"):
        glyphes = s.findall(f"a:Glyph", namespaces=NS)
        for g in glyphes[GLYPHES_PAR_STRING:]:
            s.remove(g)

    alleger_polygone(racine)

    CIBLE.mkdir(parents=True, exist_ok=True)
    dest = CIBLE / "content" / "data" / "doc_1" / nom_cible
    dest.parent.mkdir(parents=True, exist_ok=True)
    arbre.write(str(dest), encoding="UTF-8", xml_declaration=True, pretty_print=True)
    return dest.stat().st_size


def main():
    if not SOURCE.exists():
        raise SystemExit(f"corpus source introuvable : {SOURCE}")
    if CIBLE.exists():
        shutil.rmtree(CIBLE)

    total = 0
    print("Construction de la fixture ALTO minimale\n")
    for i, (rel, cible, apport) in enumerate(PAGES, 1):
        src = SOURCE / rel
        if not src.exists():
            raise SystemExit(f"page source manquante : {src}")
        avant = src.stat().st_size
        apres = alleger_page(src, cible, i)
        total += apres
        print(f"  {cible}  {avant/1024:7.0f} Ko -> {apres/1024:5.1f} Ko   {apport}")

    # Mapping IIIF : trois colonnes, sans en-tete (format attendu par IIIFMapping).
    csv = CIBLE / "gallica-bnf-fr-iiif-manifest-json.csv"
    csv.write_text(
        "".join(
            f"https://gallica.bnf.fr/iiif/ark:/12148/bpt6k9001/f{i}/full/full/0/native.jpg,bpt6k9001,f{i}\n"
            for i in range(1, len(PAGES) + 1)
        ),
        encoding="utf-8",
    )
    print(f"\n  mapping IIIF : {csv.name} ({len(PAGES)} entrees)")
    print(f"\nTotal fixture : {total/1024:.1f} Ko")


def regenerer_golden():
    """Regenere la sortie de reference du mode court."""
    import sys as _sys
    _sys.path.insert(0, str(RACINE / "tests"))
    import tempfile
    from test_e2e_pipeline import GOLDEN, MODE_COURT, lancer_pipeline, normaliser

    tei = lancer_pipeline(Path(tempfile.mkdtemp()), **MODE_COURT)
    GOLDEN.parent.mkdir(parents=True, exist_ok=True)
    GOLDEN.write_text(normaliser(tei, sans_taxonomie=True), encoding="utf-8")
    print(f"golden ecrit : {GOLDEN.relative_to(RACINE)} "
          f"({GOLDEN.stat().st_size / 1024:.1f} Ko)")


if __name__ == "__main__":
    import sys
    if "--golden" in sys.argv:
        regenerer_golden()
    else:
        main()
