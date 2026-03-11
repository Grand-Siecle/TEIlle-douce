# -----------------------------------------------------------
# Code by: Kelly Christensen
# Builds the <body> element with text from MainZone regions.
# -----------------------------------------------------------
"""
TEI Body builder module.

This module constructs the <body> element of a TEI document by assembling
text lines extracted from the sourceDoc. It handles different zone types
(MainZone, NumberingZone, MarginTextZone, etc.) and wraps them appropriately.

Enhanced with FastText language detection at the paragraph/container level
with support for mixed-language detection using <foreign> tags.
"""

from lxml import etree

from ..constants import NS_XML
from ..lang import get_detector

# xml:lang attribute key with namespace
XML_LANG = f"{{{NS_XML}}}lang"


def _append_choice(parent, original, modernized):
    """
    Append a <choice><orig>…</orig><reg type="modernized">…</reg></choice>
    element as a child of *parent*.

    Args:
        parent: The container element (ab, note, fw, hi).
        original: Original historical text.
        modernized: Modernized text.

    Returns:
        etree.Element: The created <choice> element.
    """
    choice = etree.SubElement(parent, "choice")
    orig = etree.SubElement(choice, "orig")
    orig.text = original
    reg = etree.SubElement(choice, "reg", type="modernized")
    reg.text = modernized
    return choice


def build_body(root, data, detect_lang=True):
    """
    Build the TEI <body> element from extracted line data.

    Creates the text structure with appropriate TEI elements:
    - <pb/> for page breaks
    - <fw> for running titles, page numbers, quire marks
    - <note> for margin text
    - <ab> for main text blocks
    - <hi> for emphasized lines (drop capitals, headings)
    - <lb/> for line breaks

    When detect_lang=True, detects language at the container level
    and adds xml:lang attributes. For mixed-language containers,
    uses <foreign> tags for non-primary language segments.

    Args:
        root (etree.Element): TEI root element to append body to.
        data (list): List of Line namedtuples from Text class.
        detect_lang (bool): Whether to detect and add xml:lang attributes.

    Returns:
        dict or None: Language statistics if detect_lang=True, else None.
    """
    # Initialize language detector if needed
    detector = None
    if detect_lang:
        detector = get_detector()
        detector.reset_stats()

    text_el = etree.SubElement(root, "text")
    body = etree.SubElement(text_el, "body")
    div = etree.SubElement(body, "div")

    # Track containers for language detection
    containers = []  # list of (element, list of line texts)

    for line in data:
        # Prepare zone attributes (without language for now)
        zone_atts = {"corresp": f"#{line.zone_id}", "type": line.zone_type}

        # Create <lb/> with reference to line's xml:id
        lb = etree.Element("lb", corresp=f"#{line.id}")
        lb.tail = f"{line.text}"

        # Add page break at first line of each page
        if int(line.n) == 1:
            pb = etree.Element("pb", corresp=f"#{line.page_id}")
            div.append(pb)

        # Ensure div has at least one element
        if len(div) == 0:
            pb = etree.Element("pb", corresp=f"#{line.page_id}")
            div.append(pb)

        last_element = div[-1]

        # Handle different zone types
        if line.zone_type in ("NumberingZone", "QuireMarksZone", "RunningTitleZone"):
            # Page numbers, quire marks, running titles -> <fw>
            fw = etree.Element("fw", zone_atts)
            last_element.addnext(fw)
            fw.append(lb)
            # Track for language detection
            if detector:
                containers.append((fw, [line.text]))

        elif line.zone_type == "MarginTextZone":
            # Margin text -> <note>
            if last_element.tag != "note":
                note = etree.Element("note", zone_atts)
                last_element.addnext(note)
                note.append(lb)
                if detector:
                    containers.append((note, [line.text]))
            else:
                last_element.append(lb)
                # Add text to existing container
                if detector and containers and containers[-1][0] == last_element:
                    containers[-1][1].append(line.text)

        elif line.zone_type and line.zone_type.startswith("Main"):
            # Main text -> <ab>
            if last_element.tag != "ab":
                ab = etree.Element("ab", zone_atts)
                last_element.addnext(ab)
                last_element = div[-1]
                if detector:
                    containers.append((ab, [line.text]))
            else:
                # Add text to existing container
                if detector and containers and containers[-1][0] == last_element:
                    containers[-1][1].append(line.text)

            # Handle emphasized lines (drop capitals, headings)
            if line.line_type in ("DropCapitalLine", "HeadingLine"):
                ab_children = last_element.getchildren()
                # Check if we need a new <hi> or can reuse existing
                if (
                    len(ab_children) == 0
                    or ab_children[-1].tag != "hi"
                    or ab_children[-1].get("rend") != line.line_type
                ):
                    hi = etree.Element("hi", rend=line.line_type)
                    last_element.append(hi)
                    hi.append(lb)
                elif ab_children[-1].tag == "hi":
                    ab_children[-1].append(lb)

            # Regular lines
            elif line.line_type and line.line_type.startswith("Default"):
                last_element.append(lb)

    # Now apply language detection to containers
    if detector:
        _apply_language_detection(containers, detector)
        return detector.get_stats()

    return None


