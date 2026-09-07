"""What a produced TEI file is checked against, beyond its schemas.

The known failure modes of this pipeline, in one pass over the tree.
Five invariants that used to live here as well — prose inside
`<langUsage>`, an undivided IIIF idno, a residual ¬ inside a `<reg>`, a
GraphicZone with no xml:id, a template ORCID — are stated once, in
`schema/teille-douce.odd`, and applied by the Schematron. Saying them
again in Python kept two versions that drifted on severity and on scope.
"""

import re

from lxml import etree

SVRL = "http://purl.oclc.org/dsdl/svrl"
XML_ID = "{http://www.w3.org/XML/1998/namespace}id"
NCNAME = re.compile(r"^[A-Za-z_][A-Za-z0-9._\-]*$")
POINTS_PAIR = re.compile(r"^-?\d+,-?\d+$")

# collect_ids=False: libxml2 otherwise rejects xml:id values that are not
# NCNames at *parse time* (XMLSyntaxError), before the tree can be
# inspected at all. Some old outputs carry xml:id="ark:/12148/…" (colons
# and slashes), which is exactly the failure this has to report as an
# ordinary ERROR line rather than crash on. huge_tree=True matches the
# pipeline's own parser (utils/xml.py) for large documents.
PARSER = etree.XMLParser(collect_ids=False, huge_tree=True)


def svrl_violations(report):
    """The violations of an SVRL report, as one-line messages.

    Schematron distinguishes the unmet assertion (`failed-assert`) from
    the triggered report (`successful-report`); the ODD uses both,
    according to whether it is clearer to assert what must be or to flag
    what must not. For an output check both are violations.

    Returns (message, role) pairs: the TEI marks three of its own rules
    `role="nonfatal"`, and treating those as fatal would fail a document
    on a warning of its own making.
    """
    if isinstance(report, str):
        report = etree.fromstring(report.encode())
    violations = []
    wanted = {f"{{{SVRL}}}failed-assert", f"{{{SVRL}}}successful-report"}
    # One walk, in document order: sweeping all the assertions and then
    # all the reports put the second kind behind the first, out of reach
    # of the display cap. On an annotated document the sixty-three
    # inventory failures were enough to make the four rules written as
    # <sch:report> invisible.
    for element in report.iter():
        if not isinstance(element.tag, str) or element.tag not in wanted:
            continue
        text = " ".join("".join(element.itertext()).split())
        where = element.get("location", "")
        message = f"{text} [{where}]" if where else text
        violations.append((message, element.get("role") or "fatal"))
    return violations


def _local(element):
    return etree.QName(element).localname if isinstance(element.tag, str) else ""


