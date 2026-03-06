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
    Append a <choice><orig>...</orig><reg>...</reg></choice> to parent.

    Args:
        parent: The container element (fw, note, ab, hi).
        original (str): Original text.
        modernized (str): Modernized text.
    """
    choice = etree.SubElement(parent, "choice")
    orig = etree.SubElement(choice, "orig")
    orig.text = original
    reg = etree.SubElement(choice, "reg", type="modernized")
    reg.text = modernized


def build_body(root, data, detect_lang=True, modernized=None):
    """
    Build the TEI <body> element from extracted line data.

    Creates the text structure with appropriate TEI elements:
    - <pb/> for page breaks
    - <fw> for running titles, page numbers, quire marks
    - <note> for margin text
    - <ab> for main text blocks
    - <hi> for emphasized lines (drop capitals, headings)
    - <lb/> for line breaks

    When modernized texts are provided, each line is wrapped in
    <choice><orig>original</orig><reg type="modernized">modern</reg></choice>
    instead of plain text.

    When detect_lang=True, detects language at the container level
    and adds xml:lang attributes. For mixed-language containers,
    uses <foreign> tags for non-primary language segments.

    Args:
        root (etree.Element): TEI root element to append body to.
        data (list): List of Line namedtuples from Text class.
        detect_lang (bool): Whether to detect and add xml:lang attributes.
        modernized (list or None): List of modernized texts, same length as data.

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

    for line_idx, line in enumerate(data):
        # Prepare zone attributes (without language for now)
        zone_atts = {"corresp": f"#{line.zone_id}", "type": line.zone_type}

        # Create <lb/> with reference to line's xml:id
        lb = etree.Element("lb", corresp=f"#{line.id}")

        # Check if we have a modernized version for this line
        mod_text = modernized[line_idx] if modernized else None
        has_mod = mod_text is not None and mod_text != line.text

        if not has_mod:
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
            if has_mod:
                _append_choice(fw, line.text, mod_text)
            # Track for language detection
            if detector:
                containers.append((fw, [line.text]))

        elif line.zone_type == "MarginTextZone":
            # Margin text -> <note>
            if last_element.tag != "note":
                note = etree.Element("note", zone_atts)
                last_element.addnext(note)
                note.append(lb)
                if has_mod:
                    _append_choice(note, line.text, mod_text)
                if detector:
                    containers.append((note, [line.text]))
            else:
                last_element.append(lb)
                if has_mod:
                    _append_choice(last_element, line.text, mod_text)
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
                    if has_mod:
                        _append_choice(hi, line.text, mod_text)
                elif ab_children[-1].tag == "hi":
                    ab_children[-1].append(lb)
                    if has_mod:
                        _append_choice(ab_children[-1], line.text, mod_text)

            # Regular lines
            elif line.line_type and line.line_type.startswith("Default"):
                last_element.append(lb)
                if has_mod:
                    _append_choice(last_element, line.text, mod_text)

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