def _apply_language_detection(containers, detector):
    """
    Apply language detection to container elements.

    Detects the primary language of each container and adds xml:lang.
    For mixed-language containers, wraps foreign segments in <foreign> tags.

    Args:
        containers: List of (element, [line_texts]) tuples.
        detector: LanguageDetector instance.
    """
    for element, line_texts in containers:
        # Join all line texts for this container
        full_text = " ".join(t for t in line_texts if t)

        if not full_text.strip():
            continue

        # Detect language with segments
        primary_lang, foreign_segments = detector.detect_with_segments(full_text)

        # Set primary language on container
        if primary_lang and primary_lang != detector.default_lang:
            element.attrib[XML_LANG] = primary_lang

        # If there are foreign segments, we need to wrap them
        # This is complex because we need to insert <foreign> tags
        # into the existing structure without breaking the <lb/> references
        if foreign_segments:
            _insert_foreign_tags(element, full_text, foreign_segments, detector)


def apply_modernization(root, modernized_texts):
    """
    Post-process the body to insert <choice><orig>/<reg> for modernized lines.

    Finds all <lb> elements in the body, matches them 1:1 with
    *modernized_texts*, and wraps lines that differ in <choice>.

    Args:
        root: TEI root element (body must already be built).
        modernized_texts: List of modernized strings aligned with <lb> elements.

    Returns:
        int: Number of lines that were modernized.
    """
    body = root.find(".//body")
    if body is None:
        return 0

    lbs = list(body.iter("lb"))
    count = 0

    for i, lb in enumerate(lbs):
        if i >= len(modernized_texts):
            break
        original = lb.tail or ""
        mod = modernized_texts[i]
        if not mod or mod == original:
            continue
        # Clear the tail text from the lb, insert <choice> after it
        lb.tail = None
        choice = _append_choice(lb.getparent(), original, mod)
        # Move <choice> right after <lb/> (append puts it at the end)
        lb.addnext(choice)
        count += 1

    return count


def apply_modernization_enriched(root, corresp_to_mod):
    """
    Post-process an enriched body to insert <choice><orig>/<reg> per line.

    After enrichment, the body has <s>/<w>/<pc>/<lb> structure.
    For each <lb> whose @corresp is in *corresp_to_mod*, this wraps the
    following <w>/<pc> siblings in <choice><orig>…</orig><reg>…</reg></choice>.

    Args:
        root: TEI root element (body must already be enriched).
        corresp_to_mod: Dict mapping corresp values to modernized text strings.

    Returns:
        int: Number of lines wrapped in <choice>.
    """
    body = root.find(".//body")
    if body is None:
        return 0

    count = 0
    for container in body.iter("ab", "note", "fw"):
        has_sentences = any(c.tag == "s" for c in container)
        if has_sentences:
            # Enriched container: process <s> children and <hi> within them
            for s_elem in list(container):
                if s_elem.tag == "s":
                    count += _wrap_line_groups(s_elem, corresp_to_mod)
                    # Also recurse into <hi> inside <s>
                    for hi in list(s_elem):
                        if hi.tag == "hi":
                            count += _wrap_line_groups(hi, corresp_to_mod)
        else:
            # Non-enriched container: wrap <lb> tails directly
            count += _wrap_plain_lines(container, corresp_to_mod)

    return count


