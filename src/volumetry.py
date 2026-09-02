# -----------------------------------------------------------
# How much text is there, and in which language.
# -----------------------------------------------------------
"""
Volumetry of a built TEI document.

Two questions the header has to answer with a number: how long is this
text (``<extent>``), and what share of it is in each language
(``<langUsage>``). Both are measured HERE rather than counted along the
way, for one reason: the pipeline's own bookkeeping counts containers,
and a container is not a unit of volume. Thirty short Latin quotations
against one long French page are thirty against one by container, and
roughly three percent against ninety-seven by word.

The word counts come from the ``<line>`` elements of the sourceDoc, not
from the body. The sourceDoc holds the transcription as the OCR produced
it, untouched by enrichment and modernization, so the same document
measures the same whether or not those phases ran. Counting the body
instead would make the figure depend on the flags of the run: enrichment
splits elisions into separate ``<w>``, and modernization writes each
line twice inside ``<choice>``.
"""

import logging
from collections import defaultdict

from .constants import APPARATUS_ZONES, XML_LANG
from .utils.xml import content_root, local_tag

logger = logging.getLogger(__name__)


def transcribed_text(element):
    """Text of *element*, counting each passage once.

    Two encodings would otherwise inflate a count: a modernized line
    carries ``<choice><orig>…</orig><reg>…</reg></choice>`` — the same
    passage written twice — and enrichment turns punctuation into
    ``<pc>``, which is not a word. Both are skipped, their tails kept.
    """
    parts = []

    def walk(node):
        if node.text:
            parts.append(node.text)
        for child in node:
            if isinstance(child.tag, str) and local_tag(child.tag) not in ("reg", "pc"):
                walk(child)
            if child.tail:
                parts.append(child.tail)

    walk(element)
    return " ".join(parts)


def line_word_counts(root):
    """
    Word count of every transcribed line, from the sourceDoc.

    Returns:
        dict: line zone xml:id -> (words, zone type of the block it
        belongs to). The zone type is what lets a caller leave the page
        apparatus out of a text's extent.
    """
    counts = {}
    for el in root.iter():
        if local_tag(el.tag) != "line":
            continue
        line_zone = el.getparent()
        if line_zone is None:
            continue
        block = line_zone.getparent()
        line_id = line_zone.get("{http://www.w3.org/XML/1998/namespace}id")
        if not line_id:
            continue
        counts[line_id] = (
            len((el.text or "").split()),
            block.get("type") if block is not None else None,
        )
    return counts


def text_volume(root):
    """
    Number of words of running text, page apparatus excluded.

    Returns:
        int: 0 when the document has no transcribed line.
    """
    return sum(
        words
        for words, zone_type in line_word_counts(root).values()
        if zone_type not in APPARATUS_ZONES
    )


def token_count(root):
    """Number of <w> in the text — 0 when enrichment did not run."""
    text = content_root(root)
    if text is None:
        return 0
    return sum(1 for el in text.iter() if local_tag(el.tag) == "w")


def language_volume(root):
    """
    Words per language, measured on the encoded text.

    Counted over the same text as :func:`text_volume` — page apparatus
    excluded — so the volumes published in ``<langUsage>`` add up to the
    one published in ``<extent>``. A reader who sums them gets the
    extent, instead of two numbers in one header that cannot both be the
    length of the text.

    Each container's lines are credited to the container's ``xml:lang``,
    minus the words of its ``<foreign>`` spans, which go to theirs. A
    foreign span is measured on its own text rather than on the
    sourceDoc, since it covers part of a line; after enrichment that
    text is tokenized, so a span may weigh a word or two more than it
    reads. It shifts a share by a fraction of a percent, and it is the
    only way to attribute a passage smaller than a line.

    Returns:
        dict: TEI language ident -> word count. Empty when no container
        carries a language, i.e. when the run had detection disabled.
    """
    text = content_root(root)
    if text is None:
        return {}

    line_words = line_word_counts(root)
    volume = defaultdict(int)

    for container in text.iter():
        if local_tag(container.tag) not in ("ab", "note", "fw", "head", "titlePart"):
            continue
        lang = container.get(XML_LANG)
        if not lang or container.get("type") in APPARATUS_ZONES:
            continue

        words = 0
        for el in container.iter():
            if local_tag(el.tag) != "lb":
                continue
            corresp = (el.get("corresp") or "").lstrip("#")
            words += line_words.get(corresp, (0, None))[0]

        for foreign in container.iter():
            if local_tag(foreign.tag) != "foreign":
                continue
            foreign_lang = foreign.get(XML_LANG)
            if not foreign_lang or foreign_lang == lang:
                continue
            span = len(transcribed_text(foreign).split())
            volume[foreign_lang] += span
            words -= span

        volume[lang] += max(0, words)

    return {ident: words for ident, words in volume.items() if words}
