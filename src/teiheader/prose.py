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

The normalization paragraph reads MODERNIZE_SIMILARITY_MIN from the
configuration rather than restating it: the threshold and the sentence
describing it used to be free to drift apart (audit 2.13).
"""

from config import (
    ENRICHMENT_ENABLED,
    MODERNIZE_ENABLED,
    MODERNIZE_SIMILARITY_MIN,
    NER_ENABLED,
)

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
            '@resp="#ner-auto" and @cert (low < 0.6, mid 0.6\u20130.85, '
            "high >= 0.85). Identifiers were resolved against local "
            "authority files."
        ),
    },
}


# Description of the language-detection methodology, rendered as a <p>
# inside <profileDesc>/<langUsage>. Kept separate from EDITORIAL_DECLARATIONS
# because <langUsage> is not a valid child of <editorialDecl> in TEI P5.
LANG_USAGE_DESCRIPTION = (
    "Language detection uses Lingua (statistical n-gram model) at "
    "the container level (ab, note, fw). Mixed-language containers "
    "are segmented by Lingua's detect_multiple_languages_of, which "
    "identifies contiguous blocks via sliding-window character "
    "n-gram comparison. Segments classified as foreign are validated "
    "by rule-based heuristics (character sets, keywords, patterns): "
    "a segment is rejected if the primary-language heuristic score "
    "reaches 2, or if the target-language heuristic score is 0. "
    "Greek is detected reliably through Unicode character ranges; "
    "Latin relies on distinctive vocabulary not shared with French."
)

