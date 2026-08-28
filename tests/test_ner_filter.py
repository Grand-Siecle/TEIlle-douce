# Run: venv/bin/python -m pytest tests/test_ner_filter.py -q
#
# Targets: src/enrichment/ner_filter.py exclusively (the three-pass NER
# noise-filtering layer: span-level text checks, POS-based checks, and
# entity-level dedup/pruning). The other enrichment modules (ner_detect,
# ner_resolve, ner_align) are covered by sibling test files running in
# parallel -- this file only *imports* their dataclasses (NERSpan,
# AlignedEntity, ResolvedEntity) to build realistic fixtures; it never
# instantiates a NER model (no HuggingFace downloads).
#
# Focus: pin down exactly where each rejection threshold sits, testing
# both sides of the boundary, rather than maximizing test count. Several
# thresholds turned out to be either redundant or effectively dead code
# once traced against the surrounding checks -- see the comments next to
# the relevant tests below.
import pytest
from lxml import etree

from src.enrichment.ner_detect import NERSpan
from src.enrichment.ner_align import AlignedEntity
from src.enrichment.ner_resolve import ResolvedEntity
from src.enrichment.ner_filter import (
    _check_span,
    _fuzzy_merge_group,
    extract_title_from_tei,
    filter_aligned_by_pos,
    filter_resolved_entities,
    filter_spans,
    fix_canonical_names,
    fuzzy_merge_entities,
)

NS = "http://www.tei-c.org/ns/1.0"


def qlocal(el):
    """Namespace-agnostic local tag name (repo convention, doc. §4.2)."""
    return etree.QName(el).localname


# ── Fixture builders ────────────────────────────────────────────────

def make_span(text, entity_type="person", confidence=0.9, model="camembert",
              label=None, start=0, end=None):
    return NERSpan(
        start_char=start,
        end_char=end if end is not None else start + len(text),
        text=text,
        label=label or entity_type,
        entity_type=entity_type,
        confidence=confidence,
        model=model,
    )


def make_w(pos, lemma, text=None):
    """A real lxml <w> element carrying @pos/@lemma, as produced by the
    linguistic enrichment phase (bare tag -- tags nus et namespacés
    coexist in this pipeline, §4.2)."""
    w = etree.Element("w")
    w.set("pos", pos)
    w.set("lemma", lemma)
    w.text = text if text is not None else lemma
    return w


def make_aligned(entity_type, text, w_pos_lemma, confidence=0.9, model="camembert"):
    """w_pos_lemma: list of (pos, lemma) tuples -> one <w> each."""
    ws = [make_w(pos, lemma) for pos, lemma in w_pos_lemma]
    return AlignedEntity(
        entity_type=entity_type, text=text, confidence=confidence,
        model=model, w_elements=ws,
    )


def make_resolved(entity_type, canonical_name, mentions=None, xml_id="pers-x",
                   local_match=None):
    return ResolvedEntity(
        entity_type=entity_type,
        canonical_name=canonical_name,
        xml_id=xml_id,
        mentions=mentions if mentions is not None else [],
        local_match=local_match,
    )


def make_mention(confidence, entity_type="person", text="x"):
    """A minimal AlignedEntity used purely as a mention (only .confidence
    is read by filter_resolved_entities)."""
    return AlignedEntity(entity_type=entity_type, text=text, confidence=confidence,
                          model="camembert", w_elements=[])


# =====================================================================
# 1. _check_span / filter_spans
# =====================================================================

