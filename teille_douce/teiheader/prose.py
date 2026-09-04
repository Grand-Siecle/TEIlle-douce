# -----------------------------------------------------------
# The prose the header states about its own making.
# -----------------------------------------------------------
"""
Editorial declarations written into every TEI header.

Roughly 120 lines of prose that lived in config.py among the paths and
the timeouts (audit 4.10). It is not configuration: it is the text the
edition publishes about how it was produced, and it belongs next to the
code that writes it. Each declaration is emitted as
<encodingDesc><editorialDecl><KEY><p>…</p></KEY> by
src/teiheader/default.py, and switching one off with `enabled: False`
removes it from the output.

Nothing here restates a value the code already holds: the normalization
paragraph reads MODERNIZE_SIMILARITY_MIN, the interpretation paragraph
derives its certainty bands from NER_CERT_THRESHOLDS, and the
language-detection paragraph lists TEXT_CONTAINERS. A sentence and the
setting it describes used to be free to drift apart (audit 2.13) — and
did: the prose announced a @cert value ("mid") no element ever carried.
"""

from teille_douce.config import (
    ENRICHMENT_ENABLED,
    MODERNIZE_ENABLED,
    MODERNIZE_SIMILARITY_MIN,
    NER_CERT_THRESHOLDS,
    NER_ENABLED,
)

from ..constants import TEXT_CONTAINERS


def _cert_bands(thresholds):
    """The @cert bands, written from the table that decides them.

    The keys ARE the emitted values and the floors ARE the boundaries
    teille_douce.enrichment.ner_align._confidence_to_cert applies (a score takes
    the label of the highest floor it reaches), so both are read from
    the table rather than typed out: "low < 0.6, medium 0.6-0.85,
    high >= 0.85".
    """
    bands = sorted(thresholds.items(), key=lambda kv: kv[1])
    parts = []
    for i, (label, floor) in enumerate(bands):
        ceiling = bands[i + 1][1] if i + 1 < len(bands) else None
        if ceiling is None:
            parts.append(f"{label} >= {floor:g}")
        elif i == 0:
            # The lowest band has no meaningful floor: everything under
            # the next one falls into it, including a score of 0.
            parts.append(f"{label} < {ceiling:g}")
        else:
            parts.append(f"{label} {floor:g}\u2013{ceiling:g}")
    return ", ".join(parts)

# =============================================================================
# EDITORIAL DECLARATIONS (encodingDesc/editorialDecl)
# =============================================================================

# Each entry produces a child element of <editorialDecl> in the TEI header.
# Only entries whose "enabled" key is True (or whose matching pipeline flag
# is True) are injected.  Set to None or remove an entry to skip it.
EDITORIAL_DECLARATIONS = {
    "normalization": {
        "enabled": MODERNIZE_ENABLED,
        "attrs": {"method": "markup"},
        "text": (
            "Original historical spelling is preserved in orig elements. "
            "Modernized spelling is provided in reg elements, generated "
            "automatically via a translation API (LSTM Fairseq/FreEM model). "
            "Lines whose modernized form diverges too far from the original "
            "(word-count ratio or character-level similarity after "
            f"normalization below {MODERNIZE_SIMILARITY_MIN}) are left unmodified."
        ),
    },
    "segmentation": {
        "enabled": ENRICHMENT_ENABLED,
        "attrs": {},
        "text": (
            "Linguistic annotation (tokenization, POS tagging, "
            "lemmatization, sentence segmentation) was produced "
            "automatically by the PyHellen NLP API. Tokens are "
            "encoded as w elements with @lemma, @pos and @msd "
            "attributes; punctuation as pc elements; sentence "
            "boundaries as s elements."
        ),
    },
    "interpretation": {
        "enabled": NER_ENABLED,
        "attrs": {},
        "text": (
            "Named entities were automatically detected using a hybrid "
            "NER pipeline. French text was processed with "
            "CamemBERT-classical-fr-ner on original orthography and "
            "GLiNER-multi-v2.1 on modernized text. Non-French text "
            "was processed with GLiNER only. Annotations carry "
            f'@resp="#ner-auto" and @cert ({_cert_bands(NER_CERT_THRESHOLDS)}). '
            "Identifiers were resolved against local authority files."
        ),
    },
}


# Description of the language-detection methodology, written as a <p>
# inside <encodingDesc>/<editorialDecl>/<interpretation> -- identifying a
# language is analytic information added to the transcription. It is kept
# out of EDITORIAL_DECLARATIONS because that table is keyed by element
# name and gated on a pipeline flag, and this paragraph shares
# <interpretation> with NER while running unconditionally.
#
# It lived in <langUsage> until finalize_langusage() was found to erase
# it there, and it cannot go back: <langUsage> takes paragraphs OR
# <language> elements, never both.
LANGUAGE_DETECTION_DESCRIPTION = (
    "Language detection uses Lingua (statistical n-gram model) at "
    f"the container level ({', '.join(TEXT_CONTAINERS)}). "
    "Mixed-language containers "
    "are segmented by Lingua's detect_multiple_languages_of, which "
    "identifies contiguous blocks via sliding-window character "
    "n-gram comparison. Segments classified as foreign are validated "
    "by rule-based heuristics (character sets, keywords, patterns): "
    "a segment is rejected if the primary-language heuristic score "
    "reaches 2, or if the target-language heuristic score is 0. "
    "Greek is detected reliably through Unicode character ranges; "
    "Latin relies on distinctive vocabulary not shared with French."
)

