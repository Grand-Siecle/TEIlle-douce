# Tests de scripts/validate_tei.py -- le garde-fou de sortie, jusqu'ici
# a 0 % de couverture (audit 2.6).
#
# Run: venv/bin/python -m pytest tests/test_validate_tei.py -q
import pytest

from pathlib import Path

from scripts.validate_tei import main, validate

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
    assert any("dupliqu" in e for e in errors), errors


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
    assert any("points mal form" in e for e in errors), errors

    tous_x_zero = TEI_OK.replace(
        '<zone xml:id="zone_1" corresp="#MainZone"/>',
        '<zone xml:id="zone_1" corresp="#MainZone"><path points="0,10 0,20 0,30"/></zone>',
    )
    errors, _ = validate(_ecrire(tmp_path, tous_x_zero, nom="pts0.xml"))
    assert any("suspects" in e for e in errors), errors


def test_idno_iiif_non_splitte_est_une_erreur(tmp_path):
    """Plusieurs manifestes doivent devenir plusieurs <idno>, jamais une
    seule valeur separee par des barres."""
    contenu = TEI_OK.replace(
        "<category xml:id=\"MainZone\"/>",
        '<category xml:id="MainZone"/><idno type="iiif">https://a/manifest.json|https://b/manifest.json</idno>',
    )
    errors, _ = validate(_ecrire(tmp_path, contenu, nom="iiif.xml"))
    assert any("idno iiif non split" in e for e in errors), errors


def test_url_iiif_construite_sur_le_nom_de_fichier_est_une_erreur(tmp_path):
    """Un @source bati sur le nom de dossier ("_reconciled") ne resout
    jamais cote serveur IIIF."""
    contenu = TEI_OK.replace(
        '<surface xml:id="f1">',
        '<surface xml:id="f1" source="https://gallica.bnf.fr/iiif/LIV0001_reconciled/f1">',
    )
    errors, _ = validate(_ecrire(tmp_path, contenu, nom="src.xml"))
    assert any("URL IIIF construite" in e for e in errors), errors


def test_orcid_placeholder_et_prefixe_ark_racine(tmp_path):
    contenu = TEI_OK.replace(
        "<category xml:id=\"MainZone\"/>",
        '<category xml:id="MainZone"/><ptr target="https://orcid.org/0000-0000-0000-0000"/>',
    )
    errors, _ = validate(_ecrire(tmp_path, contenu, nom="orcid.xml"))
    assert any("ORCID placeholder" in e for e in errors), errors

    racine_ark = TEI_OK.replace(
        '<TEI xmlns="http://www.tei-c.org/ns/1.0">',
        '<TEI xmlns="http://www.tei-c.org/ns/1.0" xml:id="ark_12148_bpt6k9001">',
    )
    errors, _ = validate(_ecrire(tmp_path, racine_ark, nom="ark.xml"))
    assert any("prefixe ark hardcode" in e or "ark hardcod" in e for e in errors), errors


def test_hyphen_residuel_dans_reg_est_un_avertissement(tmp_path):
    """Un ¬ survivant dans un <reg> signale une de-hyphenation ratee ;
    c'est un avertissement, pas une erreur bloquante."""
    contenu = TEI_OK.replace(
        "<ab corresp=\"#zone_1\">",
        '<ab corresp="#zone_1"><choice><orig>mot¬</orig><reg>mot¬ coupe</reg></choice>',
    )
    errors, warnings = validate(_ecrire(tmp_path, contenu, nom="reg.xml"))
    assert any("dans reg" in w for w in warnings), warnings
    assert errors == []


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
    assert any("GraphicZone sans <figure>" in e for e in errors), errors


def test_figure_sans_graphic_est_une_erreur_quand_le_crop_existe(tmp_path):
    contenu = TEI_FIGURE.replace(
        '<graphic url="https://iiif/f1/crop.jpg"/>', "<ab/>"
    )
    errors, _ = validate(_ecrire(tmp_path, contenu))
    assert any("sans <graphic url>" in e for e in errors), errors


def test_figure_sans_graphic_est_normale_quand_la_zone_n_a_pas_de_crop(tmp_path):
    """Sans mapping IIIF le sourceDoc n'a pas de @source : la <figure>
    ancree par @corresp seul est la sortie normale du pipeline, et le
    validateur ne doit pas contredire le constructeur."""
    contenu = TEI_FIGURE.replace(' source="https://iiif/f1/crop.jpg"', "", 1)
    contenu = contenu.replace('<graphic url="https://iiif/f1/crop.jpg"/>', "")
    errors, warnings = validate(_ecrire(tmp_path, contenu))
    assert errors == []
    assert warnings == []


