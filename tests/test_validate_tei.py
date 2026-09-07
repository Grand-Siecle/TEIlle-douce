# Tests de teille_douce/validation/ -- le garde-fou de sortie, jusqu'ici
# a 0 % de couverture (audit 2.6).
#
# Run: venv/bin/python -m pytest tests/test_validate_tei.py -q
import pytest

from pathlib import Path

from teille_douce.cli import app as _app
from teille_douce.cli import validate as _validate
from teille_douce.validation import validate


def main(argv):
    """L'ancienne surface, telle que ces tests l'exercaient.

    `--odd` etait alors optionnel et l'est devenu par defaut : un appel
    qui ne le demandait pas recoit donc `--no-odd`, pour que ces tests
    disent toujours ce qu'ils disaient."""
    if "--odd" not in argv:
        argv = ["--no-odd", *argv]
    parsed = _app.build_parser().parse_args(["validate", *argv])
    raise SystemExit(_validate.execute(parsed))

RACINE = Path(__file__).resolve().parent.parent

TEI_OK = """<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <category xml:id="MainZone"/>
  </teiHeader>
  <sourceDoc>
    <surface xml:id="f1">
      <zone xml:id="zone_1" corresp="#MainZone"/>
    </surface>
  </sourceDoc>
  <text><body><div>
    <pb corresp="#f1" facs="#f1"/>
    <ab corresp="#zone_1"><lb corresp="#zone_1" facs="#zone_1"/>texte</ab>
  </div></body></text>
</TEI>"""


def _ecrire(tmp_path, contenu, nom="doc.tei.xml"):
    p = tmp_path / nom
    p.write_text(contenu, encoding="utf-8")
    return str(p)


def violations_odd(chemin):
    """Les violations Schematron de l'ODD sur un document.

    Les cinq invariants locaux que ce script verifiait en double sont
    desormais enonces une seule fois, dans schema/teille-douce.odd. Les tests
    qui les epinglaient interrogent donc la regle publiee, pas une seconde
    implementation."""
    pytest.importorskip("saxonche")
    from teille_douce.validation import (svrl_violations as erreurs_svrl,
                                     project_schematron as schematron_du_projet)
    _, feuille = schematron_du_projet()
    return [m for m, _ in erreurs_svrl(feuille.transform_to_string(source_file=chemin))]


def test_document_sain_sans_erreur(tmp_path):
    errors, warnings = validate(_ecrire(tmp_path, TEI_OK))
    assert errors == []
    assert warnings == []


def test_corresp_pendant_est_une_erreur(tmp_path):
    """Audit 2.6/5.1 : un @corresp dont la cible n'existe pas (le cas des
    218 #MarginTextZone jamais declares) doit etre signale."""
    contenu = TEI_OK.replace('corresp="#MainZone"', 'corresp="#FantomeZone"')
    errors, _ = validate(_ecrire(tmp_path, contenu))
    assert any("@corresp/@facs" in e and "FantomeZone" in e for e in errors), errors


def test_facs_pendant_est_une_erreur(tmp_path):
    contenu = TEI_OK.replace('facs="#f1"', 'facs="#page_inconnue"')
    errors, _ = validate(_ecrire(tmp_path, contenu))
    assert any("@corresp/@facs" in e for e in errors), errors


def test_xml_id_duplique_est_une_erreur(tmp_path):
    contenu = TEI_OK.replace('xml:id="zone_1"', 'xml:id="f1"', 1)
    errors, _ = validate(_ecrire(tmp_path, contenu))
    assert any("duplicate xml:id" in e for e in errors), errors


def test_xml_id_non_ncname_est_une_erreur(tmp_path):
    contenu = TEI_OK.replace('xml:id="zone_1"', 'xml:id="ark:/12148/xyz"')
    errors, _ = validate(_ecrire(tmp_path, contenu))
    assert any("NCName" in e for e in errors), errors


def test_main_isole_les_fichiers_illisibles(tmp_path, capsys):
    """Audit 2.6 : un XML mal forme ne doit pas faire crasher le script ;
    il est signale FAIL et les fichiers suivants sont controles."""
    casse = _ecrire(tmp_path, "<TEI>pas ferme", nom="casse.xml")
    sain = _ecrire(tmp_path, TEI_OK, nom="sain.xml")

    with pytest.raises(SystemExit) as exc:
        main([casse, sain])

    assert exc.value.code == 1
    sortie = capsys.readouterr().out
    assert "[FAIL]" in sortie and "casse.xml" in sortie
    assert "[ok]" in sortie and "sain.xml" in sortie


