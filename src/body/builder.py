# -----------------------------------------------------------
# Code by: Kelly Christensen
# Builds the <body> element with text from MainZone regions.
# -----------------------------------------------------------
"""
TEI Body builder module.

This module constructs the <body> element of a TEI document by assembling
text lines extracted from the sourceDoc. It handles different zone types
(MainZone, NumberingZone, MarginTextZone, etc.) and wraps them appropriately.

Enhanced with Lingua language detection at the paragraph/container level
with support for mixed-language detection using <foreign> tags.
"""

import logging
from dataclasses import dataclass, field

from lxml import etree

from ..constants import XML_ID, XML_LANG
from ..lang import get_detector
from ..utils.xml import declare_responsibility

logger = logging.getLogger(__name__)



@dataclass
class _SentenceSegment:
    """A portion of a sentence that falls on one line."""
    s_elem: object          # original <s> element
    s_xml_id: str           # original xml:id
    tokens: list = field(default_factory=list)  # <w>/<pc> elements in order
    token_langs: list = field(default_factory=list)  # parallel: <foreign> lang or None
    is_full: bool = True    # True if entire <s> is on this line


@dataclass
class _LineGroup:
    """All content belonging to one line (delimited by <lb/>)."""
    lb_corresp: str | None
    lb_element: object      # original <lb/> element
    segments: list = field(default_factory=list)  # list of _SentenceSegment


def _walk_sentence_children(parent, foreign_lang=None):
    """
    Yield (event, element, foreign_lang) in document order.

    Recurses into <hi> and <foreign> wrappers so callers see a flat
    sequence of ``"lb"`` / ``"token"`` events while still knowing the
    nearest enclosing <foreign> (for ``foreign_lang``). The <hi>
    wrapper is not tracked here — it is lost during modernization
    rebuild as in the pre-existing behaviour.

    Events:
        ("lb", <lb>, foreign_lang_or_None)
        ("token", <w|pc>, foreign_lang_or_None)
    """
    for child in parent:
        if not isinstance(child.tag, str):
            continue
        ctag = child.tag
        if ctag == "lb":
            yield ("lb", child, foreign_lang)
        elif ctag in ("w", "pc"):
            yield ("token", child, foreign_lang)
        elif ctag == "hi":
            yield from _walk_sentence_children(child, foreign_lang)
        elif ctag == "foreign":
            lang = child.get(XML_LANG) or foreign_lang
            yield from _walk_sentence_children(child, lang)


