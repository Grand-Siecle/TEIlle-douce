# -----------------------------------------------------------
# Validates TEI outputs against the known failure modes of this
# pipeline. Usage: venv/bin/python scripts/validate_tei.py tei_test/*.xml
# Optional: --odd valide contre le schema du projet (schema/alto2tei.rng
# et schema/alto2tei.svrl.xsl, tous deux versionnes, derives de
# schema/alto2tei.odd) : le TEI que cette chaine emet, et rien d'autre.
# Optional: --schema chemin/vers/tei_all.rng ajoute la validation RelaxNG
# complete (les violations sont des ERROR). Le schema n'est pas versionne
# ici (~1 Mo, https://tei-c.org/release/xml/tei/custom/schema/relaxng/) ;
# etat des lieux 2026-08-30 : la sortie du pipeline valide tei_all sans
# erreur, en mode court comme en run complet (NER compris) ; les tests
# e2e le verifient des qu'un schema est disponible.
# Exit 1 if any ERROR.
# -----------------------------------------------------------
import argparse
import re
import sys
from pathlib import Path

from lxml import etree

SVRL = "http://purl.oclc.org/dsdl/svrl"
RACINE = Path(__file__).resolve().parent.parent
ODD_RNG = RACINE / "schema" / "alto2tei.rng"
ODD_SVRL = RACINE / "schema" / "alto2tei.svrl.xsl"

XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
NCNAME = re.compile(r"^[A-Za-z_][A-Za-z0-9._\-]*$")
POINTS_PAIR = re.compile(r"^-?\d+,-?\d+$")

# collect_ids=False: libxml2 otherwise rejects xml:id values that aren't
# NCNames at *parse time* (XMLSyntaxError), before we ever get to inspect
# the tree. Some old buggy outputs have xml:id="ark:/12148/..." (colons and
# slashes), which is exactly the failure mode this script needs to report
# as a normal ERROR line rather than crash on. huge_tree=True matches the
# pipeline's own parser config (src/utils/xml.py) for large documents.
PARSER = etree.XMLParser(collect_ids=False, huge_tree=True)


def erreurs_svrl(rapport):
    """Les violations d'un rapport SVRL, en messages d'une ligne.

    Schematron distingue l'assertion non tenue (`failed-assert`) du rapport
    declenche (`successful-report`) ; l'ODD emploie les deux selon qu'il est
    plus clair d'affirmer ce qui doit etre ou de signaler ce qui ne doit pas.
    Pour un controle de sortie, les deux sont des violations.

    Renvoie des couples (message, role) : la TEI marque trois de ses
    propres regles `role="nonfatal"`, et les traiter comme fatales ferait
    echouer un document sur un avertissement de sa part."""
    if isinstance(rapport, str):
        rapport = etree.fromstring(rapport.encode())
    violations = []
    attendus = {f"{{{SVRL}}}failed-assert", f"{{{SVRL}}}successful-report"}
    # Un seul parcours, dans l'ordre du document : balayer d'abord toutes
    # les assertions puis tous les rapports mettait les seconds derriere
    # les premiers, hors de portee du plafond d'affichage. Sur un document
    # annote, les 63 echecs d'inventaire suffisaient a rendre les quatre
    # regles ecrites en <sch:report> invisibles.
    for el in rapport.iter():
        if not isinstance(el.tag, str) or el.tag not in attendus:
            continue
        texte = " ".join("".join(el.itertext()).split())
        lieu = el.get("location", "")
        message = f"{texte} [{lieu}]" if lieu else texte
        violations.append((message, el.get("role") or "fatal"))
    return violations


def schematron_du_projet():
    """La feuille SVRL compilee depuis l'ODD, prete a etre appliquee.

    Elle est en XSLT 2.0 -- c'est ce que la TEI produit -- donc lxml ne
    peut pas l'executer : il faut saxonche, qui est une dependance de
    developpement et non du pipeline."""
    try:
        from saxonche import PySaxonProcessor
    except ImportError:
        raise SystemExit(
            "--odd demande saxonche pour appliquer le Schematron :\n"
            "  pip install -r requirements-dev.txt"
        )
    if not ODD_SVRL.exists():
        raise SystemExit(
            f"{ODD_SVRL} est absent : venv/bin/python scripts/build_odd.py"
        )
    processeur = PySaxonProcessor(license=False)
    return processeur, processeur.new_xslt30_processor().compile_stylesheet(
        stylesheet_file=str(ODD_SVRL))


