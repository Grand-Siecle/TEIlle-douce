# -----------------------------------------------------------
# Validates TEI outputs against the known failure modes of this
# pipeline. Usage: venv/bin/python scripts/validate_tei.py tei_test/*.xml
# Exit 1 if any ERROR.
# -----------------------------------------------------------
import re
import sys

from lxml import etree

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


def validate(path):
    errors, warnings = [], []
    root = etree.parse(path, parser=PARSER).getroot()

    def local(el):
        return etree.QName(el).localname if isinstance(el.tag, str) else ""

    for el in root.iter():
        if not isinstance(el.tag, str):
            continue
        tag = local(el)
        xid = el.get(XML_ID)
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
        if tag == "idno" and el.get("type") == "iiif" and el.text and "|" in el.text:
            errors.append(f"idno iiif non splitté: {el.text[:60]}")
        if tag == "reg" and el.text and "¬" in el.text:
            warnings.append(f"¬ résiduel dans reg: {el.text[:50]!r}")
        src = el.get("source")
        if src and src.startswith("http") and "_reconciled" in src:
            errors.append(f"URL IIIF construite sur le nom de fichier: {src[:70]}")

    tei_id = root.get(XML_ID) or ""
    if tei_id.startswith("ark_"):
        errors.append(f"xml:id racine avec préfixe ark hardcodé: {tei_id}")

    # ORCID placeholder et ptr vides
    text = etree.tostring(root, encoding="unicode")
    if "0000-0000-0000-0000" in text:
        errors.append("ORCID placeholder 0000-0000-0000-0000 présent")

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

    return errors, warnings


def main():
    total_err = 0
    for path in sys.argv[1:]:
        errors, warnings = validate(path)
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