def test_main_exit_zero_quand_tout_est_sain(tmp_path):
    sain = _ecrire(tmp_path, TEI_OK)
    with pytest.raises(SystemExit) as exc:
        main([sain])
    assert exc.value.code == 0


# Mini-schema RelaxNG : n'accepte qu'un <TEI> (ns TEI) ne contenant que
# des <teiHeader> vides — suffisant pour tester le branchement --schema
# sans versionner le tei_all.rng de 1 Mo.
MINI_RNG = """<grammar xmlns="http://relaxng.org/ns/structure/1.0"
         ns="http://www.tei-c.org/ns/1.0">
  <start>
    <element name="TEI">
      <zeroOrMore><element name="teiHeader"><empty/></element></zeroOrMore>
    </element>
  </start>
</grammar>"""

TEI_MINI_VALIDE = '<TEI xmlns="http://www.tei-c.org/ns/1.0"><teiHeader/></TEI>'
TEI_MINI_INVALIDE = '<TEI xmlns="http://www.tei-c.org/ns/1.0"><intrus/></TEI>'


def test_schema_relaxng_violations_sont_des_erreurs(tmp_path, capsys):
    """Audit 2.6 : --schema ajoute la validation RelaxNG, et une violation
    du schema est une ERROR qui fait sortir en code 1."""
    rng = _ecrire(tmp_path, MINI_RNG, nom="mini.rng")
    invalide = _ecrire(tmp_path, TEI_MINI_INVALIDE, nom="invalide.xml")

    with pytest.raises(SystemExit) as exc:
        main(["--schema", rng, invalide])

    assert exc.value.code == 1
    assert "RelaxNG" in capsys.readouterr().out


def test_schema_relaxng_document_conforme_passe(tmp_path):
    rng = _ecrire(tmp_path, MINI_RNG, nom="mini.rng")
    valide = _ecrire(tmp_path, TEI_MINI_VALIDE, nom="valide.xml")

    with pytest.raises(SystemExit) as exc:
        main(["--schema", rng, valide])

    assert exc.value.code == 0


# =============================================================================
# Modes d'echec historiques du pipeline (branches restantes du validateur)
# =============================================================================

def test_points_mal_formes_et_suspects(tmp_path):
    """Les coordonnees de <path> : paires x,y attendues, et une colonne
    entiere de x=0 trahit une conversion ratee."""
    mal_forme = TEI_OK.replace(
        '<zone xml:id="zone_1" corresp="#MainZone"/>',
        '<zone xml:id="zone_1" corresp="#MainZone"><path points="12,34 pasunepaire"/></zone>',
    )
    errors, _ = validate(_ecrire(tmp_path, mal_forme, nom="pts.xml"))
    assert any("malformed points" in e for e in errors), errors

    tous_x_zero = TEI_OK.replace(
        '<zone xml:id="zone_1" corresp="#MainZone"/>',
        '<zone xml:id="zone_1" corresp="#MainZone"><path points="0,10 0,20 0,30"/></zone>',
    )
    errors, _ = validate(_ecrire(tmp_path, tous_x_zero, nom="pts0.xml"))
    assert any("suspicious points" in e for e in errors), errors



def test_url_iiif_construite_sur_le_nom_de_fichier_est_une_erreur(tmp_path):
    """Un @source bati sur le nom de dossier ("_reconciled") ne resout
    jamais cote serveur IIIF."""
    contenu = TEI_OK.replace(
        '<surface xml:id="f1">',
        '<surface xml:id="f1" source="https://gallica.bnf.fr/iiif/LIV0001_reconciled/f1">',
    )
    errors, _ = validate(_ecrire(tmp_path, contenu, nom="teille_douce.xml"))
    assert any("IIIF URL built on the file name" in e for e in errors), errors





