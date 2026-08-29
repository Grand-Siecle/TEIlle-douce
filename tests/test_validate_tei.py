# Tests de scripts/validate_tei.py -- le garde-fou de sortie, jusqu'ici
# a 0 % de couverture (audit 2.6).
#
# Run: venv/bin/python -m pytest tests/test_validate_tei.py -q
import pytest

from scripts.validate_tei import main, validate

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
