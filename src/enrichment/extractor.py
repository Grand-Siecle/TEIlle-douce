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


def extract_spans(container):
    """
    Extract text spans from a TEI container element.

    Walks the container's children, extracting text from <lb/> tails
    and <hi> sub-elements. Builds a concatenated raw_text with spans
    tracking their offsets.

    Args:
        container: An lxml Element (<ab>, <note>, or <fw>).

    Returns:
        tuple: (raw_text, spans) where raw_text is the concatenated text
               and spans is a list of TextSpan objects.
    """
    spans = []
    offset = 0
    line_index = 0

    for child in container:
        tag = etree.QName(child.tag).localname if isinstance(child.tag, str) else child.tag

        if tag == "lb":
            span, offset, line_index = _process_lb(
                child, None, None, offset, line_index, spans
            )

        elif tag == "hi":
            hi_rend = child.get("rend")
            for hi_child in child:
                hi_tag = etree.QName(hi_child.tag).localname if isinstance(hi_child.tag, str) else hi_child.tag
                if hi_tag == "lb":
                    span, offset, line_index = _process_lb(
                        hi_child, child, hi_rend, offset, line_index, spans
                    )

        elif tag == "foreign":
            # Skip existing <foreign> elements (will be replaced)
            pass

    raw_text = "".join(s.text for s in spans)
    return raw_text, spans


def _process_lb(lb_elem, hi_elem, hi_rend, offset, line_index, spans):
    """
    Process a single <lb/> element and its tail text.

    Args:
        lb_elem: The <lb/> element.
        hi_elem: Parent <hi> element if inside one, else None.
        hi_rend: @rend value of parent <hi>, or None.
        offset: Current offset in raw_text.
        line_index: Current line counter.
        spans: List to append the new TextSpan to.

    Returns:
        tuple: (span_or_None, new_offset, new_line_index)
    """
    text = lb_elem.tail or ""
    if not text:
        # Still record a span for the lb even without text
        spans.append(TextSpan(
            text="",
            offset_start=offset,
            offset_end=offset,
            lb_element=lb_elem,
            lb_corresp=lb_elem.get("corresp"),
            hi_element=hi_elem,
            hi_rend=hi_rend,
            line_index=line_index,
            has_hyphen=False,
        ))
        return None, offset, line_index + 1

    # Add space separator between lines (except first)
    if spans and spans[-1].text:
        text = " " + text

    has_hyphen = text.rstrip().endswith("¬") or text.rstrip().endswith("-")

    span = TextSpan(
        text=text,
        offset_start=offset,
        offset_end=offset + len(text),
        lb_element=lb_elem,
        lb_corresp=lb_elem.get("corresp"),
        hi_element=hi_elem,
        hi_rend=hi_rend,
        line_index=line_index,
        has_hyphen=has_hyphen,
    )
    spans.append(span)

    return span, offset + len(text), line_index + 1
