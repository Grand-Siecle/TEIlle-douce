# -----------------------------------------------------------
# What a run records about what it lost.
#
# A number without an address cannot be checked. Every loss carries a
# document, the step it happened in, and at least one typed locator — and
# the locator is never an internal index. "container 12" and "batch_start
# 640" are what the pipeline says today; neither of them is anything the
# reader can open.
#
# The bridge that makes it cheap: surface/@xml:id IS the ALTO file stem
# (elements.py is handed Path(filepath).stem as the folio), so one page id
# resolves to a file to open, an XPath that lands, and a IIIF region.
#
# Run: venv/bin/python -m pytest tests/test_record.py -q
# -----------------------------------------------------------
from pathlib import Path

import pytest

from teille_douce.report.record import (Block, Code, Kind, Locator, Loss,
                                        RunRecord)


# =============================================================================
# Locators: four ways to resolve one page
# =============================================================================

def test_a_page_locator_resolves_to_the_alto_file_it_came_from(tmp_path):
    """Against a real directory, not against a string. It composed
    `<ocr>/<doc>/<stem>.xml`, and a volume's pages live under
    `<doc>/content/data/doc_N/` — so the path it returned has never
    existed on this corpus, and the test asserting it never looked."""
    real = (tmp_path / "LIV0038_reconciled" / "content" / "data" / "doc_1")
    real.mkdir(parents=True)
    (real / "f284-259-0285.xml").write_text("<alto/>", encoding="utf-8")

    page = Locator.page("LIV0038_reconciled", "f284-259-0285")

    assert page.input_path(tmp_path) == real / "f284-259-0285.xml"


def test_a_page_locator_that_cannot_find_its_file_says_so(tmp_path):
    """A file can be gone between a run and the reading of its record,
    and a path that does not exist is worse than an admission."""
    assert Locator.page("LIV0038_reconciled", "f1").input_path(tmp_path) is None


def test_a_page_locator_resolves_to_an_xpath_that_lands():
    """Against the produced TEI, which is namespaced: `//surface` matched
    nothing in it, and an address that does not resolve costs the reader
    an afternoon believing the surface is missing."""
    from lxml import etree

    produced = etree.fromstring(
        "<TEI xmlns='http://www.tei-c.org/ns/1.0'><sourceDoc>"
        "<surface xml:id='f284-259-0285'/><surface xml:id='f2'/>"
        "</sourceDoc></TEI>")
    page = Locator.page("LIV0038_reconciled", "f284-259-0285")

    assert len(produced.xpath(page.xpath())) == 1


def test_a_page_locator_knows_it_is_the_most_precise_kind():
    assert Locator.page("D", "f1").kind is Kind.PAGE
    assert Locator.file(Path("OCR/Menestrier1682.zip")).kind is Kind.FILE
    assert Locator.document("D").kind is Kind.DOC


def test_a_document_locator_cannot_pretend_to_a_page():
    """Kind.DOC is the floor, and an admission: it says the pipeline knows
    which volume and nothing finer. Letting it answer input_path would
    turn that admission into a wrong path."""
    doc = Locator.document("LIV0038_reconciled")

    with pytest.raises(ValueError, match="no page"):
        doc.xpath()


def test_a_locator_renders_as_something_a_reader_can_act_on():
    assert Locator.page("D", "f284").render() == "D/f284"
    assert Locator.file(Path("OCR/x.zip")).render() == "OCR/x.zip"
    assert Locator.document("D").render() == "D"


# =============================================================================
# The catalogue of codes is closed, and every code has exactly one block
# =============================================================================

def test_every_code_belongs_to_exactly_one_block():
    """The three blocks are the whole taxonomy. A code in none of them
    would be a loss the summary cannot print; a code in two would let the
    same failure be counted twice."""
    for code in Code:
        assert isinstance(code.block, Block), code


def test_the_blocks_say_what_they_are_for():
    assert Code.PAGE_UNUSABLE.block is Block.SOURCE
    assert Code.ARCHIVE_CORRUPT.block is Block.SOURCE
    assert Code.ALTO_IDS_REPAIRED.block is Block.SOURCE

    assert Code.READING_REJECTED.block is Block.WITHHELD
    assert Code.ENTITY_FILTERED.block is Block.WITHHELD
    assert Code.CONTAINER_UNANCHORED.block is Block.WITHHELD

    assert Code.PHASE_LOST.block is Block.INCIDENT
    assert Code.CONTAINER_FAILED.block is Block.INCIDENT
    assert Code.DOCUMENT_FAILED.block is Block.INCIDENT
    assert Code.BREAKER_SKIPPED.block is Block.INCIDENT
    assert Code.BATCH_FAILED.block is Block.INCIDENT