def test_zone_graphique_sans_xml_id_ne_fait_pas_planter_la_validation(tmp_path):
    """Deux zones sans identifiant faisaient lever un TypeError au tri,
    et la traceback emportait tous les fichiers suivants de la ligne de
    commande."""
    contenu = TEI_FIGURE.replace(
        '<zone xml:id="zone_g" type="GraphicZone" corresp="#GraphicZone"\n            source="https://iiif/f1/crop.jpg"/>',
        '<zone type="GraphicZone" corresp="#GraphicZone"/>\n'
        '      <zone type="GraphicZone" corresp="#GraphicZone"/>\n'
        '      <zone xml:id="zone_g" type="GraphicZone" corresp="#GraphicZone" source="https://iiif/f1/crop.jpg"/>',
    )
    errors, warnings = validate(_ecrire(tmp_path, contenu))
    assert errors == []
    assert any("sans xml:id" in w for w in warnings), warnings


def test_prose_dans_langusage_est_une_erreur(tmp_path):
    """TEI donne a <langUsage> un modele de contenu par choix,
    (model.pLike+ | language+) : y remettre le paragraphe de methode a
    cote des <language> produirait un document invalide. Le garde-fou
    doit le dire sans qu'un tei_all.rng soit fourni."""
    contenu = TEI_OK.replace(
        "<category xml:id=\"MainZone\"/>",
        "<category xml:id=\"MainZone\"/>"
        "<langUsage><p>Methode.</p><language ident=\"fra\">French</language></langUsage>",
    )
    errors, _ = validate(_ecrire(tmp_path, contenu))
    assert any("langUsage" in e for e in errors), errors


def test_langusage_de_langues_seules_ne_leve_rien(tmp_path):
    contenu = TEI_OK.replace(
        "<category xml:id=\"MainZone\"/>",
        "<category xml:id=\"MainZone\"/>"
        "<langUsage><language ident=\"fra\">French</language></langUsage>",
    )
    errors, warnings = validate(_ecrire(tmp_path, contenu))
    assert errors == [] and warnings == []


# =============================================================================
# Validation contre le schema du projet (schema/alto2tei.odd)
# =============================================================================

SVRL_NS = "http://purl.oclc.org/dsdl/svrl"


def _rapport_svrl(corps):
    return (f'<svrl:schematron-output xmlns:svrl="{SVRL_NS}">'
            f"{corps}</svrl:schematron-output>")


def test_un_rapport_svrl_sans_echec_ne_donne_aucune_erreur():
    from scripts.validate_tei import erreurs_svrl
    assert erreurs_svrl(_rapport_svrl(
        '<svrl:fired-rule context="tei:zone"/>')) == []


def test_un_echec_svrl_devient_une_erreur_situee():
    """Schematron distingue l'assertion non tenue du rapport declenche ;
    les deux sont des violations pour nous, et le message doit dire ou."""
    from scripts.validate_tei import erreurs_svrl
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
    from scripts.validate_tei import erreurs_svrl
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
    from scripts.validate_tei import erreurs_svrl
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
    from scripts.validate_tei import erreurs_svrl
    corps = ('<svrl:report location="/a"/>'
             '<svrl:failed-assert location="/b" role="nonfatal">'
             "<svrl:text>usage of deprecated attribute</svrl:text></svrl:failed-assert>")
    ((_, role),) = erreurs_svrl(_rapport_svrl(corps))
    assert role == "nonfatal"


def test_odd_sans_schema_compile_le_dit_au_lieu_de_planter(tmp_path, monkeypatch):
    """--odd sur un depot ou build_odd.py n'a jamais tourne doit nommer la
    commande a lancer, pas echouer sur un fichier introuvable."""
    import scripts.validate_tei as vt
    monkeypatch.setattr(vt, "ODD_RNG", tmp_path / "absent.rng")
    with pytest.raises(SystemExit) as leve:
        vt.main(["--odd", _ecrire(tmp_path, TEI_OK)])
    assert "build_odd.py" in str(leve.value)


def test_schematron_du_projet_signale_une_feuille_absente(tmp_path, monkeypatch):
    import scripts.validate_tei as vt
    pytest.importorskip("saxonche")
    monkeypatch.setattr(vt, "ODD_SVRL", tmp_path / "absent.xsl")
    with pytest.raises(SystemExit) as leve:
        vt.schematron_du_projet()
    assert "build_odd.py" in str(leve.value)


def test_odd_valide_une_sortie_du_pipeline(tmp_path, capsys):
    """Le chemin nominal de --odd, de bout en bout : le golden, sa date de
    generation remise, doit passer RelaxNG et Schematron."""
    pytest.importorskip("saxonche")
    import scripts.validate_tei as vt
    if not vt.ODD_RNG.exists():
        pytest.skip("schema/alto2tei.rng absent — lancer scripts/build_odd.py")
    golden = (RACINE / "tests" / "fixtures" / "golden" / "LIV9001_court.tei.xml")
    doc = tmp_path / "doc.tei.xml"
    doc.write_text(golden.read_text(encoding="utf-8").replace(
        'when="DATE-GENERATION"', 'when="2026-09-03"'), encoding="utf-8")
    with pytest.raises(SystemExit) as leve:
        vt.main(["--odd", str(doc)])
    assert leve.value.code == 0, capsys.readouterr().out
