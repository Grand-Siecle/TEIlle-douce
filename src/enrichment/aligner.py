# -----------------------------------------------------------
# Token-to-XML alignment using offset mapping.
# -----------------------------------------------------------
"""
Token aligner (Phase 4).

Maps NLP tokens (with character offsets in dehyphenated text) back to
their source XML elements using the offset_map and TextSpan data.
"""

from dataclasses import dataclass, field
from .client import NLPToken
from .extractor import TextSpan
from .dehyphenation import HyphenJoin


@dataclass
class AlignedToken:
    """A token aligned to its source XML positions."""
    token: NLPToken
    spans: list  # list of TextSpan
    lb_elements: list  # list of lb Elements inside the word (for cross-line)
    hi_element: object  # parent <hi> if applicable
    is_cross_line: bool
    original_parts: list  # text fragments per line (e.g. ["Pro", "tection"])


def align_tokens(tokens, spans, offset_map, hyphen_joins):
    """
    Map NLP tokens to their source XML TextSpans.

    For each token, converts its dehyphenated char_start/char_end to
    raw_text positions via offset_map, then finds which TextSpans
    those positions fall within.

    Args:
        tokens: List of NLPToken from PyHellen.
        spans: List of TextSpan from extraction.
        offset_map: Position mapping from dehyphenation.
        hyphen_joins: List of HyphenJoin records.

    Returns:
        list[AlignedToken]: Tokens aligned to XML positions.
    """
    # Build a lookup: raw_text position -> span index
    span_lookup = _build_span_lookup(spans)

    # Build a set of raw positions that are hyphen joins
    hyphen_raw_positions = {hj.raw_offset_before for hj in hyphen_joins}

    aligned = []

    for token in tokens:
        # Map dehyphenated positions to raw positions
        raw_start = _dehy_to_raw(token.char_start, offset_map)
        raw_end = _dehy_to_raw(token.char_end - 1, offset_map) if token.char_end > token.char_start else raw_start

        # Find all spans covered by this token
        covered_span_indices = set()
        for raw_pos in range(raw_start, raw_end + 1):
            si = span_lookup.get(raw_pos)
            if si is not None:
                covered_span_indices.add(si)

        covered_spans = [spans[i] for i in sorted(covered_span_indices)]

        # Determine if cross-line
        unique_lines = {s.line_index for s in covered_spans if s.text}
        is_cross_line = len(unique_lines) > 1

        # Collect lb elements at join points
        lb_elements = []
        original_parts = []

        if is_cross_line and len(covered_spans) >= 2:
            sorted_indices = sorted(covered_span_indices)
            # Build parts from the original text of each line
            for idx_pos, si in enumerate(sorted_indices):
                span = spans[si]
                # Extract the portion of this span's text that belongs to this token
                part = _extract_part(span, raw_start, raw_end)
                # Strip hyphen character from the end of non-last parts
                if part and idx_pos < len(sorted_indices) - 1:
                    part = part.rstrip("¬").rstrip("-")
                if part:
                    original_parts.append(part)
                # Collect lb elements between parts (not the first one)
                if si > min(covered_span_indices):
                    lb_elements.append(span.lb_element)
        else:
            original_parts = [token.form]

        # Determine hi_element (use the first span's hi if all are the same)
        hi_element = None
        if covered_spans:
            hi_set = {id(s.hi_element) for s in covered_spans if s.hi_element is not None}
            if len(hi_set) == 1:
                hi_element = covered_spans[0].hi_element

        aligned.append(AlignedToken(
            token=token,
            spans=covered_spans,
            lb_elements=lb_elements,
            hi_element=hi_element,
            is_cross_line=is_cross_line,
            original_parts=original_parts,
        ))

    return aligned


def _build_span_lookup(spans):
    """Build a dict mapping raw_text position -> span index."""
    lookup = {}
    for i, span in enumerate(spans):
        for pos in range(span.offset_start, span.offset_end):
            lookup[pos] = i
    return lookup


def _dehy_to_raw(dehy_pos, offset_map):
    """Convert a dehyphenated text position to raw_text position."""
    if dehy_pos < 0:
        return 0
    if dehy_pos >= len(offset_map):
        return offset_map[-1] if offset_map else 0
    return offset_map[dehy_pos]


def _extract_part(span, raw_start, raw_end):
    """
    Extract the portion of a span's text that falls within raw_start..raw_end.
    """
    # Clamp to span boundaries
    part_start = max(raw_start, span.offset_start)
    part_end = min(raw_end + 1, span.offset_end)

    if part_start >= part_end:
        return ""

    # Convert to positions within span.text
    local_start = part_start - span.offset_start
    local_end = part_end - span.offset_start

    text = span.text[local_start:local_end]
    # Strip leading space (inter-line separator)
    return text.lstrip(" ")
