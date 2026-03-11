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

import logging
from dataclasses import dataclass, field

from lxml import etree

from ..constants import NS_XML
from ..lang import get_detector

logger = logging.getLogger(__name__)

# xml:lang attribute key with namespace
XML_LANG = f"{{{NS_XML}}}lang"


@dataclass
class _SentenceSegment:
    """A portion of a sentence that falls on one line."""
    s_elem: object          # original <s> element
    s_xml_id: str           # original xml:id
    tokens: list = field(default_factory=list)  # <w>, <pc>, <hi> elements
    is_full: bool = True    # True if entire <s> is on this line


@dataclass
class _LineGroup:
    """All content belonging to one line (delimited by <lb/>)."""
    lb_corresp: str | None
    lb_element: object      # original <lb/> element
    segments: list = field(default_factory=list)  # list of _SentenceSegment


def _parse_line_groups(container):
    """
    Parse an enriched container into line groups.

    Walks <s> children of the container. Inside each <s>, groups
    tokens by <lb/> boundaries into LineGroup objects.

    Args:
        container: An enriched lxml Element (<ab>, <note>, <fw>)
                   containing <s>/<w>/<pc>/<lb> structure.

    Returns:
        list[_LineGroup]: Line groups in document order.
    """
    groups = []
    current_group = None

    for s_elem in container:
        if not isinstance(s_elem.tag, str):
            continue

        tag = s_elem.tag
        if tag == "s":
            s_id = s_elem.get(f"{{{NS_XML}}}id", "")
            # Track whether this <s> has been split across lines
            segment_for_this_s = None
            s_has_multiple_lines = False

            for child in s_elem:
                if not isinstance(child.tag, str):
                    continue
                ctag = child.tag

                if ctag == "lb":
                    # Start a new line group
                    if segment_for_this_s is not None:
                        s_has_multiple_lines = True
                        segment_for_this_s.is_full = False
                    current_group = _LineGroup(
                        lb_corresp=child.get("corresp"),
                        lb_element=child,
                    )
                    groups.append(current_group)
                    segment_for_this_s = _SentenceSegment(
                        s_elem=s_elem, s_xml_id=s_id
                    )
                    current_group.segments.append(segment_for_this_s)

                elif ctag in ("w", "pc"):
                    if current_group is None:
                        # Edge case: tokens before any <lb/>
                        current_group = _LineGroup(lb_corresp=None, lb_element=None)
                        groups.append(current_group)
                        segment_for_this_s = _SentenceSegment(
                            s_elem=s_elem, s_xml_id=s_id
                        )
                        current_group.segments.append(segment_for_this_s)
                    elif segment_for_this_s is None:
                        segment_for_this_s = _SentenceSegment(
                            s_elem=s_elem, s_xml_id=s_id
                        )
                        current_group.segments.append(segment_for_this_s)
                    segment_for_this_s.tokens.append(child)

                elif ctag == "hi":
                    # <hi> contains <w>/<pc>/<lb> — recurse
                    for hi_child in child:
                        if not isinstance(hi_child.tag, str):
                            continue
                        hctag = hi_child.tag
                        if hctag == "lb":
                            if segment_for_this_s is not None:
                                s_has_multiple_lines = True
                                segment_for_this_s.is_full = False
                            current_group = _LineGroup(
                                lb_corresp=hi_child.get("corresp"),
                                lb_element=hi_child,
                            )
                            groups.append(current_group)
                            segment_for_this_s = _SentenceSegment(
                                s_elem=s_elem, s_xml_id=s_id
                            )
                            current_group.segments.append(segment_for_this_s)
                        elif hctag in ("w", "pc"):
                            if segment_for_this_s is None:
                                segment_for_this_s = _SentenceSegment(
                                    s_elem=s_elem, s_xml_id=s_id
                                )
                                if current_group:
                                    current_group.segments.append(segment_for_this_s)
                            segment_for_this_s.tokens.append(hi_child)

            # If this <s> was split, mark all its segments as not full
            if s_has_multiple_lines:
                for g in groups:
                    for seg in g.segments:
                        if seg.s_elem is s_elem:
                            seg.is_full = False

        elif tag == "lb":
            # Bare <lb> at container level
            current_group = _LineGroup(
                lb_corresp=s_elem.get("corresp"),
                lb_element=s_elem,
            )
            groups.append(current_group)

    return groups