class TestCheckSpanLength:
    """MIN_ENTITY_LENGTH (2) is checked first, but MIN_NAMED_LENGTH (3) is
    checked right after and applies unconditionally to EVERY entity type
    (the code has no `if is_named` guard there, despite the name). Since
    norm length can never exceed raw text length, any span short enough
    to trip the length-2 check is also short enough to trip the length-3
    check. So MIN_ENTITY_LENGTH's own threshold is unreachable as an
    independent boundary: the real effective minimum entity length,
    applied to every type, is 3."""

    def test_single_char_rejected_too_short(self):
        span = make_span("A", entity_type="date")
        assert _check_span(span, None, set()) == "too_short"

    def test_two_chars_rejected_too_short_named_not_too_short(self):
        # len(text) == 2 clears MIN_ENTITY_LENGTH (2) but norm len == 2
        # still trips MIN_NAMED_LENGTH (3) -- and does so for a non-named
        # type ("date"), proving check 3 is not limited to PER/LOC/ORG.
        span = make_span("Ab", entity_type="date")
        assert _check_span(span, None, set()) == "too_short_named"

    def test_three_chars_accepted_at_the_real_boundary(self):
        span = make_span("Abc", entity_type="date")
        assert _check_span(span, None, set()) is None


class TestCheckSpanPunctAndDigits:

    def test_bad_punct_rejected(self):
        span = make_span("Jean,Pierre", entity_type="person")
        assert _check_span(span, None, set()) == "bad_punct"

    def test_hyphen_not_in_bad_punct_set_accepted(self):
        # Hyphen is deliberately absent from _BAD_PUNCT -- hyphenated
        # names must survive.
        span = make_span("Jean-Pierre", entity_type="person")
        assert _check_span(span, None, set()) is None

    def test_digits_rejected(self):
        span = make_span("Jean3", entity_type="person")
        assert _check_span(span, None, set()) == "has_digits"

    def test_no_digits_accepted(self):
        span = make_span("Jean", entity_type="person")
        assert _check_span(span, None, set()) is None


class TestCheckSpanGreek:

    def test_greek_verb_ending_rejected(self):
        # single Greek word ending in "ειν" (infinitive marker)
        span = make_span("λεγειν", entity_type="person")
        assert _check_span(span, None, set()) == "greek_verb"

    def test_greek_short_fragment_rejected_at_four_chars(self):
        # normalized length 4 (<=4), no verb ending -> greek_short
        span = make_span("θεοι", entity_type="person")
        assert _check_span(span, None, set()) == "greek_short"

    def test_greek_five_chars_no_verb_ending_accepted(self):
        # one character above the greek_short boundary, and its ending
        # ("ις") is not in _GREEK_VERB_ENDINGS either -> accepted
        span = make_span("θεοις", entity_type="person")
        assert _check_span(span, None, set()) is None

    def test_greek_check_only_applies_to_person_type(self):
        # Same short Greek fragment, but entity_type != "person" -> the
        # whole Greek block is skipped, so it is NOT rejected here.
        span = make_span("θεοι", entity_type="place")
        assert _check_span(span, None, set()) is None


class TestCheckSpanAllCaps:

    def test_allcaps_low_confidence_rejected(self):
        span = make_span("PARLEMENT", entity_type="organization", confidence=0.5)
        assert _check_span(span, None, set()) == "allcaps_low_conf"

    def test_allcaps_confidence_at_threshold_070_accepted(self):
        # condition is `confidence < 0.70`; exactly 0.70 does not reject
        span = make_span("PARLEMENT", entity_type="organization", confidence=0.70)
        assert _check_span(span, None, set()) is None

    def test_allcaps_just_below_070_rejected(self):
        span = make_span("PARLEMENT", entity_type="organization", confidence=0.69)
        assert _check_span(span, None, set()) == "allcaps_low_conf"

    def test_allcaps_six_chars_below_length_gate_accepted(self):
        # rule requires len(text) >= 7; six-char all-caps low-confidence
        # text is not touched by this rule at all
        span = make_span("PARLEM", entity_type="organization", confidence=0.1)
        assert _check_span(span, None, set()) is None

    def test_allcaps_seven_chars_at_length_gate_rejected(self):
        span = make_span("PARLEME", entity_type="organization", confidence=0.1)
        assert _check_span(span, None, set()) == "allcaps_low_conf"


