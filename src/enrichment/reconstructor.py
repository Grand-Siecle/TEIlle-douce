# -----------------------------------------------------------
# XML reconstruction with linguistic annotations.
# -----------------------------------------------------------
"""
XML reconstructor (Phase 6).

Rebuilds TEI container elements with <w>, <pc>, <s>, and <lb/> elements
based on aligned and segmented NLP tokens.
"""

import uuid

from lxml import etree

from ..constants import NS_XML, XML_ID
from .segmenter import Sentence
from .aligner import AlignedToken

# xml:lang attribute key
XML_LANG = f"{{{NS_XML}}}lang"

# Punctuation join rules
JOIN_LEFT = {".", ",", ";", ":", "!", "?", ")", "]", "»"}
JOIN_RIGHT = {"(", "[", "«"}


def rebuild_container(container, sentences, spans):
    """
    Rebuild a container element with tokenized content.

    Replaces all children of the container with <s> elements containing
    <w>, <pc>, and <lb/> elements.

    Args:
        container: The lxml Element to rebuild (ab, note, fw).
        sentences: List of Sentence objects for this container.
        spans: List of TextSpan objects from extraction.
    """
    # Save container attributes and <foreign> children (from lang detection)
    attribs = dict(container.attrib)
    foreign_elements = [child for child in container if child.tag == "foreign"]

    # Clear all children and text
    container.text = None
    for child in list(container):
        container.remove(child)

    # Restore attributes
    for k, v in attribs.items():
        container.set(k, v)

    # Track which lb elements have been inserted
    inserted_lbs = set()

    # Track the last line_index to know when to insert <lb/>
    prev_line_index = None

    for sent in sentences:
        s_elem = etree.SubElement(container, "s")
        s_elem.set(XML_ID, sent.xml_id)

        if sent.next_id:
            s_elem.set("next", sent.next_id)
        if sent.prev_id:
            s_elem.set("prev", sent.prev_id)

        # Current <hi> wrapper (if tokens are inside a <hi>)
        current_hi = None
        current_hi_elem = None

        for at in sent.tokens:
            # Determine the target parent to append to
            # If this token is inside a <hi>, use or create a <hi> wrapper
            target = s_elem

            if at.hi_element is not None:
                hi_rend = at.spans[0].hi_rend if at.spans else None
                if current_hi_elem is not at.hi_element:
                    # New <hi> group
                    current_hi = etree.SubElement(s_elem, "hi")
                    if hi_rend:
                        current_hi.set("rend", hi_rend)
                    current_hi_elem = at.hi_element
                target = current_hi
            else:
                current_hi = None
                current_hi_elem = None

            # Insert <lb/> at line boundaries (only for the first span of this token)
            # For cross-line words, only insert the lb for the FIRST line;
            # subsequent lb's go inside the <w> element
            if at.spans:
                first_span = at.spans[0]
                if first_span.lb_element is not None and first_span.line_index != prev_line_index:
                    lb_id = id(first_span.lb_element)
                    if lb_id not in inserted_lbs:
                        _insert_lb(target, first_span, at, inserted_lbs)
                        prev_line_index = first_span.line_index

            # Create the token element
            if at.token.is_punctuation:
                _create_pc(target, at)
            elif at.is_cross_line:
                _create_cross_line_w(target, at, inserted_lbs)
                if at.spans:
                    prev_line_index = at.spans[-1].line_index
            else:
                _create_w(target, at)

    # Re-append <foreign> elements preserved from language detection
    for fe in foreign_elements:
        container.append(fe)


def _insert_lb(parent, span, aligned_token, inserted_lbs):
    """Insert an <lb/> element as a child of parent."""
    lb = etree.SubElement(parent, "lb")
    if span.lb_corresp:
        lb.set("corresp", span.lb_corresp)
    inserted_lbs.add(id(span.lb_element))


def _create_w(parent, at):
    """Create a <w> element for a regular word."""
    w = etree.SubElement(parent, "w")
    w.text = at.token.form

    if at.token.lemma:
        w.set("lemma", at.token.lemma)
    if at.token.pos:
        w.set("pos", at.token.pos)
    if at.token.morph:
        w.set("msd", at.token.morph)
    if at.token.treated and at.token.treated != at.token.form:
        w.set("norm", at.token.treated)


def _create_pc(parent, at):
    """Create a <pc> element for punctuation."""
    pc = etree.SubElement(parent, "pc")
    pc.text = at.token.form

    if at.token.form in JOIN_LEFT:
        pc.set("join", "left")
    elif at.token.form in JOIN_RIGHT:
        pc.set("join", "right")


def _create_cross_line_w(parent, at, inserted_lbs):
    """
    Create fragmented <w> elements for a word split across lines.

    Instead of a single <w> with embedded <lb/>, produces:
        <w xml:id="w_X1" next="#w_X2" part="I" lemma="..." pos="...">part1</w>
        <lb corresp="#..."/>
        <w xml:id="w_X2" prev="#w_X1" part="F" lemma="..." pos="...">part2</w>
    """

    parts = at.original_parts or [at.token.form]
    lb_elems = at.lb_elements or []

    if len(parts) < 2:
        # Fallback: not actually cross-line
        _create_w(parent, at)
        return

    # Generate unique IDs for linking
    base_id = uuid.uuid4().hex[:12]
    w_ids = [f"w_{base_id}_{i}" for i in range(len(parts))]

    # Shared attributes (applied to all fragments)
    shared_attrs = {}
    if at.token.lemma:
        shared_attrs["lemma"] = at.token.lemma
    if at.token.pos:
        shared_attrs["pos"] = at.token.pos
    if at.token.morph:
        shared_attrs["msd"] = at.token.morph

    for i, part_text in enumerate(parts):
        w = etree.SubElement(parent, "w")
        w.text = part_text
        w.set(XML_ID, w_ids[i])

        # Set part attribute: I (initial), M (medial), F (final)
        if i == 0:
            w.set("part", "I")
        elif i == len(parts) - 1:
            w.set("part", "F")
        else:
            w.set("part", "M")

        # Set next/prev linking
        if i < len(parts) - 1:
            w.set("next", f"#{w_ids[i + 1]}")
        if i > 0:
            w.set("prev", f"#{w_ids[i - 1]}")

        # Copy shared attributes
        for k, v in shared_attrs.items():
            w.set(k, v)

        # @norm only on initial fragment (represents the full word)
        if i == 0 and at.token.treated and at.token.treated != at.token.form:
            w.set("norm", at.token.treated)

        # Insert <lb/> between parts (after each part except the last)
        if i < len(lb_elems):
            lb = etree.SubElement(parent, "lb")
            lb_orig = lb_elems[i]
            if lb_orig is not None:
                corresp = lb_orig.get("corresp")
                if corresp:
                    lb.set("corresp", corresp)
                inserted_lbs.add(id(lb_orig))