def _parse_line_groups(container):
    """
    Parse an enriched container into line groups.

    Walks <s> children of the container. Inside each <s>, groups
    tokens by <lb/> boundaries into LineGroup objects. <foreign>
    wrappers inside <s> contribute their ``xml:lang`` to each
    contained token so the modernization rebuild can re-emit them.

    Args:
        container: An enriched lxml Element (<ab>, <note>, <fw>)
                   containing <s>/<w>/<pc>/<lb>/<foreign> structure.

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
            s_id = s_elem.get(XML_ID, "")
            segment_for_this_s = None
            s_has_multiple_lines = False

            for event, elem, fgn in _walk_sentence_children(s_elem):
                if event == "lb":
                    if segment_for_this_s is not None:
                        s_has_multiple_lines = True
                        segment_for_this_s.is_full = False
                    current_group = _LineGroup(
                        lb_corresp=elem.get("corresp"),
                        lb_element=elem,
                    )
                    groups.append(current_group)
                    segment_for_this_s = _SentenceSegment(
                        s_elem=s_elem, s_xml_id=s_id
                    )
                    current_group.segments.append(segment_for_this_s)

                else:  # "token"
                    if current_group is None:
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
                    segment_for_this_s.tokens.append(elem)
                    segment_for_this_s.token_langs.append(fgn)

            if s_has_multiple_lines:
                for g in groups:
                    for seg in g.segments:
                        if seg.s_elem is s_elem:
                            seg.is_full = False

        elif tag == "lb":
            current_group = _LineGroup(
                lb_corresp=s_elem.get("corresp"),
                lb_element=s_elem,
            )
            groups.append(current_group)

    return groups


def _append_tokens_with_foreign(parent, tokens, token_langs):
    """
    Move ``tokens`` under ``parent`` re-wrapping foreign-lang runs.

    Consecutive tokens sharing the same ``token_langs`` value (other
    than None) are grouped inside a fresh ``<foreign xml:lang="…">``
    child of ``parent``. ``None`` entries go directly under ``parent``.
    """
    current_foreign = None
    current_lang = None
    for token, lang in zip(tokens, token_langs):
        if lang and lang != current_lang:
            current_foreign = etree.SubElement(parent, "foreign")
            current_foreign.set(XML_LANG, lang)
            current_lang = lang
        elif not lang:
            current_foreign = None
            current_lang = None
        target = current_foreign if current_foreign is not None else parent
        target.append(token)


def _new_sentence_fragment(parent, seg, line_index, fragment_ids, s_occurrences):
    """
    Create one <s> fragment under *parent* with its fragmentation links.

    A sentence spanning several lines is split into one <s> per line,
    chained by @part (I/M/F) and @next/@prev. The modernized and plain
    rebuild paths differ ONLY by the parent element (audit 4.8: the
    22-line chaining logic used to be written out twice, so any fix to
    the fragmentation had to be made in both copies).
    """
    s_new = etree.SubElement(parent, "s")
    frag_id = fragment_ids.get((seg.s_xml_id, line_index), seg.s_xml_id)
    s_new.set(XML_ID, frag_id)

    occurrences = s_occurrences.get(seg.s_xml_id, [])
    if len(occurrences) <= 1:
        return s_new

    frag_idx = [idx for idx, (li, _) in enumerate(occurrences) if li == line_index][0]
    total = len(occurrences)

    if frag_idx == 0:
        s_new.set("part", "I")
    elif frag_idx == total - 1:
        s_new.set("part", "F")
    else:
        s_new.set("part", "M")

    if frag_idx < total - 1:
        next_id = fragment_ids.get((seg.s_xml_id, occurrences[frag_idx + 1][0]))
        if next_id:
            s_new.set("next", f"#{next_id}")
    if frag_idx > 0:
        prev_id = fragment_ids.get((seg.s_xml_id, occurrences[frag_idx - 1][0]))
        if prev_id:
            s_new.set("prev", f"#{prev_id}")

    return s_new


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

    # Phase 3: Clear container children (any <foreign> that survived
    # enrichment is a leftover — its content has already been collected
    # into seg.tokens via the recursive walk, so we drop the wrappers
    # and let _append_tokens_with_foreign recreate them).
    attribs = dict(container.attrib)
    container.text = None
    for child in list(container):
        container.remove(child)
    for k, v in attribs.items():
        container.set(k, v)

    # Phase 4: Rebuild
    count = 0

    for i, group in enumerate(groups):
        # Add <lb/>, carrying over the sourceDoc pointers (audit 1.6)
        if group.lb_element is not None:
            lb = etree.SubElement(container, "lb")
            if group.lb_corresp:
                lb.set("corresp", group.lb_corresp)
            facs = group.lb_element.get("facs")
            if facs:
                lb.set("facs", facs)

        # Check if this line is modernized
        is_modernized = group.lb_corresp and group.lb_corresp in corresp_to_mod

        if is_modernized:
            choice = etree.SubElement(container, "choice")
            orig = etree.SubElement(choice, "orig")

            for seg in group.segments:
                s_new = _new_sentence_fragment(
                    orig, seg, i, fragment_ids, s_occurrences
                )

                _append_tokens_with_foreign(s_new, seg.tokens, seg.token_langs)

            _make_reg(choice, corresp_to_mod[group.lb_corresp])
            count += 1

        else:
            # Non-modernized line: add <s> fragments directly to container
            for seg in group.segments:
                s_new = _new_sentence_fragment(
                    container, seg, i, fragment_ids, s_occurrences
                )

                _append_tokens_with_foreign(s_new, seg.tokens, seg.token_langs)

    return count


def _reading_text(reading):
    """Text of a Reading, or of a bare string (a caller that grades nothing)."""
    return reading.text if hasattr(reading, "text") else reading


def _make_reg(choice, reading):
    """
    Write the modernized reading, attributed.

    <reg> used to carry neither @resp nor @cert although the pipeline
    computes a similarity score for every line (audit 1.12). The grade
    is decided upstream, between what was SENT to the API and what came
    BACK — recomputing it here from a re-derived "original" scored
    dehyphenation as if it were an editorial rewrite.
    """
    text, cert = (reading.text, reading.cert) if hasattr(reading, "text") else (reading, None)
    reg = etree.SubElement(choice, "reg", type="modernized")
    reg.text = text
    reg.set("resp", "#modernize-auto")
    # Declared here rather than by the caller: apply_modernization* are
    # public entry points (CLAUDE.md documents standalone use), and a
    # @resp with no respStmt to point at is a dangling pointer.
    root = choice.getroottree().getroot()
    declare_responsibility(
        root, "modernize-auto",
        "Modernisation automatique des formes anciennes", "VieuxParler",
    )
    reg.set("cert", cert or "unknown")
    return reg


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
    _make_reg(choice, modernized)
    return choice


def _make_pb(line):
    """<pb> pointing at its surface via @corresp and @facs, with the
    surface's number as @n when the sourceDoc carries one (audit 1.6)."""
    atts = {"corresp": f"#{line.page_id}", "facs": f"#{line.page_id}"}
    if line.page_n:
        atts["n"] = line.page_n
    return etree.Element("pb", atts)


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

    last_page_id = None

    for line in data:
        # Prepare zone attributes (without language for now)
        zone_atts = {"corresp": f"#{line.zone_id}", "type": line.zone_type}

        # Create <lb/> with reference to line's xml:id: @corresp plus
        # @facs, the canonical attribute for TEI/IIIF viewers (audit 1.6)
        lb = etree.Element("lb", corresp=f"#{line.id}", facs=f"#{line.id}")
        lb.tail = f"{line.text}"

        # Add page break when the page changes (not per zone)
        if line.page_id != last_page_id:
            div.append(_make_pb(line))
            last_page_id = line.page_id

        # Ensure div has at least one element
        if len(div) == 0:
            div.append(_make_pb(line))

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
            # Main text -> <ab>. The type check keeps Main text out of the
            # fallback <ab> a preceding unhandled zone may have opened.
            if last_element.tag != "ab" or not (last_element.get("type") or "").startswith("Main"):
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

            # Regular lines — and any other line type: an unknown label
            # must not silently drop the line's text (audit 1.1).
            else:
                last_element.append(lb)

        else:
            # Fallback for zone types without a dedicated branch
            # (TitlePageZone, GraphicZone, StampZone, TableZone,
            # CustomZone, ...): a plain <ab type="..."> per zone, so no
            # text is ever silently lost (audit 1.1). Consecutive lines
            # of the same zone share one <ab>.
            if last_element.tag == "ab" and last_element.get("corresp") == zone_atts["corresp"]:
                last_element.append(lb)
                if detector and containers and containers[-1][0] == last_element:
                    containers[-1][1].append(line.text)
            else:
                logger.debug(
                    "Zone type %r has no dedicated body element; falling back to <ab>",
                    line.zone_type,
                )
                ab = etree.Element("ab", {k: v for k, v in zone_atts.items() if v})
                last_element.addnext(ab)
                ab.append(lb)
                if detector:
                    containers.append((ab, [line.text]))

    # Apply language detection to containers (two-pass)
    if detector:
        # Pass 1: establish document-level dominant language
        all_texts = (t for _, texts in containers for t in texts if t)
        detector.compute_document_prior(all_texts)
        # Pass 2: detect per-container with document prior as bias
        _apply_language_detection(containers, detector)
        return detector.get_stats()

    return None