class TestCheckSpanTitleMatch:

    def test_title_exact_match_rejected(self):
        span = make_span("Lyon", entity_type="place")
        norm_title = "lyon"
        assert _check_span(span, norm_title, {"lyon"}) == "title_exact"

    def test_title_exact_below_length_gate_of_four_accepted(self):
        # the whole title-match block requires len(norm) >= 4; a 3-char
        # exact match to the title slips through ungated
        span = make_span("Foa", entity_type="place")
        norm_title = "foa"
        assert _check_span(span, norm_title, {"foa"}) is None

    def test_title_word_rejected_at_five_chars(self):
        norm_title = "vies des hommes illustres"
        title_words = set(norm_title.split())
        span = make_span("Illustres", entity_type="organization")
        assert _check_span(span, norm_title, title_words) == "title_word"

    def test_title_word_four_chars_below_gate_accepted(self):
        # "vies" (len 4) is a real content word of the title, but the
        # title_word rule only fires for len(norm) >= 5 -> not rejected,
        # and it isn't the *whole* title either, so title_exact doesn't
        # fire -- span passes through untouched.
        norm_title = "vies des hommes illustres"
        title_words = set(norm_title.split())
        span = make_span("Vies", entity_type="person")
        assert _check_span(span, norm_title, title_words) is None

    def test_title_match_does_not_apply_to_non_named_types(self):
        norm_title = "lyon"
        span = make_span("Lyon", entity_type="date")
        assert _check_span(span, norm_title, {"lyon"}) is None


class TestFilterSpans:

    def test_empty_list_returns_empty(self):
        assert filter_spans([]) == []

    def test_filters_and_preserves_order_of_survivors(self):
        spans = [
            make_span("Amyot", entity_type="person"),   # kept
            make_span("A", entity_type="person"),        # too_short
            make_span("Jean3", entity_type="person"),    # has_digits
            make_span("Rabelais", entity_type="person"), # kept
        ]
        result = filter_spans(spans)
        assert [s.text for s in result] == ["Amyot", "Rabelais"]

    def test_title_is_normalized_and_threaded_through(self):
        spans = [make_span("Sorbonne", entity_type="organization")]
        result = filter_spans(spans, title="  SORBONNE  ")
        assert result == []


# =====================================================================
# 2. extract_title_from_tei
# =====================================================================

class TestExtractTitleFromTei:

    def test_extracts_title_with_namespaced_tags(self):
        root = etree.fromstring(
            f'<TEI xmlns="{NS}"><teiHeader><fileDesc><titleStmt>'
            f"<title>Les Essais</title></titleStmt></fileDesc></teiHeader></TEI>"
        )
        assert extract_title_from_tei(root) == "Les Essais"

    def test_extracts_title_with_bare_tags(self):
        # tags nus et namespacés coexistent (§4.2) -- extract_title_from_tei
        # goes through qlocal-equivalent local_tag(), so bare tags work too.
        root = etree.fromstring(
            "<TEI><teiHeader><fileDesc><titleStmt>"
            "<title>Gargantua</title></titleStmt></fileDesc></teiHeader></TEI>"
        )
        assert extract_title_from_tei(root) == "Gargantua"
        assert qlocal(root) == "TEI"

    def test_title_with_nested_markup_joins_all_text(self):
        root = etree.fromstring(
            "<TEI><teiHeader><fileDesc><titleStmt>"
            "<title>La <hi>Vraye</hi> Histoire</title>"
            "</titleStmt></fileDesc></teiHeader></TEI>"
        )
        assert extract_title_from_tei(root) == "La Vraye Histoire"

    def test_titlestmt_without_title_returns_empty_string_no_exception(self):
        root = etree.fromstring(
            "<TEI><teiHeader><fileDesc><titleStmt>"
            "<author>Anonyme</author>"
            "</titleStmt></fileDesc></teiHeader></TEI>"
        )
        assert extract_title_from_tei(root) == ""

    def test_no_titlestmt_at_all_returns_empty_string_no_exception(self):
        root = etree.fromstring("<TEI><text><body/></text></TEI>")
        assert extract_title_from_tei(root) == ""


