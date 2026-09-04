# Run: venv/bin/python tests/test_document_id.py
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from teille_douce.utils.files import parse_document_id, canonical_document_id
from teille_douce.tei import TEI
from teille_douce.constants import XML_ID


def test_parse():
    assert parse_document_id("LIV0008_reconciled") == ("LIV0008", None)
    assert parse_document_id("LIV0002a_reconciled") == ("LIV0002", "a")
    assert parse_document_id("LIV0031_t2_reconciled") == ("LIV0031", "t2")
    assert parse_document_id("LIV0326_v2_altos_transcribed") == ("LIV0326", "v2")
    assert parse_document_id("ABC999_b_folder") == ("ABC999", "b")


def test_canonical():
    assert canonical_document_id("LIV0008_reconciled") == "LIV0008"
    assert canonical_document_id("LIV0002a_reconciled") == "LIV0002a"
    assert canonical_document_id("LIV0031_t2_reconciled") == "LIV0031_t2"
    # underscore volume markers with a bare letter collapse ("_b" -> "b")
    assert canonical_document_id("ABC999_b_folder") == "ABC999b"


def test_root_xml_id():
    tree = TEI("LIV0002a_reconciled", [])
    tree.build_tree()
    assert tree.root.get(XML_ID) == "LIV0002a", tree.root.get(XML_ID)


if __name__ == "__main__":
    test_parse()
    test_canonical()
    test_root_xml_id()
    print("OK test_document_id")
