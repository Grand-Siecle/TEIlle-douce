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

from ..constants import NS_XML, UUID_NAMESPACE, XML_ID
from .segmenter import Sentence
from .aligner import AlignedToken

# xml:lang attribute key
XML_LANG = f"{{{NS_XML}}}lang"

# Punctuation join rules
JOIN_LEFT = {".", ",", ";", ":", "!", "?", ")", "]", "»"}
JOIN_RIGHT = {"(", "[", "«"}


def rebuild_container(container, sentences, spans, primary_lang=None):
    """
    Rebuild a container element with tokenized content.

    Replaces all children of the container with <s> elements containing
    <w>, <pc>, <lb/> and — for foreign-language runs — <foreign> wrappers.

    Args:
        container: The lxml Element to rebuild (ab, note, fw).
        sentences: List of Sentence objects for this container.
        spans: List of TextSpan objects from extraction.
        primary_lang: TEI ident of the container's primary language.
            Tokens whose ``origin_lang`` differs are wrapped in
            ``<foreign xml:lang="…">`` inside their sentence.
    """
    if primary_lang is None:
        primary_lang = container.get(XML_LANG) or ""

    # Clear everything (including any <foreign> still sitting around
    # from the language-detection phase — they are recreated inline
    # below from token.origin_lang).
    attribs = dict(container.attrib)
    container.text = None
    for child in list(container):
        container.remove(child)
    for k, v in attribs.items():
        container.set(k, v)

    inserted_lbs = set()
    prev_line_index = None

    for sent in sentences:
        s_elem = etree.SubElement(container, "s")
        s_elem.set(XML_ID, sent.xml_id)
        if sent.next_id:
            s_elem.set("next", sent.next_id)
        if sent.prev_id:
            s_elem.set("prev", sent.prev_id)

        # Open-wrapper state: a <hi> wrapper corresponds to an
        # <hi rend="…"> group, a <foreign> wrapper corresponds to a
        # run of tokens with origin_lang ≠ primary_lang. <hi> is the
        # outer wrapper (typographic) and <foreign> sits inside it.
        current_hi = None
        current_hi_elem = None
        current_foreign = None
        current_foreign_lang = None

        for at in sent.tokens:
            # <hi> layer
            if at.hi_element is not None:
                hi_rend = at.spans[0].hi_rend if at.spans else None
                if current_hi_elem is not at.hi_element:
                    current_hi = etree.SubElement(s_elem, "hi")
                    if hi_rend:
                        current_hi.set("rend", hi_rend)
                    current_hi_elem = at.hi_element
                    current_foreign = None
                    current_foreign_lang = None
                hi_target = current_hi
            else:
                current_hi = None
                current_hi_elem = None
                hi_target = s_elem

            # <foreign> layer (inside <hi> if present)
            tok_lang = at.token.origin_lang or ""
            if tok_lang and tok_lang != primary_lang:
                if current_foreign_lang != tok_lang or current_foreign is None or current_foreign.getparent() is not hi_target:
                    current_foreign = etree.SubElement(hi_target, "foreign")
                    current_foreign.set(XML_LANG, tok_lang)
                    current_foreign_lang = tok_lang
                target = current_foreign
            else:
                current_foreign = None
                current_foreign_lang = None
                target = hi_target

            # Insert <lb/> at line boundaries (first span of this token).
            if at.spans:
                first_span = at.spans[0]
                if first_span.lb_element is not None and first_span.line_index != prev_line_index:
                    lb_id = id(first_span.lb_element)
                    if lb_id not in inserted_lbs:
                        _insert_lb(target, first_span, at, inserted_lbs)
                        prev_line_index = first_span.line_index

            # Emit the token element
            if at.token.is_punctuation:
                _create_pc(target, at)
            elif at.is_cross_line:
                _create_cross_line_w(target, at, inserted_lbs)
                if at.spans:
                    prev_line_index = at.spans[-1].line_index
            else:
                _create_w(target, at)


def _insert_lb(parent, span, aligned_token, inserted_lbs):
    """Insert an <lb/> element as a child of parent."""
    lb = etree.SubElement(parent, "lb")
    if span.lb_corresp:
        lb.set("corresp", span.lb_corresp)
    if span.lb_element is not None:
        facs = span.lb_element.get("facs")
        if facs:
            lb.set("facs", facs)
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

    # Deterministic linking ids (audit 2.8): anchored on the enclosing
    # sentence id, the insertion position and the token form.
    anchor = parent.get(XML_ID) or ""
    base_id = uuid.uuid5(
        UUID_NAMESPACE, f"{anchor}\x1f{len(parent)}\x1f{at.token.form}"
    ).hex[:12]
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
                facs = lb_orig.get("facs")
                if facs:
                    lb.set("facs", facs)
                inserted_lbs.add(id(lb_orig))