TEI_FIGURE = """<TEI xmlns="http://www.tei-c.org/ns/1.0">
  <teiHeader>
    <category xml:id="GraphicZone"/>
  </teiHeader>
  <sourceDoc>
    <surface xml:id="f1">
      <zone xml:id="zone_g" type="GraphicZone" corresp="#GraphicZone"
            source="https://iiif/f1/crop.jpg"/>
    </surface>
  </sourceDoc>
  <text><body><div>
    <pb corresp="#f1" facs="#f1"/>
    <figure corresp="#zone_g" facs="#zone_g" type="GraphicZone">
      <graphic url="https://iiif/f1/crop.jpg"/>
    </figure>
  </div></body></text>
</TEI>"""


def test_figure_conforme_ne_leve_rien(tmp_path):
    errors, warnings = validate(_ecrire(tmp_path, TEI_FIGURE))
    assert errors == []
    assert warnings == []


def test_graphiczone_sans_figure_est_une_erreur(tmp_path):
    """Une illustration presente dans le sourceDoc mais absente du texte :
    c'est exactement l'etat d'avant, et rien ne le signalait."""
    contenu = TEI_FIGURE.replace(
        """<figure corresp="#zone_g" facs="#zone_g" type="GraphicZone">
      <graphic url="https://iiif/f1/crop.jpg"/>
    </figure>""",
        '<ab corresp="#zone_g"/>',
    )
    errors, _ = validate(_ecrire(tmp_path, contenu))
    assert any("GraphicZone with no <figure>" in e for e in errors), errors


def test_figure_sans_graphic_est_une_erreur_quand_le_crop_existe(tmp_path):
    contenu = TEI_FIGURE.replace(
        '<graphic url="https://iiif/f1/crop.jpg"/>', "<ab/>"
    )
    errors, _ = validate(_ecrire(tmp_path, contenu))
    assert any("with no <graphic url>" in e for e in errors), errors


def test_figure_sans_graphic_est_normale_quand_la_zone_n_a_pas_de_crop(tmp_path):
    """Sans mapping IIIF le sourceDoc n'a pas de @source : la <figure>
    ancree par @corresp seul est la sortie normale du pipeline, et le
    validateur ne doit pas contredire le constructeur."""
    contenu = TEI_FIGURE.replace(' source="https://iiif/f1/crop.jpg"', "", 1)
    contenu = contenu.replace('<graphic url="https://iiif/f1/crop.jpg"/>', "")
    errors, warnings = validate(_ecrire(tmp_path, contenu))
    assert errors == []
    assert warnings == []



# =============================================================================
# Validation contre le schema du projet (schema/teille-douce.odd)
# =============================================================================

SVRL_NS = "http://purl.oclc.org/dsdl/svrl"


def _rapport_svrl(corps):
    return (f'<svrl:schematron-output xmlns:svrl="{SVRL_NS}">'
            f"{corps}</svrl:schematron-output>")


def test_un_rapport_svrl_sans_echec_ne_donne_aucune_erreur():
    from teille_douce.validation import svrl_violations as erreurs_svrl
    assert erreurs_svrl(_rapport_svrl(
        '<svrl:fired-rule context="tei:zone"/>')) == []


def test_un_echec_svrl_devient_une_erreur_situee():
    """Schematron distingue l'assertion non tenue du rapport declenche ;
    les deux sont des violations pour nous, et le message doit dire ou."""
    from teille_douce.validation import svrl_violations as erreurs_svrl
    corps = (
        '<svrl:failed-assert location="/tei:TEI/tei:text[1]">'
        "<svrl:text>A figure must carry @corresp.</svrl:text>"
        "</svrl:failed-assert>"
        '<svrl:successful-report location="/tei:TEI/tei:teiHeader[1]">'
        "<svrl:text>langUsage mixes prose and language elements.</svrl:text>"
        "</svrl:successful-report>"
    )
    erreurs = erreurs_svrl(_rapport_svrl(corps))
    assert len(erreurs) == 2
    (premier, role1), (second, _) = erreurs
    assert "figure must carry @corresp" in premier
    assert "tei:text[1]" in premier
    assert role1 == "fatal", "sans @role, une violation est fatale"
    assert "langUsage mixes prose" in second