def _wrap_line_groups(parent, corresp_to_mod):
    """
    Wrap <w>/<pc> groups after each <lb> in <choice><orig>/<reg>.

    Iterates direct children of *parent*. Groups elements between
    consecutive <lb/> elements. For each group whose <lb> @corresp
    is in corresp_to_mod, wraps the group in <choice>.

    Args:
        parent: An <s> or <hi> element.
        corresp_to_mod: Dict mapping corresp values to modernized text.

    Returns:
        int: Number of groups wrapped.
    """
    # Collect line groups: (lb_element, [following sibling elements])
    groups = []
    current_lb = None
    current_elements = []

    for child in list(parent):
        tag = child.tag if isinstance(child.tag, str) else ""
        if tag == "lb":
            if current_lb is not None:
                groups.append((current_lb, current_elements))
            current_lb = child
            current_elements = []
        elif tag not in ("hi", "s", "choice"):
            # Collect <w>, <pc>, <foreign>, etc.
            current_elements.append(child)

    # Don't forget the last group
    if current_lb is not None:
        groups.append((current_lb, current_elements))

    # Wrap groups in reverse order to preserve tree indices
    count = 0
    for lb, elements in reversed(groups):
        corresp = lb.get("corresp")
        if not corresp or corresp not in corresp_to_mod or not elements:
            continue

        mod_text = corresp_to_mod[corresp]

        choice = etree.Element("choice")
        orig = etree.SubElement(choice, "orig")
        reg = etree.SubElement(choice, "reg", type="modernized")
        reg.text = mod_text

        # Move elements into <orig>
        for elem in elements:
            parent.remove(elem)
            orig.append(elem)

        # Insert <choice> right after <lb/>
        lb.addnext(choice)
        count += 1

    return count


def _wrap_plain_lines(container, corresp_to_mod):
    """
    Wrap <lb> tail text in <choice> for non-enriched containers.

    Fallback for containers that enrichment skipped (too short, unsupported
    language, etc.).

    Args:
        container: A container element (ab, note, fw) with <lb/> children.
        corresp_to_mod: Dict mapping corresp values to modernized text.

    Returns:
        int: Number of lines wrapped.
    """
    count = 0
    for lb in list(container.iter("lb")):
        corresp = lb.get("corresp")
        original = lb.tail or ""
        if not corresp or corresp not in corresp_to_mod:
            continue
        mod_text = corresp_to_mod[corresp]
        if mod_text == original:
            continue
        lb.tail = None
        choice = _append_choice(lb.getparent(), original, mod_text)
        lb.addnext(choice)
        count += 1
    return count


def _insert_foreign_tags(element, full_text, foreign_segments, detector):
    """
    Insert <foreign> tags for detected foreign language segments.

    This is a simplified approach that adds <foreign> elements
    at the end of the container with the detected foreign text.
    A more sophisticated approach would splice them inline.

    Args:
        element: The container element (ab, note, fw).
        full_text: The full text content of the container.
        foreign_segments: List of LangSegment objects.
        detector: LanguageDetector instance.
    """
    # For each foreign segment, create a <foreign> element
    # We add them as siblings after the container, or as notes
    # This is a pragmatic approach since modifying inline is complex

    for seg in foreign_segments:
        if seg.lang and seg.lang != detector.default_lang:
            # Create a comment noting the foreign passage
            # A full implementation would insert inline, but that requires
            # complex text node manipulation
            foreign = etree.Element("foreign")
            foreign.attrib[XML_LANG] = seg.lang
            # We could add the text, but it's already in the container
            # Just mark that foreign text was detected
            foreign.attrib["corresp"] = element.get("corresp", "")
            foreign.text = f"[{seg.text[:50]}...]" if len(seg.text) > 50 else seg.text

            # Add as child at the end
            element.append(foreign)
