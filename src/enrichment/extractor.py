# -----------------------------------------------------------
# Text extraction from TEI body containers with offset mapping.
# -----------------------------------------------------------
"""
Text extractor (Phase 1).

Extracts text from TEI body containers (<ab>, <note>, <fw>),
building a list of TextSpan objects that map text segments back
to their source XML elements.
"""

from dataclasses import dataclass
from lxml import etree

from ..constants import NS_XML


XML_LANG = f"{{{NS_XML}}}lang"


@dataclass
class TextSpan:
    """A text segment mapped to its source XML element."""
    text: str
    offset_start: int
    offset_end: int
    lb_element: object  # etree.Element or None
    lb_corresp: str | None
    hi_element: object  # etree.Element or None
    hi_rend: str | None
    line_index: int
    has_hyphen: bool
    # Language of the text fragment. ``None`` means the container's
    # primary language; a TEI ident (e.g. ``"lat"``) means the text
    # came from inside a <foreign xml:lang="…"> element.
    lang: str | None = None
    # Parent <foreign> element if the text is inside one, else None.
    # Kept for the reconstructor so it can rebuild the wrapper.
    foreign_element: object = None


def extract_spans(container):
    """
    Extract text spans from a TEI container element.

    Walks the container's children, extracting text from <lb/> tails,
    <hi> sub-elements, and <foreign> elements inserted by the
    language-detection phase. Builds a concatenated raw_text with
    spans tracking their offsets.

    Args:
        container: An lxml Element (<ab>, <note>, or <fw>).

    Returns:
        tuple: (raw_text, spans) where raw_text is the concatenated text
               and spans is a list of TextSpan objects.
    """
    spans = []
    state = _ExtractState(offset=0, line_index=0, last_lb=None)
    _walk_children(container, None, None, spans, state)
    raw_text = "".join(s.text for s in spans)
    return raw_text, spans


@dataclass
class _ExtractState:
    """Mutable offsets / counters threaded through the walk."""
    offset: int
    line_index: int
    last_lb: object  # last <lb/> element seen, for <foreign> association


def _walk_children(parent, hi_elem, hi_rend, spans, state):
    """
    Iterate ``parent``'s children, dispatching on tag.

    ``hi_elem``/``hi_rend`` are propagated from an enclosing <hi>.
    """
    for child in parent:
        tag = etree.QName(child.tag).localname if isinstance(child.tag, str) else child.tag

        if tag == "lb":
            _process_lb(child, hi_elem, hi_rend, spans, state)

        elif tag == "hi":
            new_hi_rend = child.get("rend")
            _walk_children(child, child, new_hi_rend, spans, state)

        elif tag == "foreign":
            _process_foreign(child, hi_elem, hi_rend, spans, state)


def _process_lb(lb_elem, hi_elem, hi_rend, spans, state):
    """Record a TextSpan for a <lb/> and its tail text."""
    state.last_lb = lb_elem

    text = lb_elem.tail or ""
    if not text:
        spans.append(TextSpan(
            text="",
            offset_start=state.offset,
            offset_end=state.offset,
            lb_element=lb_elem,
            lb_corresp=lb_elem.get("corresp"),
            hi_element=hi_elem,
            hi_rend=hi_rend,
            line_index=state.line_index,
            has_hyphen=False,
            lang=None,
            foreign_element=None,
        ))
        state.line_index += 1
        return

    text = _maybe_prepend_separator(text, state.line_index, spans)

    has_hyphen = text.rstrip().endswith("¬") or text.rstrip().endswith("-")

    spans.append(TextSpan(
        text=text,
        offset_start=state.offset,
        offset_end=state.offset + len(text),
        lb_element=lb_elem,
        lb_corresp=lb_elem.get("corresp"),
        hi_element=hi_elem,
        hi_rend=hi_rend,
        line_index=state.line_index,
        has_hyphen=has_hyphen,
        lang=None,
        foreign_element=None,
    ))
    state.offset += len(text)
    state.line_index += 1


def _process_foreign(foreign_elem, hi_elem, hi_rend, spans, state):
    """
    Record spans for a <foreign> element's text and tail.

    A <foreign> inserted by the language-detection phase lives on the
    same line as the preceding <lb/>, so both ``.text`` (foreign-lang)
    and ``.tail`` (back to primary-lang) inherit that line's index and
    lb reference. The lang field distinguishes the two halves.
    """
    foreign_lang = foreign_elem.get(XML_LANG)
    anchor_lb = state.last_lb
    anchor_corresp = anchor_lb.get("corresp") if anchor_lb is not None else None
    anchor_line_idx = max(0, state.line_index - 1)

    if foreign_elem.text:
        text = _maybe_prepend_separator(foreign_elem.text, anchor_line_idx, spans)
        has_hyphen = text.rstrip().endswith("¬") or text.rstrip().endswith("-")
        spans.append(TextSpan(
            text=text,
            offset_start=state.offset,
            offset_end=state.offset + len(text),
            lb_element=anchor_lb,
            lb_corresp=anchor_corresp,
            hi_element=hi_elem,
            hi_rend=hi_rend,
            line_index=anchor_line_idx,
            has_hyphen=has_hyphen,
            lang=foreign_lang,
            foreign_element=foreign_elem,
        ))
        state.offset += len(text)

    if foreign_elem.tail:
        text = _maybe_prepend_separator(foreign_elem.tail, anchor_line_idx, spans)
        has_hyphen = text.rstrip().endswith("¬") or text.rstrip().endswith("-")
        spans.append(TextSpan(
            text=text,
            offset_start=state.offset,
            offset_end=state.offset + len(text),
            lb_element=anchor_lb,
            lb_corresp=anchor_corresp,
            hi_element=hi_elem,
            hi_rend=hi_rend,
            line_index=anchor_line_idx,
            has_hyphen=has_hyphen,
            lang=None,
            foreign_element=None,
        ))
        state.offset += len(text)


def _maybe_prepend_separator(text, line_idx, spans):
    """
    Prepend a single space when ``text`` starts a new line.

    The raw text concatenates per-line tails; a separator must be
    injected when the current ``line_idx`` differs from the line of
    the most recent non-empty span. No-op for empty text or when no
    previous non-empty span exists.
    """
    if not text:
        return text
    prev_non_empty = next((s for s in reversed(spans) if s.text), None)
    if prev_non_empty is not None and prev_non_empty.line_index != line_idx:
        return " " + text
    return text
