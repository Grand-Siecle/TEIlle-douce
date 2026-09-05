# -----------------------------------------------------------
# The keys the run reads must be the keys the phases write.
#
# `containers_total` was read in three places and written nowhere, so a
# service dying recorded `0 of 0 containers` — verbatim the failure
# CLAUDE.md forbids and the one `render_count` was written to make
# impossible. It stayed invisible because `whole_phases_lost()` counts
# document names and still looked right.
#
# Run: venv/bin/python -m pytest tests/test_loss_wiring.py -q
# -----------------------------------------------------------
from teille_douce.cli import run as run_module
from teille_douce.enrichment.pipeline import new_stats
from teille_douce.report.collector import Run
from teille_douce.report.counts import PhaseState
from teille_douce.report.record import Block, Code


def a_run():
    return Run(input_dir="OCR", output_dir="out", volumes=1, pages=10)


def enrich_stats(**counts):
    stats = new_stats()
    stats.update(counts)
    return stats


# =============================================================================
# A dead service says how much it refused
# =============================================================================

def test_a_lost_phase_is_measured_against_what_it_was_given():
    """`0 of 1 402 containers` is a phase that refused everything; `0 of
    0` is a document that had nothing. The whole report turns on the
    difference."""
    run = a_run()
    run.document_started("D1", pages=10)

    run_module._phase_lost(run, "D1", "enrich",
                           enrich_stats(containers_found=1402),
                           "containers", "PyHellen stopped answering")

    loss, = run.record.losses(Block.INCIDENT)
    assert loss.code is Code.PHASE_LOST
    assert loss.total == 1402


def test_the_panel_shows_the_denominator_of_a_lost_phase():
    run = a_run()
    run.document_started("D1", pages=10)

    run_module._phase_lost(run, "D1", "enrich",
                           enrich_stats(containers_found=1402),
                           "containers", "PyHellen stopped answering")

    line = next(l for l in run.panel().current.phases if l.name == "enrich")
    assert line.total == 1402


# =============================================================================
# Modernization writes different keys, and they must be read
# =============================================================================

def test_a_container_modernization_could_not_reach_is_recorded():
    """`_containers_failed` read the enrichment pipeline's split keys, so
    modernization — which only has `containers_failed` — recorded nothing
    at all. The console printed a warning; the record, the three blocks
    and --fail-on saw an empty run."""
    run = a_run()
    run.document_started("D1", pages=10)

    run_module._containers_failed(
        run, "D1", "modernize",
        {"containers_failed": 5, "containers_found": 100},
        "VieuxParler refused these containers")

    loss, = run.record.losses(Block.INCIDENT)
    assert loss.count == 5
    assert loss.total == 100


def test_the_split_causes_are_still_read_where_they_exist():
    run = a_run()
    run.document_started("D1", pages=10)

    run_module._containers_failed(
        run, "D1", "enrich",
        enrich_stats(containers_found=100, containers_failed=7,
                     containers_broken=2, containers_refused=5),
        "PyHellen")

    # Two lines, not one: they share a code and not a diagnosis.
    failed = [loss for loss in run.record.losses()
              if loss.code is Code.CONTAINER_FAILED]
    assert sum(loss.count for loss in failed) == 2 + 5
    assert len({loss.step for loss in failed}) == 2


def test_a_guard_refusing_is_block_two_not_block_three():
    run = a_run()
    run.document_started("D1", pages=10)

    run_module._containers_failed(
        run, "D1", "enrich",
        enrich_stats(containers_found=100, containers_failed=3,
                     containers_unanchored=3),
        "PyHellen")

    loss, = run.record.losses(Block.WITHHELD)
    assert loss.code is Code.CONTAINER_UNANCHORED
    assert run.record.losses(Block.INCIDENT) == ()


def test_two_causes_of_one_code_keep_their_own_diagnosis():
    """`2 broken + 90 refused` printed "92 containers — the pipeline
    raised on these". The count was right and the diagnosis was wrong."""
    run = a_run()
    run.document_started("D1", pages=10)

    run_module._containers_failed(
        run, "D1", "enrich",
        enrich_stats(containers_found=100, containers_failed=92,
                     containers_broken=2, containers_refused=90),
        "PyHellen")

    details = " ".join(loss.detail for loss in run.record.losses())
    assert "raised" in details and "refused" in details