def validate(path, relaxng=None, schematron=None, odd_rng=None):
    """Check one file. Returns (errors, warnings), both lists of strings.

    Nothing is truncated here. The caps used to be applied at collection
    — twenty RelaxNG errors, twenty Schematron violations — so a file
    with more of them was quietly said to have exactly twenty, and no
    flag could lift it. Truncation is a display decision, and it is made
    where the display is.
    """
    errors, warnings = [], []
    tree = etree.parse(path, parser=PARSER)
    root = tree.getroot()

    # Full schema validation, if a tei_all.rng was given.
    if relaxng is not None and not relaxng.validate(tree):
        errors.extend(f"RelaxNG L{failure.line}: {failure.message}"
                      for failure in relaxng.error_log)

    # The project content model.
    if odd_rng is not None and not odd_rng.validate(tree):
        errors.extend(f"teille-douce.rng L{failure.line}: {failure.message}"
                      for failure in odd_rng.error_log)

    # The ODD's constraints, if the SVRL sheet was given.
    if schematron is not None:
        for message, role in svrl_violations(
                schematron.transform_to_string(source_file=path)):
            if role == "nonfatal":
                warnings.append(f"Schematron: {message}")
            else:
                errors.append(f"Schematron: {message}")

    # Graphic zones and figures, gathered in the general pass below
    # rather than in walks of their own: this already visits the whole
    # tree, sourceDoc included.
    graphic_sources = {}   # the zone's xml:id -> its IIIF crop (or None)
    figures = []           # (what @corresp points at, has a <graphic url>)

    for element in root.iter():
        if not isinstance(element.tag, str):
            continue
        tag = _local(element)
        identifier = element.get(XML_ID)
        if tag == "zone" and element.get("type") == "GraphicZone":
            if identifier:
                graphic_sources[identifier] = element.get("source")
        elif tag == "figure":
            figures.append((
                (element.get("corresp") or "").lstrip("#"),
                any(_local(child) == "graphic" and child.get("url")
                    for child in element),
            ))
        if identifier and not NCNAME.match(identifier):
            errors.append(f"xml:id is not an NCName: {identifier!r} on <{tag}>")
        points = element.get("points")
        if points:
            pairs = points.split()
            malformed = [pair for pair in pairs if not POINTS_PAIR.match(pair)]
            if malformed:
                errors.append(f"malformed points on <{tag}>: {malformed[:2]}")
            elif len(pairs) > 2 and all(p.split(",")[0] == "0" for p in pairs):
                errors.append(f"suspicious points (every x is 0) on "
                              f"<{tag} xml:id={identifier}>")
        source = element.get("source")
        if source and source.startswith("http") and "_reconciled" in source:
            errors.append(f"IIIF URL built on the file name: {source[:70]}")

    # Every graphic zone of the sourceDoc has to come out as a <figure>:
    # without this an illustration becomes invisible in the body with
    # nothing to say so.
    figured = {target for target, _ in figures}
    unfigured = set(graphic_sources) - figured
    if unfigured:
        errors.append(
            f"{len(unfigured)} GraphicZone with no <figure> in the text "
            f"(e.g. {sorted(unfigured)[:2]})")
    # The image is missing only where the zone had a IIIF crop to carry
    # over: with no IIIF mapping the sourceDoc has no @source, and a
    # <figure> anchored by @corresp alone is then the normal output.
    for target, has_image in figures:
        if not has_image and graphic_sources.get(target):
            errors.append(f"<figure corresp=#{target}> with no <graphic url>")

    tei_id = root.get(XML_ID) or ""
    if tei_id.startswith("ark_"):
        errors.append(f"root xml:id with a hard-coded ark prefix: {tei_id}")

    # Duplicate xml:id: invalid, and fatal for every link resolution.
    # (collect_ids=False on the parser, so this is ours to check.)
    seen, duplicates = set(), []
    for element in root.iter():
        identifier = element.get(XML_ID)
        if identifier is None:
            continue
        if identifier in seen:
            duplicates.append(identifier)
        seen.add(identifier)
    if duplicates:
        errors.append(f"{len(duplicates)} duplicate xml:id "
                      f"(e.g. {duplicates[:3]})")

    # @ref and @target in the body must resolve to an xml:id of the file.
    identifiers = {element.get(XML_ID) for element in root.iter()
                   if element.get(XML_ID)}
    body = (root.find(".//{*}text") if root.find(".//{*}text") is not None
            else root.find(".//text"))
    dangling_ref = dangling_target = 0
    if body is not None:
        for element in body.iter():
            for reference in (element.get("ref") or "").split():
                if reference.startswith("#") and reference[1:] not in identifiers:
                    dangling_ref += 1
            for target in (element.get("target") or "").split():
                if target.startswith("#") and target[1:] not in identifiers:
                    dangling_target += 1
    if dangling_ref:
        warnings.append(f"{dangling_ref} @ref in the body with no target "
                        f"in the document")
    if dangling_target:
        errors.append(f"{dangling_target} @target in the body with no target "
                      f"in the document")

    # @corresp, @facs and @resp — the central body->sourceDoc mechanism,
    # zones->taxonomy, and the attribution of automatic annotations: every
    # #ref must resolve within the document. This is the check that was
    # missing when 218 dangling @corresp (#MarginTextZone, #GraphicZone,
    # undeclared) went unnoticed.
    dangling_links, examples = 0, []
    for element in root.iter():
        for attribute in ("corresp", "facs", "resp"):
            for reference in (element.get(attribute) or "").split():
                if reference.startswith("#") and reference[1:] not in identifiers:
                    dangling_links += 1
                    if len(examples) < 3:
                        examples.append(f"{attribute}={reference}")
    if dangling_links:
        errors.append(f"{dangling_links} @corresp/@facs/@resp with no target "
                      f"in the document (e.g. {examples})")

    return errors, warnings