def test_le_texte_svrl_est_ramene_sur_une_ligne():
    """Les messages ecrits dans l'ODD sont indentes sur plusieurs lignes ;
    tels quels ils casseraient l'affichage en une erreur par ligne."""
    from teille_douce.validation import svrl_violations as erreurs_svrl
    corps = ('<svrl:failed-assert location="/tei:TEI">'
             "<svrl:text>\n     un message\n     coupe en trois\n   </svrl:text>"
             "</svrl:failed-assert>")
    ((erreur, _),) = erreurs_svrl(_rapport_svrl(corps))
    assert "un message coupe en trois" in erreur
    assert "\n" not in erreur


def test_les_violations_sortent_dans_l_ordre_du_document():
    """Elles etaient groupees par type : toutes les assertions, puis tous
    les rapports. Sur un document annote, les 63 echecs d'inventaire
    passaient devant et le plafond d'affichage rendait les quatre regles
    ecrites en <sch:report> litteralement inatteignables."""
    from teille_douce.validation import svrl_violations as erreurs_svrl
    corps = (
        '<svrl:successful-report location="/a"><svrl:text>premier</svrl:text></svrl:successful-report>'
        '<svrl:failed-assert location="/b"><svrl:text>deuxieme</svrl:text></svrl:failed-assert>'
        '<svrl:successful-report location="/c"><svrl:text>troisieme</svrl:text></svrl:successful-report>'
    )
    messages = [m for m, _ in erreurs_svrl(_rapport_svrl(corps))]
    assert ["premier" in messages[0], "deuxieme" in messages[1],
            "troisieme" in messages[2]] == [True, True, True], messages


def test_une_regle_nonfatale_de_la_tei_est_un_avertissement():
    """La TEI marque trois de ses propres regles role="nonfatal" ; les
    traiter comme fatales ferait echouer un document sur un avertissement
    qui n'est pas le notre."""
    from teille_douce.validation import svrl_violations as erreurs_svrl
    corps = ('<svrl:report location="/a"/>'
             '<svrl:failed-assert location="/b" role="nonfatal">'
             "<svrl:text>usage of deprecated attribute</svrl:text></svrl:failed-assert>")
    ((_, role),) = erreurs_svrl(_rapport_svrl(corps))
    assert role == "nonfatal"


def test_odd_sans_schema_compile_le_dit_au_lieu_de_planter(tmp_path, monkeypatch):
    """--odd sur un depot ou build_odd.py n'a jamais tourne doit nommer la
    commande a lancer, pas echouer sur un fichier introuvable."""
    import teille_douce.validation.schemas as vt
    monkeypatch.setattr(vt, "ODD_RNG", tmp_path / "absent.rng")
    with pytest.raises(SystemExit) as leve:
        main(["--odd", _ecrire(tmp_path, TEI_OK)])
    assert "odd build" in str(leve.value)


def test_schematron_du_projet_signale_une_feuille_absente(tmp_path, monkeypatch):
    import teille_douce.validation.schemas as vt
    pytest.importorskip("saxonche")
    monkeypatch.setattr(vt, "ODD_SVRL", tmp_path / "absent.xsl")
    from teille_douce.validation import Missing

    with pytest.raises(Missing) as leve:
        vt.project_schematron()
    assert "odd build" in str(leve.value)


def test_odd_valide_une_sortie_du_pipeline(tmp_path, capsys):
    """Le chemin nominal de --odd, de bout en bout : le golden, sa date de
    generation remise, doit passer RelaxNG et Schematron."""
    pytest.importorskip("saxonche")
    import teille_douce.validation.schemas as vt
    if not vt.ODD_RNG.exists():
        pytest.skip("schema/teille-douce.rng absent — lancer teille-douce odd build")
    golden = (RACINE / "tests" / "fixtures" / "golden" / "LIV9001_court.tei.xml")
    doc = tmp_path / "doc.tei.xml"
    doc.write_text(golden.read_text(encoding="utf-8").replace(
        'when="DATE-GENERATION"', 'when="2026-09-03"'), encoding="utf-8")
    with pytest.raises(SystemExit) as leve:
        main(["--odd", str(doc)])
    assert leve.value.code == 0, capsys.readouterr().out


# =============================================================================
# Les cinq invariants locaux, desormais enonces par l'ODD seul
# =============================================================================