def _apply_language_detection(containers, detector):
    """
    Apply language detection to container elements.

    Detects the primary language of each container and adds xml:lang.
    For mixed-language containers, splices <foreign> elements inline
    around the corresponding portions of the <lb/> tails so that
    downstream stages (enrichment, modernization) can re-tag each
    language span with the appropriate model.

    Args:
        containers: List of (element, [line_texts]) tuples.
        detector: LinguaDetector instance.
    """
    for element, line_texts in containers:
        # Join non-empty line texts. Offsets in this joined text align
        # with the per-line tails via _build_offset_to_line below.
        full_text = " ".join(t for t in line_texts if t)

        if not full_text.strip():
            continue

        # Primary language + foreign segments in one pass (audit 3.1/3.6:
        # single cleaning, multi-language scan gated on confidence).
        primary_lang, segments = detector.detect_primary_and_segments(full_text)
        if primary_lang:
            element.attrib[XML_LANG] = primary_lang

        if segments:
            _insert_foreign_inline(element, line_texts, segments)


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
        # A Reading is only present for a line the API actually changed
        # (grade_readings puts None everywhere else); a bare string still
        # gets the old "did anything change?" check.
        if mod is None:
            continue
        if not hasattr(mod, "text") and (not mod or mod == original):
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
        if not hasattr(mod_text, "text") and mod_text == original:
            continue
        lb.tail = None
        choice = _append_choice(lb.getparent(), original, mod_text)
        lb.addnext(choice)
        count += 1
    return count