def test_nothing_is_recorded_when_nothing_failed():
    run = a_run()
    run.document_started("D1", pages=10)

    run_module._containers_failed(run, "D1", "enrich",
                                  enrich_stats(containers_found=100), "x")

    assert run.record.losses() == ()


def test_a_lost_modernization_also_says_how_much_it_refused():
    """`containers_found` is written by the enrichment pipeline only, so
    modernization kept rendering `0 of 0 containers` — the fix reached
    one phase of the two."""
    run = a_run()
    run.document_started("D1", pages=10)

    run_module._phase_lost(run, "D1", "modernize",
                           {"containers_found": 900, "containers_failed": 0},
                           "containers", "VieuxParler stopped answering")

    loss, = run.record.losses(Block.INCIDENT)
    assert loss.total == 900


def test_the_containers_a_document_offers_are_counted_before_anything_runs():
    """The denominator has to come from the document, not from the walk
    that only happens once the service has answered."""
    from lxml import etree

    from teille_douce.body.builder import count_containers

    body = etree.fromstring(
        "<body><ab><lb corresp='#a'/>un</ab><ab><lb corresp='#b'/>deux</ab></body>")

    assert count_containers(body) == 2


# =============================================================================
# What the totals row counts
# =============================================================================

def test_a_lost_phase_is_not_written_as_zero_things_lost():
    """`incident 0` printed directly above three incident lines, over a
    summary reporting three. `Loss.count` is the amount GONE everywhere
    else — pages unusable, ids repaired, one failed volume — and
    `RunRecord.total` sums it on that reading; a lost phase stored what
    it had MANAGED, which for a dead service is always nought."""
    run = a_run()
    run.document_started("D1", pages=10)

    run_module._phase_lost(run, "D1", "enrich",
                           enrich_stats(containers_found=1402),
                           "containers", "PyHellen stopped answering")

    assert run.record.total(Block.INCIDENT) == 1402
    assert run.panel().incidents == 1402
    assert len(run.panel().incident_lines) == 1


def test_a_phase_that_lost_only_part_of_what_it_had_says_so():
    run = a_run()
    run.document_started("D1", pages=10)

    run.phase("D1", "enrich", PhaseState.LOST, done=1200, total=1402,
              unit="containers", reason="PyHellen stopped answering")

    loss, = run.record.losses(Block.INCIDENT)
    assert (loss.count, loss.total) == (202, 1402)


# =============================================================================
# A volume that read nothing is not a volume that wrote everything
# =============================================================================

def test_a_volume_whose_every_page_was_unusable_claims_none_of_them():
    """`_open_read or _open_pages` cannot tell "not measured" from
    "measured, and it was nought". A volume whose every ALTO is unusable
    does not raise, so it reported reading zero pages, fell through to
    the page count it was OFFERED, and the headline read "754 of 754
    pages written" one line above "pages unusable 754 of 754"."""
    run = Run(input_dir="OCR", output_dir="out", volumes=1, pages=754)
    run.document_started("D1", pages=754)
    run.pages_read("D1", 0)
    run.document_finished("D1", ok=True)

    assert run.panel().pages_written == 0


def test_a_volume_nobody_measured_still_counts_the_pages_it_was_given():
    """The other half: `pages_read` is not called on every path, and a
    volume that converted normally must not report nought."""
    run = Run(input_dir="OCR", output_dir="out", volumes=1, pages=754)
    run.document_started("D1", pages=754)
    run.document_finished("D1", ok=True)

    assert run.panel().pages_written == 754


# =============================================================================
# The third phase
# =============================================================================

def test_a_lost_ner_phase_is_an_incident_like_the_other_two():
    """NER touched the reporter nowhere at all: a run whose entity
    recognition died on every volume exited 0 under `--fail-on incident`,
    drew a green tick on the banner and printed "lost to an incident …
    nothing" over a corpus with no <standOff> in it.

    Against a denominator of one document, because that is the unit it
    works in — it runs over the whole tree in one step, so the honest
    measure of what a failure lost is the document."""
    run = a_run()
    run.document_started("D1", pages=10)

    run.phase("D1", "ner", PhaseState.LOST, done=0, total=1,
              unit="documents", reason="NER failed (CUDA out of memory)")

    loss, = run.record.losses(Block.INCIDENT)
    assert loss.code is Code.PHASE_LOST
    assert (loss.step, loss.count, loss.total) == ("ner", 1, 1)
    assert "CUDA out of memory" in loss.detail