# =====================================================================
# 3. filter_aligned_by_pos
# =====================================================================

class TestFilterAlignedByPos:

    def test_empty_list_returns_empty(self):
        assert filter_aligned_by_pos([]) == []

    def test_entity_without_w_elements_is_kept_untouched(self):
        ent = AlignedEntity(entity_type="person", text="Raw", confidence=0.5,
                             model="gliner", w_elements=[])
        assert filter_aligned_by_pos([ent]) == [ent]

    def test_rule0_all_foreign_lemmas_rejected(self):
        ent = make_aligned("person", "quelque chose",
                            [("NOMcom", "@latin"), ("ADJqua", "@latin")])
        assert filter_aligned_by_pos([ent]) == []

    def test_rule0_partial_foreign_lemmas_not_rejected_by_this_rule(self):
        # only ALL-foreign triggers rule 0; one real lemma survives it
        # (though it may still be rejected by a later rule)
        ent = make_aligned("person", "Jean latin",
                            [("NOMpro", "jean"), ("NOMcom", "@latin")])
        assert filter_aligned_by_pos([ent]) == [ent]

    def test_rule1_all_words_non_entity_pos_rejected(self):
        # "le grand" : DET + ADJ, both non-entity POS, no NOMpro/NOMcom
        # involved -- a place/org name can never be built from these.
        ent = make_aligned("place", "le grand",
                            [("DETdef", "le"), ("ADJqua", "grand")])
        assert filter_aligned_by_pos([ent]) == []

    def test_rule1_lasla_single_letter_tag_rejected(self):
        # LASLA (Latin) single-letter tagset: "r" = preposition, a
        # non-entity POS via the dedicated _LATIN_NON_ENTITY set rather
        # than the PyHellen-style prefix check.
        ent = make_aligned("place", "de", [("r", "de")])
        assert filter_aligned_by_pos([ent]) == []

    def test_rule1_upos_fallback_tag_not_caught_by_prefix_rejected(self):
        # "ADP" (UPOS adposition) is not matched by the PyHellen prefix
        # list (VER/DET/PRE/PRO/ADJ/ADV/CON/INJ/PON all differ from
        # "ADP" past the first two letters) -- it only falls through the
        # dedicated _NON_ENTITY_UPOS fallback set.
        ent = make_aligned("place", "de", [("ADP", "de")])
        assert filter_aligned_by_pos([ent]) == []

    def test_rule2_person_without_proper_noun_rejected(self):
        # "le philosophe" : DET + common noun, no NOMpro anywhere
        ent = make_aligned("person", "le philosophe",
                            [("DETdef", "le"), ("NOMcom", "philosophe")])
        assert filter_aligned_by_pos([ent]) == []

    def test_rule2_does_not_apply_to_non_person_types(self):
        # same DET+NOUN shape, but as a place: rule 1 doesn't reject it
        # (NOMcom is not a non-entity POS) and rule 2 is person-only.
        ent = make_aligned("place", "le hameau",
                            [("DETdef", "le"), ("NOMcom", "hameau")])
        assert filter_aligned_by_pos([ent]) == [ent]

    def test_rule3_false_propn_latin_lemma_rejected(self):
        # tagged as a proper noun (passes rule 2) but the lemma is a
        # known false-PROPN Latin common noun
        ent = make_aligned("person", "Deus", [("NOMpro", "deus")])
        assert filter_aligned_by_pos([ent]) == []

    def test_rule3_real_proper_noun_not_in_false_propn_kept(self):
        ent = make_aligned("person", "Amyot", [("NOMpro", "amyot")])
        assert filter_aligned_by_pos([ent]) == [ent]

    def test_single_word_adjective_rejected(self):
        # "adjectif seul" from the task table. Note: this text is single
        # word + ADJqua, which is already caught by rule 1 (ADJ is a
        # non-entity POS, and a lone word makes "all non-entity" trivially
        # true) before the code ever reaches its own dedicated rule 4
        # ADJ-check -- both paths agree on the observable outcome
        # (rejected), which is all a black-box test can pin down.
        ent = make_aligned("place", "ancien", [("ADJqua", "ancien")])
        assert filter_aligned_by_pos([ent]) == []

    def test_two_word_proper_name_kept(self):
        ent = make_aligned("person", "Jean Dupont",
                            [("NOMpro", "jean"), ("NOMpro", "dupont")])
        assert filter_aligned_by_pos([ent]) == [ent]

    def test_unknown_pos_person_is_not_rejected_by_rule2(self):
        # has_unknown True satisfies rule 2's "or has_unknown" escape hatch
        ent = make_aligned("person", "Xyzzy", [("UNK", "xyzzy")])
        assert filter_aligned_by_pos([ent]) == [ent]

    def test_mixed_kept_and_rejected_preserves_survivors(self):
        good = make_aligned("person", "Jean Dupont",
                             [("NOMpro", "jean"), ("NOMpro", "dupont")])
        bad = make_aligned("person", "le philosophe",
                            [("DETdef", "le"), ("NOMcom", "philosophe")])
        assert filter_aligned_by_pos([good, bad]) == [good]


