# -----------------------------------------------------------
# Sentence segmentation based on punctuation rules.
# -----------------------------------------------------------
"""
Sentence segmenter (Phase 5).

Segments aligned tokens into sentences based on punctuation patterns.
Handles abbreviations, cross-container linking with @next/@prev.
"""

import uuid
from dataclasses import dataclass, field

from ..constants import UUID_NAMESPACE
from .aligner import AlignedToken


# Known abbreviations that end with period but don't end a sentence
ABBREVIATIONS = {
    "cap.", "lib.", "art.", "chap.", "tom.", "vol.", "fig.",
    "sic.", "etc.", "resp.", "al.", "op.", "cit.",
    "ibid.", "vid.", "conf.", "pag.", "fol.", "sect.",
}

# Terminal punctuation marks
TERMINAL_PUNCT = {".", "?", "!"}

# Colon triggers sentence break only if followed by uppercase
CONDITIONAL_PUNCT = {":"}


@dataclass
class Sentence:
    """A sentence containing aligned tokens."""
    xml_id: str
    tokens: list  # list of AlignedToken
    next_id: str | None = None
    prev_id: str | None = None


def segment_sentences(aligned_tokens, id_scope=""):
    """
    Segment aligned tokens into sentences.

    Rules:
    - Break after . ? ! if the next word-token starts with uppercase.
    - : triggers break only if followed by uppercase.
    - Ignore single-letter abbreviations (S., D.) and known abbreviations.

    Args:
        aligned_tokens: List of AlignedToken objects.
        id_scope (str): Deterministic scope for sentence xml:ids, typically
            the container's @corresp (audit 2.8) — sentence ids are derived
            from (scope, index) so the same input yields the same ids.

    Returns:
        list[Sentence]: Segmented sentences.
    """
    if not aligned_tokens:
        return []

    sentences = []
    current_tokens = []

    for i, at in enumerate(aligned_tokens):
        current_tokens.append(at)

        # Check if this token is terminal punctuation
        if at.token.is_punctuation and at.token.form in TERMINAL_PUNCT:
            # Check if it's an abbreviation
            if _is_abbreviation(aligned_tokens, i):
                continue

            # Check if next word-token starts with uppercase
            next_word = _find_next_word(aligned_tokens, i + 1)
            if next_word and next_word.token.form[0:1].isupper():
                sentences.append(_make_sentence(current_tokens, id_scope, len(sentences)))
                current_tokens = []

        elif at.token.is_punctuation and at.token.form in CONDITIONAL_PUNCT:
            next_word = _find_next_word(aligned_tokens, i + 1)
            if next_word and next_word.token.form[0:1].isupper():
                sentences.append(_make_sentence(current_tokens, id_scope, len(sentences)))
                current_tokens = []

    # Remaining tokens form the last sentence
    if current_tokens:
        sentences.append(_make_sentence(current_tokens, id_scope, len(sentences)))

    return sentences


def chain_cross_container(all_container_sentences):
    """
    Link sentences across container boundaries.

    If the last sentence of a container doesn't end with terminal
    punctuation, link it to the first sentence of the next container
    using @next/@prev attributes.

    Args:
        all_container_sentences: List of lists of Sentence objects,
                                  one list per container.
    """
    for i in range(len(all_container_sentences) - 1):
        current_sents = all_container_sentences[i]
        next_sents = all_container_sentences[i + 1]

        if not current_sents or not next_sents:
            continue

        last_sent = current_sents[-1]
        first_next = next_sents[0]

        # Check if last sentence ends with terminal punctuation
        last_token = _find_last_word_or_punct(last_sent.tokens)
        if last_token and last_token.token.form not in TERMINAL_PUNCT:
            last_sent.next_id = f"#{first_next.xml_id}"
            first_next.prev_id = f"#{last_sent.xml_id}"


def _make_sentence(tokens, id_scope, index):
    """Create a Sentence with a deterministic ID (audit 2.8): uuid5 over
    (container scope, sentence index) — same input, same ids, every run."""
    name = f"{id_scope}\x1f{index}"
    sid = f"s_{uuid.uuid5(UUID_NAMESPACE, name).hex[:12]}"
    return Sentence(xml_id=sid, tokens=list(tokens))


def _is_abbreviation(tokens, punct_index):
    """Check if a period is part of an abbreviation."""
    if punct_index < 1:
        return False

    prev = tokens[punct_index - 1]
    form = prev.token.form

    # Single-letter abbreviation (S., D., etc.)
    if len(form) == 1 and form.isalpha():
        return True

    # Known abbreviation (check form + ".")
    combined = form.lower() + "."
    if combined in ABBREVIATIONS:
        return True

    return False


def _find_next_word(tokens, start_idx):
    """Find the next non-punctuation token starting from start_idx."""
    for i in range(start_idx, len(tokens)):
        if not tokens[i].token.is_punctuation:
            return tokens[i]
    return None


def _find_last_word_or_punct(tokens):
    """Find the last token in the list."""
    for t in reversed(tokens):
        if t.token.form.strip():
            return t
    return None