def test_idno_iiif_non_splitte(tmp_path):
    """Une cellule CSV peut porter plusieurs manifestes separes par une
    barre ; non decoupes, ils forment une adresse que rien ne resout."""
    contenu = TEI_OK.replace(
        "<category xml:id=\"MainZone\"/>",
        "<category xml:id=\"MainZone\"/>"
        "<idno type=\"iiif\">https://a/manifest|https://b/manifest</idno>")
    violations = violations_odd(_ecrire(tmp_path, contenu))
    assert any("IIIF idno" in v for v in violations), violations


def test_orcid_gabarit(tmp_path):
    contenu = TEI_OK.replace(
        "<category xml:id=\"MainZone\"/>",
        "<category xml:id=\"MainZone\"/>"
        "<idno type=\"orcid\">0000-0000-0000-0000</idno>")
    violations = violations_odd(_ecrire(tmp_path, contenu))
    assert any("placeholder ORCID" in v for v in violations), violations


def test_cesure_residuelle_dans_reg(tmp_path):
    """La regle de l'ODD lit la valeur textuelle entiere : elle voit le ¬
    d'un <reg> enrichi, dont le texte vit dans des <w> enfants -- ce que
    le controle Python, qui lisait el.text, manquait."""
    contenu = TEI_OK.replace(
        "texte</ab>",
        "<choice><orig>ma¬in</orig><reg><w>ma¬in</w></reg></choice></ab>")
    violations = violations_odd(_ecrire(tmp_path, contenu))
    assert any("line-break hyphen" in v for v in violations), violations


def test_prose_dans_langusage(tmp_path):
    contenu = TEI_OK.replace(
        "<category xml:id=\"MainZone\"/>",
        "<category xml:id=\"MainZone\"/><langUsage><p>methode</p>"
        "<language ident=\"fra\" usage=\"100\" n=\"10 words\">French</language></langUsage>")
    violations = violations_odd(_ecrire(tmp_path, contenu))
    assert any("langUsage mixes" in v for v in violations), violations


def test_graphiczone_sans_xml_id(tmp_path):
    contenu = TEI_OK.replace(
        "<zone xml:id=\"zone_1\" corresp=\"#MainZone\"/>",
        "<zone type=\"GraphicZone\"/>")
    violations = violations_odd(_ecrire(tmp_path, contenu))
    assert any("GraphicZone must carry an xml:id" in v for v in violations), violations


def test_un_document_sain_ne_declenche_aucune_regle_de_l_odd(tmp_path):
    assert violations_odd(_ecrire(tmp_path, TEI_OK)) == []


def test_les_invariants_locaux_ne_sont_plus_rediscutes_en_python(tmp_path):
    """Cinq invariants existaient en double : une fois ici, une fois dans
    l'ODD. Les deux versions divergeaient — severite differente, portee
    differente, et la version Python du ¬ manquait les <reg> enrichis. Le
    meme fichier etait donc « ok », « avec avertissements » ou « en echec »
    selon la commande tapee. L'ODD est desormais seul a les enoncer."""
    contenu = TEI_OK.replace(
        '<category xml:id="MainZone"/>',
        '<category xml:id="MainZone"/>'
        '<idno type="iiif">https://a|https://b</idno>'
        '<idno type="orcid">0000-0000-0000-0000</idno>'
        '<langUsage><p>methode</p><language ident="fra">French</language></langUsage>'
    ).replace("texte</ab>", "<reg>ma¬in</reg></ab>")
    errors, warnings = validate(_ecrire(tmp_path, contenu))
    tout = " ".join(errors + warnings)
    for disparu in ("iiif non splitté", "ORCID placeholder", "melange", "¬ résiduel"):
        assert disparu not in tout, f"controle encore double en Python : {disparu}"


def test_sans_odd_le_script_dit_ce_qu_il_n_a_pas_verifie(tmp_path, capsys):
    """Cinq invariants ne vivent plus que dans l'ODD. Un appel sans --odd
    ne les verifie donc pas, et se taire la-dessus laisserait croire a un
    controle complet."""
    with pytest.raises(SystemExit):
        main([_ecrire(tmp_path, TEI_OK)])
    sortie = capsys.readouterr().out
    assert "--odd" in sortie
    assert "unchecked" in sortie.lower()