def _insert_foreign_inline(element, line_texts, segments):
    """
    Splice <foreign> elements inline around foreign-language runs.

    For each segment ``(start, end, lang)`` with offsets in the joined
    ``" ".join(t for t in line_texts if t)`` text, this locates the
    corresponding portion of the matching <lb/> tail and wraps it in
    a ``<foreign xml:lang="...">`` sibling inserted right after the <lb/>.

    Segments that cross a line boundary produce one <foreign> per
    affected line (the intervening line break stays where it was).

    Args:
        element: Container element (<ab>, <note>, <fw>).
        line_texts: List of per-line texts in the same order as the
            <lb/> children (including empty ones).
        segments: Iterable of (start, end, lang) tuples returned by
            :meth:`LinguaDetector.detect_foreign_segments`.
    """
    if not segments:
        return

    offset_to_line = _build_offset_to_line(line_texts)

    # Group splice ops per line (a single segment may touch several).
    splices_per_line = {}
    for seg_start, seg_end, seg_lang in segments:
        line_ranges = {}  # line_idx -> [min_j, max_j]
        for pos in range(seg_start, min(seg_end, len(offset_to_line))):
            line_idx, j = offset_to_line[pos]
            if line_idx < 0:
                continue
            r = line_ranges.get(line_idx)
            if r is None:
                line_ranges[line_idx] = [j, j]
            else:
                if j < r[0]:
                    r[0] = j
                if j > r[1]:
                    r[1] = j
        for line_idx, (j_min, j_max) in line_ranges.items():
            splices_per_line.setdefault(line_idx, []).append(
                (j_min, j_max + 1, seg_lang)
            )

    if not splices_per_line:
        return

    lbs = list(element.iter("lb"))
    for line_idx, ops in splices_per_line.items():
        if line_idx >= len(lbs):
            continue
        _splice_lb_tail(lbs[line_idx], ops)


def _build_offset_to_line(line_texts):
    """
    Build a position map for ``" ".join(t for t in line_texts if t)``.

    Returns a list where ``offset_to_line[i] = (line_idx, j)`` means the
    character at offset ``i`` in the joined string comes from
    ``line_texts[line_idx][j]``. A marker ``(-1, -1)`` is used for the
    separator spaces inserted by the join.
    """
    offset_to_line = []
    non_empty_seen = False
    for line_idx, t in enumerate(line_texts):
        if not t:
            continue
        if non_empty_seen:
            offset_to_line.append((-1, -1))
        non_empty_seen = True
        for j in range(len(t)):
            offset_to_line.append((line_idx, j))
    return offset_to_line


def _splice_lb_tail(lb, splice_ops):
    """
    Splice a <lb/>'s tail with one or more <foreign> elements.

    Args:
        lb: The <lb/> lxml Element.
        splice_ops: Iterable of ``(start, end, lang)`` in the tail's
            character positions. Overlapping ops are dropped after the
            first one is accepted (sorted by start).
    """
    tail = lb.tail or ""
    if not tail:
        return

    # Sort, clamp, drop overlaps.
    clean_ops = []
    prev_end = 0
    for s, e, lang in sorted(splice_ops, key=lambda x: x[0]):
        s = max(0, min(s, len(tail)))
        e = max(s, min(e, len(tail)))
        if s < prev_end or s >= e:
            continue
        clean_ops.append((s, e, lang))
        prev_end = e

    if not clean_ops:
        return

    # Build alternating (text | foreign-element) items covering the tail.
    items = []
    cursor = 0
    for s, e, lang in clean_ops:
        if s > cursor:
            items.append(("text", tail[cursor:s]))
        f = etree.Element("foreign")
        f.set(XML_LANG, lang)
        f.text = tail[s:e]
        items.append(("foreign", f))
        cursor = e
    if cursor < len(tail):
        items.append(("text", tail[cursor:]))

    # Apply: rewrite lb.tail as the first run of text (empty if items
    # start with a foreign), insert each foreign as a sibling, and
    # attach subsequent text runs as the tail of the previously
    # inserted element.
    lb.tail = None
    prev = lb
    for kind, payload in items:
        if kind == "text":
            prev.tail = payload
        else:
            prev.addnext(payload)
            prev = payload