def validate(path, relaxng=None, schematron=None):
    errors, warnings = [], []
    tree = etree.parse(path, parser=PARSER)
    root = tree.getroot()

    # Validation de schema complete, si un tei_all.rng est fourni
    if relaxng is not None and not relaxng.validate(tree):
        for err in relaxng.error_log[:20]:
            errors.append(f"RelaxNG L{err.line}: {err.message}")

    # Contraintes de l'ODD, si la feuille SVRL est fournie
    if schematron is not None:
        for message, role in erreurs_svrl(
                schematron.transform_to_string(source_file=path))[:20]:
            if role == "nonfatal":
                warnings.append(f"Schematron: {message}")
            else:
                errors.append(f"Schematron: {message}")

    def local(el):
        return etree.QName(el).localname if isinstance(el.tag, str) else ""

    # Zones graphiques et figures, collectées dans la passe générale
    # ci-dessous plutôt qu'en parcours séparés : validate() visite déjà
    # tout l'arbre, sourceDoc compris.
    graphic_sources = {}   # xml:id de la zone -> son crop IIIF (ou None)
    figures = []           # (cible de @corresp, a-t-il un <graphic url>)

    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        tag = local(el)
        xid = el.get(XML_ID)
        if tag == "zone" and el.get("type") == "GraphicZone":
            if xid:
                graphic_sources[xid] = el.get("source")
        elif tag == "figure":
            figures.append((
                (el.get("corresp") or "").lstrip("#"),
                any(local(c) == "graphic" and c.get("url") for c in el),
            ))
        if xid and not NCNAME.match(xid):
            errors.append(f"xml:id invalide (NCName): {xid!r} sur <{tag}>")
        pts = el.get("points")
        if pts:
            pairs = pts.split()
            bad = [p for p in pairs if not POINTS_PAIR.match(p)]
            if bad:
                errors.append(f"points mal formés sur <{tag}>: {bad[:2]}")
            elif len(pairs) > 2 and all(p.split(",")[0] == "0" for p in pairs):
                errors.append(f"points suspects (tous x=0) sur <{tag} xml:id={xid}>")
        # Cinq invariants locaux -- prose dans <langUsage>, idno IIIF non
        # decoupe, ¬ residuel dans un <reg>, GraphicZone sans xml:id,
        # ORCID de gabarit -- ne sont plus verifies ici : ils sont enonces
        # une seule fois, dans schema/alto2tei.odd, et appliques par la
        # validation Schematron ci-dessus. Les redire en Python entretenait
        # deux versions qui divergeaient sur la severite et la portee.
        src = el.get("source")
        if src and src.startswith("http") and "_reconciled" in src:
            errors.append(f"URL IIIF construite sur le nom de fichier: {src[:70]}")

    # Chaque zone graphique du sourceDoc doit ressortir en <figure> :
    # sans ce contrôle, une illustration redevient invisible dans le corps
    # sans que rien ne le signale.
    figured = {cible for cible, _ in figures}
    manquantes = set(graphic_sources) - figured
    if manquantes:
        errors.append(
            f"{len(manquantes)} GraphicZone sans <figure> dans le texte "
            f"(ex.: {sorted(manquantes)[:2]})"
        )
    # L'image manque seulement si la zone avait un crop IIIF à reprendre :
    # sans mapping IIIF le sourceDoc n'a pas de @source, et une <figure>
    # ancrée par @corresp seul est alors la sortie normale.
    for cible, a_une_image in figures:
        if not a_une_image and graphic_sources.get(cible):
            errors.append(f"<figure corresp=#{cible}> sans <graphic url>")

    tei_id = root.get(XML_ID) or ""
    if tei_id.startswith("ark_"):
        errors.append(f"xml:id racine avec préfixe ark hardcodé: {tei_id}")


    # xml:id dupliqués : invalide, et fatal pour toute résolution de liens.
    # (collect_ids=False sur le parseur, donc c'est à nous de le vérifier.)
    seen_ids, duplicate_ids = set(), []
    for e in root.iter():
        xid = e.get(XML_ID)
        if xid is None:
            continue
        if xid in seen_ids:
            duplicate_ids.append(xid)
        seen_ids.add(xid)
    if duplicate_ids:
        errors.append(
            f"{len(duplicate_ids)} xml:id dupliqués (ex.: {duplicate_ids[:3]})"
        )

    # @ref et @target du body doivent résoudre vers un xml:id du document
    ids = {e.get(XML_ID) for e in root.iter() if e.get(XML_ID)}
    body = root.find(".//{*}text") if root.find(".//{*}text") is not None else root.find(".//text")
    dangling_ref, dangling_target = 0, 0
    if body is not None:
        for el in body.iter():
            for ref in (el.get("ref") or "").split():
                if ref.startswith("#") and ref[1:] not in ids:
                    dangling_ref += 1
            for tgt in (el.get("target") or "").split():
                if tgt.startswith("#") and tgt[1:] not in ids:
                    dangling_target += 1
    if dangling_ref:
        warnings.append(f"{dangling_ref} @ref du body sans cible dans le document")
    if dangling_target:
        errors.append(f"{dangling_target} @target du body sans cible dans le document")

    # @corresp, @facs et @resp — le mécanisme central body->sourceDoc,
    # zones->taxonomie, et l'attribution des annotations automatiques : chaque #ref doit résoudre dans le document.
    # C'est le contrôle qui manquait quand 218 @corresp pendants
    # (#MarginTextZone/#GraphicZone non déclarés) passaient inaperçus.
    dangling_link = 0
    exemples = []
    for el in root.iter():
        for att in ("corresp", "facs", "resp"):
            for ref in (el.get(att) or "").split():
                if ref.startswith("#") and ref[1:] not in ids:
                    dangling_link += 1
                    if len(exemples) < 3:
                        exemples.append(f"{att}={ref}")
    if dangling_link:
        errors.append(
            f"{dangling_link} @corresp/@facs/@resp sans cible dans le document "
            f"(ex.: {exemples})"
        )

    return errors, warnings