# =====================================================================
# 4. fix_canonical_names
# =====================================================================

class TestFixCanonicalNames:

    def test_two_identical_words_collapsed(self):
        ent = make_resolved("person", "bonus bonus")
        fix_canonical_names([ent])
        assert ent.canonical_name == "bonus"

    def test_three_identical_words_collapsed(self):
        ent = make_resolved("person", "bonus bonus bonus")
        fix_canonical_names([ent])
        assert ent.canonical_name == "bonus"

    def test_longer_repeated_two_word_block_collapsed(self):
        ent = make_resolved("person", "abc def abc def")
        fix_canonical_names([ent])
        assert ent.canonical_name == "abc def"

    def test_single_word_untouched(self):
        ent = make_resolved("person", "bonus")
        fix_canonical_names([ent])
        assert ent.canonical_name == "bonus"

    def test_five_distinct_words_no_divisor_pattern_untouched(self):
        # n=5 words, all distinct: chunk=1 fails the identity check,
        # chunk=2 doesn't evenly divide 5 (the "n % chunk != 0" skip
        # branch), and the loop range never reaches chunk=5 itself --
        # no repeated block is found, name is left alone.
        ent = make_resolved("person", "abc def ghi jkl mno")
        fix_canonical_names([ent])
        assert ent.canonical_name == "abc def ghi jkl mno"

    def test_normal_two_word_name_not_falsely_collapsed(self):
        ent = make_resolved("person", "Jean Dupont")
        fix_canonical_names([ent])
        assert ent.canonical_name == "Jean Dupont"

    def test_operates_in_place_over_a_list(self):
        e1 = make_resolved("person", "bonus bonus")
        e2 = make_resolved("person", "Jean Dupont")
        entities = [e1, e2]
        fix_canonical_names(entities)
        assert entities[0].canonical_name == "bonus"
        assert entities[1].canonical_name == "Jean Dupont"


# =====================================================================
# 5. filter_resolved_entities
# =====================================================================

class TestFilterResolvedEntities:

    def test_single_mention_low_confidence_pruned(self):
        ent = make_resolved("person", "Ghost", mentions=[make_mention(0.3)])
        assert filter_resolved_entities([ent]) == []

    def test_single_mention_confidence_at_threshold_055_kept(self):
        # condition is `best < min_confidence_single`; == 0.55 is kept
        ent = make_resolved("person", "Edge", mentions=[make_mention(0.55)])
        assert filter_resolved_entities([ent]) == [ent]

    def test_single_mention_just_below_threshold_pruned(self):
        ent = make_resolved("person", "Edge", mentions=[make_mention(0.54)])
        assert filter_resolved_entities([ent]) == []

    def test_multi_mention_kept_regardless_of_low_confidence(self):
        # repeated detection is itself a signal -- the confidence floor
        # only applies to single-mention entities
        ent = make_resolved("person", "Repeated",
                             mentions=[make_mention(0.1), make_mention(0.2)])
        assert filter_resolved_entities([ent]) == [ent]

    def test_custom_threshold_is_honoured(self):
        ent = make_resolved("person", "Strict", mentions=[make_mention(0.6)])
        assert filter_resolved_entities([ent], min_confidence_single=0.65) == []
        assert filter_resolved_entities([ent], min_confidence_single=0.5) == [ent]


