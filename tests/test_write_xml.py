# Tests de src/utils/xml.py::write_xml -- ecriture atomique.
#
# --skip-existing considere l'existence du fichier final comme preuve de
# conversion : un fichier tronque par une interruption serait alors saute
# pour toujours. write_xml doit donc etre atomique (tmp + os.replace).
#
# Run: venv/bin/python -m pytest tests/test_write_xml.py -q
import pytest
from lxml import etree

from src.utils import xml as xml_mod
from src.utils.xml import write_xml


def qlocal(el):
    """Nom local du tag : les deux conventions de namespace coexistent
    dans le meme arbre (audit 4.2)."""
    return etree.QName(el).localname


def test_write_xml_writes_the_file(tmp_path):
    root = etree.Element("TEI")
    etree.SubElement(root, "teiHeader")
    cible = tmp_path / "sortie" / "doc.tei.xml"

    write_xml(root, cible)

    assert cible.exists()
    assert etree.parse(str(cible)).getroot().tag == "TEI"
    # pas de residu temporaire
    assert list(cible.parent.glob("*.part")) == []


def test_write_xml_failure_leaves_no_final_file(tmp_path, monkeypatch):
    """Si la serialisation echoue en cours de route, le chemin final ne doit
    pas exister : un fichier tronque serait pris pour une conversion faite."""
    root = etree.Element("TEI")
    cible = tmp_path / "doc.tei.xml"

    class ArbreExplosif:
        def __init__(self, _root):
            pass

        def write(self, chemin, **kwargs):
            with open(chemin, "wb") as f:
                f.write(b"<TEI>tronque")   # ecrit un debut de fichier puis meurt
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(xml_mod.etree, "ElementTree", ArbreExplosif)

    with pytest.raises(OSError):
        write_xml(root, cible)

    assert not cible.exists(), "un fichier final (tronque) a ete laisse en place"
    assert list(tmp_path.glob("*.part")) == [], "residu temporaire non nettoye"


def test_write_xml_failure_preserves_previous_output(tmp_path, monkeypatch):
    """Un echec de reecriture ne doit pas detruire la version precedente."""
    root = etree.Element("TEI")
    cible = tmp_path / "doc.tei.xml"
    cible.write_text("<TEI>version precedente</TEI>", encoding="utf-8")

    class ArbreExplosif:
        def __init__(self, _root):
            pass

        def write(self, chemin, **kwargs):
            raise OSError(28, "No space left on device")

    monkeypatch.setattr(xml_mod.etree, "ElementTree", ArbreExplosif)

    with pytest.raises(OSError):
        write_xml(root, cible)

    assert cible.read_text(encoding="utf-8") == "<TEI>version precedente</TEI>"


# =============================================================================
# content_root : le point d'entree commun a toutes les phases
# =============================================================================

def test_content_root_returns_text_so_front_matter_is_processed():
    """Chaque phase partait de <body> ; la page de titre, arrivee dans
    <front>, aurait traverse le pipeline sans annotation."""
    root = etree.fromstring(
        b"<TEI><text><front><titlePage/></front><body><div/></body></text></TEI>"
    )
    trouve = xml_mod.content_root(root)
    assert qlocal(trouve) == "text"
    assert [qlocal(el) for el in trouve] == ["front", "body"]


def test_content_root_falls_back_to_body_then_none():
    """Les arbres construits sans <text> (tests unitaires, appels
    externes) doivent continuer de fonctionner."""
    sans_text = etree.fromstring(b"<TEI><body><div/></body></TEI>")
    assert qlocal(xml_mod.content_root(sans_text)) == "body"
    assert xml_mod.content_root(etree.fromstring(b"<TEI/>")) is None


def test_content_root_accepts_the_container_itself():
    """Un appelant qui passe deja le <body> doit le recuperer, pas None."""
    body = etree.fromstring(b"<body><div/></body>")
    assert xml_mod.content_root(body) is body


def test_content_root_handles_namespaced_trees():
    """Les deux conventions de namespace coexistent dans le meme arbre
    (audit 4.2) : le nom nu ne suffit pas."""
    root = etree.fromstring(
        b'<TEI xmlns="http://www.tei-c.org/ns/1.0"><text><body/></text></TEI>'
    )
    assert etree.QName(xml_mod.content_root(root)).localname == "text"