def _rebuild_with_modernization(container, groups, corresp_to_mod):
    """
    Rebuild an enriched container with <choice> wrapping for modernized lines.

    Clears the container and rebuilds it with:
    - <lb/> as direct children
    - <choice><orig><s>...</s></orig><reg>text</reg></choice> for modernized lines
    - <s> fragments directly for non-modernized lines

    Sentences spanning line boundaries are fragmented with @part/@next/@prev.

    Args:
        container: The lxml Element to rebuild.
        groups: list[_LineGroup] from _parse_line_groups().
        corresp_to_mod: Dict mapping corresp values to modernized text.

    Returns:
        int: Number of lines wrapped in <choice>.
    """
    # Phase 1: Build fragment mapping for sentences split across lines
    s_occurrences = {}
    for i, group in enumerate(groups):
        for seg in group.segments:
            s_occurrences.setdefault(seg.s_xml_id, []).append((i, seg))

    # Phase 2: Assign fragment IDs
    fragment_ids = {}  # (s_xml_id, line_index) -> new_id
    for s_id, occurrences in s_occurrences.items():
        if len(occurrences) > 1:
            for j, (line_idx, seg) in enumerate(occurrences):
                frag_id = f"{s_id}_{j}"
                fragment_ids[(s_id, line_idx)] = frag_id
        else:
            line_idx = occurrences[0][0]
            fragment_ids[(s_id, line_idx)] = s_id

    # Phase 3: Clear container children
    attribs = dict(container.attrib)
    container.text = None
    for child in list(container):
        container.remove(child)
    for k, v in attribs.items():
        container.set(k, v)

    # Phase 4: Rebuild
    XML_ID_KEY = f"{{{NS_XML}}}id"
    count = 0

    for i, group in enumerate(groups):
        # Add <lb/>
        if group.lb_element is not None:
            lb = etree.SubElement(container, "lb")
            if group.lb_corresp:
                lb.set("corresp", group.lb_corresp)

        # Check if this line is modernized
        is_modernized = group.lb_corresp and group.lb_corresp in corresp_to_mod

        if is_modernized:
            choice = etree.SubElement(container, "choice")
            orig = etree.SubElement(choice, "orig")

            for seg in group.segments:
                s_new = etree.SubElement(orig, "s")
                frag_id = fragment_ids.get((seg.s_xml_id, i), seg.s_xml_id)
                s_new.set(XML_ID_KEY, frag_id)

                # Set @part/@next/@prev for fragmented sentences
                occurrences = s_occurrences.get(seg.s_xml_id, [])
                if len(occurrences) > 1:
                    frag_idx = [idx for idx, (li, _) in enumerate(occurrences) if li == i][0]
                    total = len(occurrences)

                    if frag_idx == 0:
                        s_new.set("part", "I")
                    elif frag_idx == total - 1:
                        s_new.set("part", "F")
                    else:
                        s_new.set("part", "M")

                    if frag_idx < total - 1:
                        next_line_idx = occurrences[frag_idx + 1][0]
                        next_id = fragment_ids.get((seg.s_xml_id, next_line_idx))
                        if next_id:
                            s_new.set("next", f"#{next_id}")
                    if frag_idx > 0:
                        prev_line_idx = occurrences[frag_idx - 1][0]
                        prev_id = fragment_ids.get((seg.s_xml_id, prev_line_idx))
                        if prev_id:
                            s_new.set("prev", f"#{prev_id}")

                # Copy tokens into new <s>
                for token in seg.tokens:
                    s_new.append(token)

            reg = etree.SubElement(choice, "reg", type="modernized")
            reg.text = corresp_to_mod[group.lb_corresp]
            count += 1

        else:
            # Non-modernized line: add <s> fragments directly to container
            for seg in group.segments:
                s_new = etree.SubElement(container, "s")
                frag_id = fragment_ids.get((seg.s_xml_id, i), seg.s_xml_id)
                s_new.set(XML_ID_KEY, frag_id)

                occurrences = s_occurrences.get(seg.s_xml_id, [])
                if len(occurrences) > 1:
                    frag_idx = [idx for idx, (li, _) in enumerate(occurrences) if li == i][0]
                    total = len(occurrences)

                    if frag_idx == 0:
                        s_new.set("part", "I")
                    elif frag_idx == total - 1:
                        s_new.set("part", "F")
                    else:
                        s_new.set("part", "M")

                    if frag_idx < total - 1:
                        next_line_idx = occurrences[frag_idx + 1][0]
                        next_id = fragment_ids.get((seg.s_xml_id, next_line_idx))
                        if next_id:
                            s_new.set("next", f"#{next_id}")
                    if frag_idx > 0:
                        prev_line_idx = occurrences[frag_idx - 1][0]
                        prev_id = fragment_ids.get((seg.s_xml_id, prev_line_idx))
                        if prev_id:
                            s_new.set("prev", f"#{prev_id}")

                for token in seg.tokens:
                    s_new.append(token)

    return count


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

    After enrichment, containers have <s>/<w>/<pc>/<lb> structure.
    This function restructures containers so that:
    - <lb/> and <choice> are direct children of the container
    - <s> elements (potentially fragmented) live inside <choice><orig>
    - <reg> contains the modernized line text

    Containers without any modernized lines are left untouched.
    Non-enriched containers (no <s> children) fall back to plain wrapping.

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
            # Enriched container: parse and rebuild
            groups = _parse_line_groups(container)

            # Check if any line in this container needs modernization
            has_mod = any(
                g.lb_corresp and g.lb_corresp in corresp_to_mod
                for g in groups
            )
            if not has_mod:
                continue

            count += _rebuild_with_modernization(container, groups, corresp_to_mod)
        else:
            # Non-enriched container: wrap <lb> tails directly
            count += _wrap_plain_lines(container, corresp_to_mod)


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