# =====================================================================
# 6. fuzzy_merge_entities / _fuzzy_merge_group
# =====================================================================
#
# Threshold values pinned down (default similarity_threshold=0.78,
# min_name_length=4), measured with difflib.SequenceMatcher directly:
#   ratio("montaigne", "montegne") == 0.8235  -> above threshold, merges
#   ratio("erasme",    "erasmus")  == 0.7692  -> below threshold, no merge
# These are exact SequenceMatcher.ratio() values on the accent-stripped,
# lower-cased forms (the same normalization fuzzy_merge_entities applies
# internally via _normalize). Assertions below target only the merge
# *outcome*, never comparison counts or internals, so they hold unchanged
# across the O(n^2) -> quick_ratio()-prefiltered optimization planned in
# docs/rapport_audit.md §3.8.

class TestFuzzyMergeEntities:

    def test_zero_or_one_entity_returned_unchanged(self):
        assert fuzzy_merge_entities([]) == []
        ent = make_resolved("person", "Solo")
        assert fuzzy_merge_entities([ent]) == [ent]

    def test_variants_above_threshold_are_merged(self):
        a = make_resolved("person", "Montaigne", mentions=[make_mention(0.9)],
                           xml_id="pers-a")
        b = make_resolved("person", "Montegne", mentions=[make_mention(0.8)],
                           xml_id="pers-b")
        result = fuzzy_merge_entities([a, b])
        assert len(result) == 1
        # mention counts are tied (1 each); representative selection
        # (max by mention count) breaks the tie in favour of the first
        # entity encountered -- see test_representative_is_the_entity_
        # with_more_mentions below for the non-tied case.
        assert result[0].canonical_name == "Montaigne"
        assert len(result[0].mentions) == 2

    def test_variants_just_below_threshold_are_not_merged(self):
        a = make_resolved("person", "Erasme", mentions=[make_mention(0.9)])
        b = make_resolved("person", "Erasmus", mentions=[make_mention(0.8)])
        result = fuzzy_merge_entities([a, b])
        assert len(result) == 2
        names = {e.canonical_name for e in result}
        assert names == {"Erasme", "Erasmus"}

    def test_different_types_never_merged_even_if_identical(self):
        a = make_resolved("person", "Montaigne", mentions=[make_mention(0.9)])
        b = make_resolved("place", "Montaigne", mentions=[make_mention(0.9)])
        result = fuzzy_merge_entities([a, b])
        assert len(result) == 2

    def test_representative_is_the_entity_with_more_mentions(self):
        few = make_resolved("person", "Montegne",
                             mentions=[make_mention(0.5)], xml_id="pers-few")
        many = make_resolved("person", "Montaigne",
                              mentions=[make_mention(0.9), make_mention(0.9),
                                        make_mention(0.9)], xml_id="pers-many")
        result = fuzzy_merge_entities([few, many])
        assert len(result) == 1
        assert result[0].xml_id == "pers-many"
        assert result[0].canonical_name == "Montaigne"
        assert len(result[0].mentions) == 4  # 3 + 1, absorbed

    def test_local_match_propagates_from_absorbed_entity_when_missing(self):
        winner = make_resolved("person", "Montaigne",
                                mentions=[make_mention(0.9), make_mention(0.9)],
                                local_match=None)
        loser = make_resolved("person", "Montegne",
                               mentions=[make_mention(0.5)],
                               local_match="PERS0042")
        result = fuzzy_merge_entities([winner, loser])
        assert len(result) == 1
        assert result[0].local_match == "PERS0042"

    def test_local_match_not_overwritten_when_winner_already_has_one(self):
        winner = make_resolved("person", "Montaigne",
                                mentions=[make_mention(0.9), make_mention(0.9)],
                                local_match="PERS0001")
        loser = make_resolved("person", "Montegne",
                               mentions=[make_mention(0.5)],
                               local_match="PERS0042")
        result = fuzzy_merge_entities([winner, loser])
        assert result[0].local_match == "PERS0001"

    def test_names_shorter_than_min_name_length_never_merged(self):
        # both normalized names have length 2 (< min_name_length=4);
        # they are skipped from fuzzy comparison entirely, even though
        # they are character-for-character identical.
        a = make_resolved("person", "Jo", mentions=[make_mention(0.9)])
        b = make_resolved("person", "Jo", mentions=[make_mention(0.9)])
        result = fuzzy_merge_entities([a, b])
        assert len(result) == 2

    def test_names_at_min_name_length_boundary_do_merge(self):
        # normalized length exactly 4 (== min_name_length): eligible,
        # and identical strings trivially clear the ratio threshold.
        a = make_resolved("person", "abcd", mentions=[make_mention(0.9)])
        b = make_resolved("person", "abcd", mentions=[make_mention(0.9)])
        result = fuzzy_merge_entities([a, b])
        assert len(result) == 1

    def test_custom_threshold_widens_or_narrows_merging(self):
        a = make_resolved("person", "Erasme", mentions=[make_mention(0.9)])
        b = make_resolved("person", "Erasmus", mentions=[make_mention(0.8)])
        # ratio is 0.7692: not merged at default 0.78, merged if we lower
        # the bar just below the measured ratio.
        assert len(fuzzy_merge_entities([a, b], similarity_threshold=0.76)) == 1
        assert len(fuzzy_merge_entities([a, b], similarity_threshold=0.78)) == 2

    def test_three_way_transitive_merge_via_union_find(self):
        # a~b and b~c both clear the threshold even if a~c might not;
        # union-find should still merge all three into one group.
        a = make_resolved("person", "Tertulus", mentions=[make_mention(0.9)],
                           xml_id="a")
        b = make_resolved("person", "Tertullus", mentions=[make_mention(0.9)],
                           xml_id="b")
        c = make_resolved("person", "Tertulius", mentions=[make_mention(0.9)],
                           xml_id="c")
        result = fuzzy_merge_entities([a, b, c])
        assert len(result) == 1
        assert len(result[0].mentions) == 3