def test_a_repaired_defect_is_not_an_incident():
    """Duplicate ALTO ids are the loudest number in this corpus and
    nothing is lost to them: a source defect is repaired and the repair
    written down. Filing it under incidents would make the biggest figure
    in the summary a false alarm."""
    assert Code.ALTO_IDS_REPAIRED.block is not Block.INCIDENT
    assert Code.ALTO_IDS_REPAIRED.repaired is True


def test_a_guard_doing_its_job_is_never_an_incident():
    """Block 2 must not count towards a failure: punishing the chain for
    rejecting a hallucination would punish it for being honest."""
    for code in Code:
        if code.block is Block.WITHHELD:
            assert code.needs_a_human is False, code
        if code.block is Block.INCIDENT:
            assert code.needs_a_human is True, code


# =============================================================================
# A loss without an address is not recordable
# =============================================================================

def test_a_loss_carries_a_document_a_step_and_a_locator():
    loss = Loss(Code.PAGE_UNUSABLE, "LIV0038_reconciled", "sourcedoc",
                Locator.page("LIV0038_reconciled", "f41"), count=1, total=754)

    assert loss.block is Block.SOURCE
    assert loss.locator.render() == "LIV0038_reconciled/f41"


def test_a_loss_with_no_locator_at_all_is_refused():
    """Kind.DOC is the floor, not the absence of a floor. A record that
    cannot even name its volume is an unverifiable number."""
    with pytest.raises(ValueError, match="locator"):
        Loss(Code.PAGE_UNUSABLE, "LIV0038_reconciled", "sourcedoc", None,
             count=1, total=754)


def test_a_loss_needs_the_denominator_the_renderer_will_ask_for():
    """Refused here rather than at render time: the call site knows the
    total, the renderer only knows it is missing."""
    with pytest.raises(ValueError, match="total"):
        Loss(Code.PAGE_UNUSABLE, "D", "sourcedoc", Locator.document("D"),
             count=1, total=None)


# =============================================================================
# The run record adds up
# =============================================================================

def test_the_record_totals_each_block_separately():
    record = RunRecord()
    record.add(Loss(Code.PAGE_UNUSABLE, "D1", "sourcedoc",
                    Locator.page("D1", "f41"), count=3, total=754))
    record.add(Loss(Code.READING_REJECTED, "D1", "modernize",
                    Locator.document("D1"), count=118, total=3402))
    record.add(Loss(Code.CONTAINER_FAILED, "D1", "enrich",
                    Locator.page("D1", "f88"), count=12, total=1402))

    assert record.total(Block.SOURCE) == 3
    assert record.total(Block.WITHHELD) == 118
    assert record.total(Block.INCIDENT) == 12


def test_an_empty_block_totals_zero_rather_than_disappearing():
    """A line that vanishes at zero cannot be told from a phase that was
    never checked, so the record answers for every block always."""
    record = RunRecord()

    assert record.total(Block.INCIDENT) == 0
    assert record.losses(Block.INCIDENT) == ()


def test_the_record_says_how_much_of_it_can_be_acted_on():
    """`located … 0 unlocated` prints even at zero: a blind spot becomes a
    visible line rather than an absence."""
    record = RunRecord()
    record.add(Loss(Code.PAGE_UNUSABLE, "D1", "sourcedoc",
                    Locator.page("D1", "f41"), count=2, total=10))
    record.add(Loss(Code.ARCHIVE_CORRUPT, "D2", "expand",
                    Locator.file(Path("OCR/x.zip")), count=1, total=1))
    record.add(Loss(Code.READING_REJECTED, "D3", "modernize",
                    Locator.document("D3"), count=7, total=20))

    assert record.located() == {"precise": 3, "document_only": 7}


def test_a_document_that_failed_is_findable_by_name():
    """`--retry-failed` needs the list, and so does the reader."""
    record = RunRecord()
    record.add(Loss(Code.DOCUMENT_FAILED, "LIV0044_reconciled", "step 12/15",
                    Locator.document("LIV0044_reconciled"), count=1, total=1,
                    detail="KeyError 'Date_edition'"))

    failed, = record.losses(Block.INCIDENT)
    assert failed.document == "LIV0044_reconciled"
    assert failed.step == "step 12/15"
    assert failed.detail == "KeyError 'Date_edition'"


# =============================================================================
# PyHellen dies at document 12: twelve calls and a render
# =============================================================================

def test_a_service_dying_mid_run_is_one_loss_per_document_after_it():
    """No service, no ALTO, no subprocess — the scenario is twelve calls.
    Each document written without enrichment is its own loss, because
    averaging them into a container count is exactly the convenient lie
    the project forbids."""
    record = RunRecord()
    for index in range(12, 15):
        document = f"LIV{index:04d}_reconciled"
        record.add(Loss(Code.PHASE_LOST, document, "enrich",
                        Locator.document(document), count=0, total=1402,
                        detail="PyHellen down since 19:41"))

    assert record.total(Block.INCIDENT) == 0, "no container was lost twice"
    assert record.whole_phases_lost() == 3
