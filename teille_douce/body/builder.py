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
from collections import defaultdict
from dataclasses import dataclass, field

from lxml import etree

from ..constants import TEXT_CONTAINERS, XML_ID, XML_LANG
from ..lang import get_detector
from ..utils.xml import content_root, declare_responsibility, local_tag
from .text import GRAPHIC_ZONES

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
        ctag = local_tag(child.tag)
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

        tag = local_tag(s_elem.tag)
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

    # A group with no <lb> to identify it cannot be matched against the
    # modernized lines, which are keyed by @corresp: its text would go
    # through the API and come back to nothing (audit 6.8). build_body
    # always emits an <lb> before a line's text, so this does not happen
    # today — but it would be a silent hole in the modernization the day
    # it did, and a silent hole is what a warning is for.
    for group in groups:
        if group.lb_corresp is None and group.segments:
            logger.warning(
                "Line group in <%s> %s: its text cannot be modernized",
                local_tag(container.tag),
                "has no <lb> to anchor it" if group.lb_element is None
                else "has an <lb> with no @corresp",
            )
            break

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


def _rebuild_with_modernization(container, groups, corresp_to_mod, stats=None):
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
        stats: Optional dict to report into, as the enrichment phase does
            (audit 2.7): a container left untouched increments
            "containers_failed" so the run can say its readings were lost.

    Returns:
        int: Number of lines wrapped in <choice>.
    """
    # _parse_line_groups only collects <s> and <lb>; anything else a
    # container holds is not in *groups* and would be dropped by the
    # clearing below. A container already carrying <choice> has been
    # modernized once — rebuilding it would delete the readings written
    # then, keeping only what the groups hold.
    unhandled = [
        local_tag(child.tag) for child in container
        if local_tag(child.tag) not in ("s", "lb")
    ]
    if unhandled:
        logger.warning(
            "<%s> holds %s outside the <s>/<lb> structure: leaving it "
            "untouched rather than rebuilding it without them",
            local_tag(container.tag), ", ".join(sorted(set(unhandled))),
        )
        # Leaving it alone is the right call, but its modernization IS
        # lost: counted, so the console does not report the document as
        # simply having had nothing to modernize.
        if stats is not None:
            stats["containers_failed"] = stats.get("containers_failed", 0) + 1
        return 0

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


def _continues_zone(element, zone_atts):
    """True when *element* is an open container for the SAME source zone.

    Merging consecutive lines is right within one zone and wrong across
    two: a container's @corresp names one zone of the sourceDoc, and
    everything downstream resolves it back — note anchoring, IIIF crops,
    entity references. Two marginal glosses stacked on a page, or the two
    columns of a page, are two zones; welding them made the second
    disappear behind the first one's identity.
    """
    return (
        element is not None
        and element.tag == zone_atts["tag"]
        and element.get("corresp") == zone_atts["corresp"]
    )


def _figure_caption(figure):
    """The <ab> holding a figure's transcribed lines, created on demand.

    Text inside a GraphicZone is transcription, not editorial description,
    so it goes to <ab> rather than <figDesc> — and <ab> is what the
    enrichment and NER phases know how to walk.
    """
    for child in figure:
        if child.tag == "ab":
            return child
    atts = {"corresp": figure.get("corresp")}
    if figure.get("type"):
        atts["type"] = figure.get("type")
    return etree.SubElement(figure, "ab", atts)


def build_body(root, data, detect_lang=True, graphics=None, front_pages=None):
    """
    Build the TEI <text> element from extracted sourceDoc data.

    Creates the text structure with appropriate TEI elements:
    - <front><titlePage> for the pages given over to a title page
    - <pb/> for page breaks
    - <fw> for running titles, page numbers, quire marks
    - <note> for margin text
    - <figure><graphic/> for illustrations, with their IIIF crop
    - <div><head> opened by each run of heading lines
    - <ab> for main text blocks
    - <hi> for emphasized lines (drop capitals)
    - <lb/> for line breaks

    When detect_lang=True, detects language at the container level
    and adds xml:lang attributes. For mixed-language containers,
    uses <foreign> tags for non-primary language segments.

    Args:
        root (etree.Element): TEI root element to append text to.
        data (Text or list): The Text built from the sourceDoc — pass it
            whole; it carries the lines, the graphic zones and the front
            pages, and passing only its lines silently drops the figures
            and the front matter. A bare list of Line namedtuples stays
            accepted for hand-built callers.
        detect_lang (bool): Whether to detect and add xml:lang attributes.
        graphics (list): Graphic namedtuples, in reading order. Defaults
            to the ones carried by *data*.
        front_pages (set): Surface xml:ids to route to <front>. Defaults
            to the ones carried by *data*.

    Returns:
        dict or None: Language statistics if detect_lang=True, else None.
    """
    if hasattr(data, "data"):  # a Text: take everything it extracted
        if graphics is None:
            graphics = data.graphics
        if front_pages is None:
            front_pages = data.front_pages
        data = data.data

    # Initialize language detector if needed
    detector = None
    if detect_lang:
        detector = get_detector()
        detector.reset_stats()

    text_el = etree.SubElement(root, "text")
    body = etree.SubElement(text_el, "body")
    body_div = etree.SubElement(body, "div")
    front = None
    current_page = None
    front_pages = front_pages or set()

    # Graphics keyed by the number of lines that precede them, so an
    # illustration lands at its place in reading order — including one
    # with no TextLine of its own, which no line could ever announce.
    graphics_at = defaultdict(list)
    for graphic in graphics or ():
        graphics_at[graphic.after_lines].append(graphic)

    containers = []       # (element, list of line texts) for language detection
    registered = {}       # id(element) -> its list of texts
    figures = {}          # zone xml:id -> <figure>, to hang its lines on
    title_pages = {}      # page xml:id -> <titlePage>

    def register(element, text):
        """Record a line's text under its container.

        Keyed by element rather than by "is it the last one registered":
        the figure captions and title parts are re-entered after other
        elements have been emitted, and a second entry for the same
        element would make _apply_language_detection run over it twice,
        splicing <foreign> against a truncated list of line texts.
        """
        if not detector:
            return
        texts = registered.get(id(element))
        if texts is None:
            texts = [text]
            registered[id(element)] = texts
            containers.append((element, texts))
        else:
            texts.append(text)

    def container_for(page_id):
        """<front> for a page given over to a title page, else the open <div>."""
        nonlocal front
        if page_id in front_pages:
            if front is None:
                front = etree.Element("front")
                text_el.insert(0, front)
            return front
        return body_div

    def open_page(container, record):
        """Emit the <pb> of *record*'s page when it is a new one."""
        nonlocal current_page
        if record.page_id != current_page:
            container.append(_make_pb(record))
            current_page = record.page_id
        elif len(container) == 0:
            container.append(_make_pb(record))

    def emit_graphic(graphic):
        container = container_for(graphic.page_id)
        open_page(container, graphic)
        atts = {"corresp": f"#{graphic.zone_id}", "facs": f"#{graphic.zone_id}"}
        if graphic.zone_type:
            atts["type"] = graphic.zone_type
        figure = etree.SubElement(container, "figure", atts)
        if graphic.source:
            # The sourceDoc already holds the IIIF crop of the zone.
            etree.SubElement(figure, "graphic", url=graphic.source)
        else:
            logger.debug("Graphic zone %s has no IIIF source", graphic.zone_id)
        figures[graphic.zone_id] = figure

    for index, line in enumerate(data):
        for graphic in graphics_at.get(index, ()):
            emit_graphic(graphic)

        # Prepare zone attributes (without language for now)
        zone_atts = {"corresp": f"#{line.zone_id}", "type": line.zone_type}

        # Create <lb/> with reference to line's xml:id: @corresp plus
        # @facs, the canonical attribute for TEI/IIIF viewers (audit 1.6)
        lb = etree.Element("lb", corresp=f"#{line.id}", facs=f"#{line.id}")
        lb.tail = f"{line.text}"

        container = container_for(line.page_id)
        open_page(container, line)
        last_element = container[-1] if len(container) else None
        last_tag = last_element.tag if last_element is not None else None

        # Handle different zone types
        if line.zone_type in ("NumberingZone", "QuireMarksZone", "RunningTitleZone"):
            # Page numbers, quire marks, running titles -> <fw>.
            # Consecutive lines of the SAME zone share one <fw>, as <ab>
            # and <note> already did: a running title set on two lines is
            # one running title, and splitting it left each half as a
            # separate piece of apparatus (audit 6.2).
            if _continues_zone(last_element, {**zone_atts, "tag": "fw"}):
                last_element.append(lb)
                register(last_element, line.text)
            else:
                fw = etree.SubElement(container, "fw", zone_atts)
                fw.append(lb)
                register(fw, line.text)

        elif line.zone_type == "MarginTextZone":
            # Margin text -> <note place="margin">. Without @place a
            # reader cannot tell a marginal gloss from a footnote or an
            # editorial remark: the zone knows, the note did not say.
            if _continues_zone(last_element, {**zone_atts, "tag": "note"}):
                last_element.append(lb)
                register(last_element, line.text)
            else:
                note = etree.SubElement(
                    container, "note", {**zone_atts, "place": "margin"}
                )
                note.append(lb)
                register(note, line.text)

        elif line.zone_type in GRAPHIC_ZONES and line.zone_id in figures:
            # Text transcribed inside an illustration stays inside its
            # <figure> rather than floating next to it as a bare <ab>.
            caption = _figure_caption(figures[line.zone_id])
            caption.append(lb)
            register(caption, line.text)

        elif line.zone_type == "TitlePageZone" and container is front:
            # Title page -> <front><titlePage><titlePart>. One <titlePart>
            # per zone: the pipeline has no signal telling a title from a
            # byline or an imprint, and inventing that distinction would
            # be an editorial claim the OCR does not support.
            # Kept per page, not per neighbouring element: a signature or
            # a folio number printed between two title zones must not
            # split the page into two <titlePage>.
            title_page = title_pages.get(line.page_id)
            if title_page is None:
                title_page = etree.SubElement(
                    container, "titlePage", {"facs": f"#{line.page_id}"}
                )
                title_pages[line.page_id] = title_page
            part = next(
                (c for c in title_page
                 if c.tag == "titlePart" and c.get("corresp") == zone_atts["corresp"]),
                None,
            )
            if part is None:
                part = etree.SubElement(title_page, "titlePart", zone_atts)
            part.append(lb)
            register(part, line.text)

        elif line.zone_type and line.zone_type.startswith("Main"):
            # A heading opens a section: it becomes the <head> of a new
            # <div>, which is also the only place TEI accepts a head.
            # Front matter has no <div> to open, so a heading there keeps
            # the <hi> treatment below.
            if line.line_type == "HeadingLine" and container is body_div:
                # A title set on several lines is ONE title: consecutive
                # heading lines of the same zone continue the same <head>
                # instead of opening a section per line.
                if last_tag == "head" and last_element.get("corresp") == zone_atts["corresp"]:
                    last_element.append(lb)
                    register(last_element, line.text)
                    continue

                new_div = etree.SubElement(body, "div")
                if last_tag == "pb":
                    # The page opens on the heading: its page break belongs
                    # to the section starting here, not to the one ending.
                    new_div.append(last_element)
                head = etree.SubElement(new_div, "head", zone_atts)
                head.append(lb)
                register(head, line.text)
                body_div = new_div
                continue

            # Main text -> <ab>, one per source zone: two columns of a
            # page are two MainZones, and merging them left the second
            # inside an <ab> claiming to be the first.
            if not _continues_zone(last_element, {**zone_atts, "tag": "ab"}):
                last_element = etree.SubElement(container, "ab", zone_atts)
            register(last_element, line.text)

            # Handle emphasized lines (drop capitals, headings in front)
            if line.line_type in ("DropCapitalLine", "HeadingLine"):
                ab_children = list(last_element)
                # Grouping looks at the immediate sibling only, and that
                # is deliberate (audit 6.3 reads it as a defect): an
                # ordinary line between two drop-capital runs means two
                # different initials, not one interrupted. Merging them
                # would claim a single ornament spanning text that sits
                # between them.
                # Check if we need a new <hi> or can reuse existing
                if (
                    len(ab_children) == 0
                    or ab_children[-1].tag != "hi"
                    or ab_children[-1].get("rend") != line.line_type
                ):
                    hi = etree.SubElement(last_element, "hi", rend=line.line_type)
                    hi.append(lb)
                else:
                    ab_children[-1].append(lb)

            # Regular lines — and any other line type: an unknown label
            # must not silently drop the line's text (audit 1.1).
            else:
                last_element.append(lb)

        else:
            # Fallback for zone types without a dedicated branch (StampZone,
            # TableZone, CustomZone, and a TitlePageZone sitting on a page
            # that also carries running text — <titlePage> is front matter,
            # TEI does not accept it inside a <div>): a plain
            # <ab type="..."> per zone, so no text is ever silently lost
            # (audit 1.1). Consecutive lines of the same zone share one <ab>.
            if last_tag == "ab" and last_element.get("corresp") == zone_atts["corresp"]:
                last_element.append(lb)
                register(last_element, line.text)
            else:
                logger.debug(
                    "Zone type %r has no dedicated body element; falling back to <ab>",
                    line.zone_type,
                )
                ab = etree.SubElement(
                    container, "ab", {k: v for k, v in zone_atts.items() if v}
                )
                ab.append(lb)
                register(ab, line.text)

    # Trailing graphics: everything the loop never reached. Keyed on ">="
    # rather than "== len(data)" so a miscount can only misplace a figure,
    # never drop it.
    for index in sorted(k for k in graphics_at if k >= len(data)):
        for graphic in graphics_at[index]:
            emit_graphic(graphic)

    # Drop the <div> a document opening on a heading leaves empty, then
    # keep <body> non-empty: neither an empty <body> nor an empty <div>
    # is valid TEI, and a volume that is nothing but a title page would
    # produce both.
    for div in [el for el in body if el.tag == "div" and len(el) == 0]:
        body.remove(div)
    if len(body) == 0:
        etree.SubElement(etree.SubElement(body, "div"), "ab")

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
    body = content_root(root)
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