class TestFuzzyMergeGroupDirect:
    """A couple of tests calling the internal helper directly, since it
    is explicitly listed in the API surface for this file."""

    def test_empty_and_singleton_groups_returned_unchanged(self):
        assert _fuzzy_merge_group([], 0.78, 4) == []
        ent = make_resolved("person", "Solo")
        assert _fuzzy_merge_group([ent], 0.78, 4) == [ent]

    def test_no_similar_pairs_returns_all_entities(self):
        a = make_resolved("person", "Petrus", mentions=[make_mention(0.9)])
        b = make_resolved("person", "Rabelais", mentions=[make_mention(0.9)])
        result = _fuzzy_merge_group([a, b], 0.78, 4)
        assert len(result) == 2

    def test_short_entity_between_two_long_ones_is_skipped_not_merged(self):
        # "Jo" (norm length 2) sits at index 1, between two longer,
        # mutually similar names -- it must be skipped by the inner-loop
        # min_length guard (not the outer one, already covered above)
        # while the two long ones still find each other.
        a = make_resolved("person", "Montaigne", mentions=[make_mention(0.9)])
        b = make_resolved("person", "Jo", mentions=[make_mention(0.9)])
        c = make_resolved("person", "Montegne", mentions=[make_mention(0.9)])
        result = _fuzzy_merge_group([a, b, c], 0.78, 4)
        assert len(result) == 2
        names = {e.canonical_name for e in result}
        assert "Jo" in names
        merged = [e for e in result if e.canonical_name != "Jo"][0]
        assert len(merged.mentions) == 2