def test_odd_sans_saxonche_valide_quand_meme_le_relaxng(tmp_path, capsys, monkeypatch):
    """saxonche absent ne doit pas emporter la validation RelaxNG, qui ne
    depend que de lxml et d'un schema versionne."""
    import teille_douce.validation.schemas as vt
    if not vt.ODD_RNG.exists():
        pytest.skip("schema/teille-douce.rng absent — lancer teille-douce odd build")

    from teille_douce.validation import Missing

    def pas_de_saxon(*_):
        raise Missing("saxonche is not installed")

    # Ce que la disponibilite decide desormais dans le parent, et ce que
    # le worker tente : le premier imprime la note, le second se passe du
    # Schematron sans emporter le RelaxNG.
    monkeypatch.setattr(vt, "project_schematron", pas_de_saxon)
    monkeypatch.setattr(_validate, "available", lambda odd: (
        False, "note: Schematron not applied (saxonche is not installed) — "
               "only teille-douce.rng was used"))

    with pytest.raises(SystemExit):
        main(["--odd", _ecrire(tmp_path, TEI_OK)])
    sortie = capsys.readouterr().out
    assert "Schematron" in sortie and "saxonche" in sortie
    assert "teille-douce.rng" in sortie, "le RelaxNG doit avoir ete applique"


# Chaque regle publiee par l'ODD doit pouvoir etre enfreinte, et l'echec
# doit se voir. Quatre d'entre elles n'etaient declenchees par aucun test
# — et l'histoire de cette PR montre qu'une regle peut disparaitre du
# Schematron produit sans que rien ne le signale.
INFRACTIONS = {
    "language-quantite": (
        '<category xml:id="MainZone"/>',
        '<category xml:id="MainZone"/><langUsage>'
        '<language ident="fra" usage="100" n="10">French</language></langUsage>',
        "@n on language"),
    "choice-orig-reg": (
        "texte</ab>", "<choice><orig>ancien</orig></choice></ab>",
        "exactly one orig"),
    "entite-automatique-datee": (
        "texte</ab>", '<persName resp="#ner-auto">Poussin</persName></ab>',
        "must carry @cert"),
    "inventaire-ferme": (
        "texte</ab>", "<quote>hors inventaire</quote></ab>",
        "is not part of"),
    "coordonnees-bien-formees": (
        '<zone xml:id="zone_1" corresp="#MainZone"/>',
        '<zone xml:id="zone_1" corresp="#MainZone" points="12,3 pasunpoint"/>',
        "whitespace-separated x,y pairs"),
}


@pytest.mark.parametrize("regle", list(INFRACTIONS))
def test_chaque_regle_de_l_odd_se_declenche_quand_on_l_enfreint(regle, tmp_path):
    avant, apres, attendu = INFRACTIONS[regle]
    contenu = TEI_OK.replace(avant, apres, 1)
    violations = violations_odd(_ecrire(tmp_path, contenu))
    assert any(attendu in v for v in violations), (regle, violations)


def test_des_coordonnees_valides_ne_declenchent_rien(tmp_path):
    """La regle sur @points remplace un type TEI qui coutait 55 des 57
    secondes de validation d'un document de 20 000 elements : elle doit
    etre exacte, pas seulement rapide."""
    for valeur in ("12,3", "12,3 45,6", "-1,-2 3.5,4.25 0,0"):
        contenu = TEI_OK.replace(
            '<zone xml:id="zone_1" corresp="#MainZone"/>',
            f'<zone xml:id="zone_1" corresp="#MainZone" points="{valeur}"/>')
        assert violations_odd(_ecrire(tmp_path, contenu, "p.xml")) == [], valeur


def test_les_fichiers_se_controlent_en_parallele(tmp_path, capsys):
    """Les documents sont independants et la validation est longue : sur un
    corpus, le seul levier qui compte est de ne pas les faire l'un apres
    l'autre. Le resultat doit etre identique, dans le meme ordre."""
    fichiers = [_ecrire(tmp_path, TEI_OK, f"doc{i}.xml") for i in range(4)]
    fichiers.append(_ecrire(tmp_path, TEI_OK.replace('xml:id="zone_1"',
                                                     'xml:id="ark:/12148/x"'),
                            "fautif.xml"))
    with pytest.raises(SystemExit):
        main(["--jobs", "2", *fichiers])
    parallele = capsys.readouterr().out
    with pytest.raises(SystemExit):
        main(fichiers)
    sequentiel = capsys.readouterr().out
    assert parallele == sequentiel
    assert "NCName" in parallele