def count_containers(root):
    """How many containers this document offers modernization.

    Read before the service is asked anything: it is the denominator the
    phase's losses are measured against, and counting it inside the walk
    that only runs once the service answered is what kept a dead service
    reporting "0 of 0 containers".
    """
    # On the local name: bare and namespaced tags coexist in the same
    # tree (audit 4.2), and `iter("ab")` sees only the bare ones — so a
    # namespaced document counted zero containers and its denominator
    # went back to being useless.
    return sum(1 for element in root.iter()
               if local_tag(element.tag) in TEXT_CONTAINERS)


def apply_modernization_enriched(root, corresp_to_mod, stats=None):
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
        stats: Optional dict to report into; "containers_failed" counts
            the containers whose readings could not be applied.

    Returns:
        int: Number of lines wrapped in <choice>.
    """
    body = content_root(root)
    if body is None:
        return 0

    count = 0
    # TEXT_CONTAINERS, not a literal list: <head> and <titlePart> are
    # enriched and scanned for entities, so leaving them out here paid
    # for VieuxParler round-trips whose readings nothing consumed —
    # the title page came back modernized and was dropped on the floor.
    for container in body.iter(*TEXT_CONTAINERS):
        has_sentences = any(local_tag(c.tag) == "s" for c in container)

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

            count += _rebuild_with_modernization(
                container, groups, corresp_to_mod, stats=stats
            )
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


def _word_start_at_or_after(text, pos):
    """First word start at or after *pos*, or None if there is none."""
    if pos <= 0 or pos >= len(text):
        return pos if pos < len(text) else None
    if text[pos - 1].isspace():
        return pos
    space = next((i for i in range(pos, len(text)) if text[i].isspace()), None)
    if space is None:
        return None
    while space < len(text) and text[space].isspace():
        space += 1
    return space if space < len(text) else None


def _splice_lb_tail(lb, splice_ops):
    """
    Splice a <lb/>'s tail with one or more <foreign> elements.

    Args:
        lb: The <lb/> lxml Element.
        splice_ops: Iterable of ``(start, end, lang)`` in the tail's
            character positions. Two segments cannot cover the same
            characters — one XML element cannot be in two languages — so
            an overlapping one is trimmed to what is left free, and
            dropped (with a warning) only when nothing is.
    """
    tail = lb.tail or ""
    if not tail:
        return

    # Sort, clamp, resolve overlaps.
    clean_ops = []
    prev_end = 0
    for s, e, lang in sorted(splice_ops, key=lambda x: x[0]):
        s = max(0, min(s, len(tail)))
        e = max(s, min(e, len(tail)))
        if s >= e:
            logger.debug(
                "Foreign segment (%s) ignored: empty or outside the line %r",
                lang, tail[:40],
            )
            continue
        if s < prev_end:
            # Overlap: keep the part the previous segment left free
            # rather than losing the language of the whole segment. The
            # detector returns character ranges over a joined text, and
            # two ranges can meet on a word boundary it read twice.
            start = _word_start_at_or_after(tail, prev_end)
            if start is None or start >= e:
                logger.warning(
                    "Foreign segment (%s) dropped: the previous segment "
                    "leaves no whole word free on line %r", lang, tail[:40],
                )
                continue
            # Snapped to a word boundary: cutting at the raw offset would
            # open a <foreign> mid-word, and the two halves would then be
            # tagged by two different language models.
            logger.debug(
                "Foreign segments overlap on %r: %r trimmed from %d to %d",
                tail[:40], lang, s, start,
            )
            s = start
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
