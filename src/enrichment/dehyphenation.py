# -----------------------------------------------------------
# Dehyphenation: merging words split across lines.
# -----------------------------------------------------------
"""
Dehyphenation module (Phase 2).

Handles hyphenation marks (¬ and -) that split words across lines.
Produces a dehyphenated text and an offset map to trace positions
back to the original raw_text.
"""

from dataclasses import dataclass


@dataclass
class HyphenJoin:
    """Record of a hyphen join between two spans."""
    span_index: int           # index of the hyphenated span in spans list
    hyphen_char: str          # the hyphen character removed ("¬" or "-")
    raw_offset_before: int    # position of hyphen in raw_text
    raw_offset_after: int     # position of the joined text start in raw_text


def dehyphenate(raw_text, spans):
    """
    Remove hyphenation marks and join split words.

    For each span ending with ¬ or -:
    1. Remove the hyphen character
    2. Remove the space separator before the next span
    3. Build an offset_map from dehyphenated positions to raw_text positions

    Args:
        raw_text: The concatenated text from extraction.
        spans: List of TextSpan objects from extraction.

    Returns:
        tuple: (dehyphenated_text, offset_map, hyphen_joins)
            - dehyphenated_text: Text with hyphens removed and words joined.
            - offset_map: List where offset_map[i] gives the raw_text position
              for dehyphenated_text position i.
            - hyphen_joins: List of HyphenJoin records.
    """
    # Work character by character, building the dehyphenated text
    # and the offset map simultaneously

    # First, identify positions to skip in raw_text
    skip_positions = set()
    hyphen_joins = []

    for i, span in enumerate(spans):
        if not span.has_hyphen and not span.text:
            continue

        if span.has_hyphen and i + 1 < len(spans):
            text = span.text.rstrip()
            # Find the hyphen position in raw_text
            # The hyphen is the last non-space character of this span
            if text.endswith("¬"):
                hyphen_raw_pos = span.offset_start + len(text) - 1
                hyphen_char = "¬"
            elif text.endswith("-"):
                hyphen_raw_pos = span.offset_start + len(text) - 1
                hyphen_char = "-"
            else:
                continue

            # Mark the hyphen for removal
            skip_positions.add(hyphen_raw_pos)

            # Also mark any trailing whitespace after the hyphen in this span
            for pos in range(hyphen_raw_pos + 1, span.offset_end):
                if raw_text[pos] if pos < len(raw_text) else "":
                    skip_positions.add(pos)

            # Mark the leading space of the next span (the " " separator)
            next_span = spans[i + 1]
            if next_span.text.startswith(" "):
                skip_positions.add(next_span.offset_start)

            hyphen_joins.append(HyphenJoin(
                span_index=i,
                hyphen_char=hyphen_char,
                raw_offset_before=hyphen_raw_pos,
                raw_offset_after=next_span.offset_start,
            ))

    # Build dehyphenated text and offset map
    dehyphenated = []
    offset_map = []

    for raw_pos, ch in enumerate(raw_text):
        if raw_pos in skip_positions:
            continue
        dehyphenated.append(ch)
        offset_map.append(raw_pos)

    dehyphenated_text = "".join(dehyphenated)
    return dehyphenated_text, offset_map, hyphen_joins