def main(paths=None):
    parser = argparse.ArgumentParser(
        description="Contrôle les sorties TEI du pipeline (modes d'échec connus)."
    )
    parser.add_argument("fichiers", nargs="+", help="fichiers TEI à contrôler")
    parser.add_argument(
        "--odd", action="store_true",
        help="valide contre le schema du projet (schema/alto2tei.{rng,svrl.xsl})",
    )
    parser.add_argument(
        "--schema", metavar="RNG",
        help="chemin d'un tei_all.rng : ajoute la validation RelaxNG complète",
    )
    args = parser.parse_args(sys.argv[1:] if paths is None else list(paths))

    relaxng = etree.RelaxNG(etree.parse(args.schema)) if args.schema else None
    schematron = processeur = None
    odd_rng = None
    if args.odd:
        if not ODD_RNG.exists():
            raise SystemExit(
                f"{ODD_RNG} est absent : venv/bin/python scripts/build_odd.py")
        odd_rng = etree.RelaxNG(etree.parse(str(ODD_RNG)))
        try:
            # Le processeur n'est plus utilise ensuite, mais il est garde
            # en vie deliberement : la feuille compilee en depend, et rien
            # ne garantit ce lien d'une version de SaxonC a l'autre.
            processeur, schematron = schematron_du_projet()
        except SystemExit as absent:
            # saxonche manquant n'emporte pas la validation RelaxNG, qui
            # ne demande que lxml et un schema versionne.
            print(f"note: Schematron non applique ({absent}) — "
                  "seul alto2tei.rng a servi")
    else:
        # Cinq invariants locaux ne vivent plus que dans l'ODD ; se taire
        # ici laisserait croire a un controle complet.
        print("note: prose dans <langUsage>, idno IIIF non decoupe, ¬ residuel "
              "dans un <reg>,\n      GraphicZone sans xml:id et ORCID de gabarit "
              "sont enonces par l'ODD :\n      non verifies sans --odd.")

    total_err = 0
    for path in args.fichiers:
        # Un fichier illisible est un échec de CE fichier, pas du script :
        # les suivants sont quand même contrôlés.
        # Saxon leve ses propres exceptions (PySaxonApiError) sur un
        # fichier que lxml accepte : un DOCTYPE introuvable, un
        # imbriquement trop profond, une regle TEI qui bute sur une valeur.
        # Les laisser passer arretait le lot au premier fichier fautif.
        try:
            errors, warnings = validate(path, relaxng=relaxng,
                                        schematron=schematron)
            if odd_rng is not None:
                arbre = etree.parse(path, parser=PARSER)
                if not odd_rng.validate(arbre):
                    errors.extend(
                        f"alto2tei.rng L{e.line}: {e.message}"
                        for e in list(odd_rng.error_log)[:20]
                    )
        except Exception as e:
            errors, warnings = [f"fichier invalide: {type(e).__name__}: {e}"], []
        status = "FAIL" if errors else "ok"
        print(f"[{status}] {path}: {len(errors)} erreurs, {len(warnings)} avertissements")
        for e in errors[:10]:
            print(f"   ERROR {e}")
        for w in warnings[:5]:
            print(f"   warn  {w}")
        total_err += len(errors)
    sys.exit(1 if total_err else 0)


if __name__ == "__main__":
    main()
