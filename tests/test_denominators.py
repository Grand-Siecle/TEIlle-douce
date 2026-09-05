# -----------------------------------------------------------
# What a phase was given, counted before it can lose it.
#
# `containers_found` is the denominator every loss of a phase is measured
# against, and it was set AFTER the point where a dead service returns —
# so the one case it exists for, a service dying, still rendered `0 of 0
# containers`. Three rounds of review found the same sentence, because
# each fix moved the read and not the count.
#
# Counted first, at the top of the phase: a denominator that depends on
# the phase succeeding is not a denominator.
#
# Run: venv/bin/python -m pytest tests/test_denominators.py -q
# -----------------------------------------------------------
from lxml import etree

import teille_douce.enrichment.pipeline as pipeline
from teille_douce.tei import TEI


BODY = ("<TEI xmlns='http://www.tei-c.org/ns/1.0'><text><body>"
        "<ab><lb corresp='#a'/>une ligne</ab>"
        "<ab><lb corresp='#b'/>une autre</ab>"
        "</body></text></TEI>")


def test_enrichment_knows_what_it_was_given_before_the_service_answers(
        monkeypatch):
    """The probe returning False used to return all-zero stats, so the
    phase that lost two containers reported losing none of none."""
    monkeypatch.setattr(pipeline, "check_server", lambda: False)

    stats = pipeline.enrich_body(etree.fromstring(BODY))

    assert stats["server_unavailable"] is True
    assert stats["containers_found"] == 2


def test_modernization_knows_it_too(monkeypatch):
    """Its count lived inside the walk that only runs once the service
    has answered, so the same zero survived one fix longer. A service
    that answers nothing is the shape a dead VieuxParler takes here —
    the reachability probe is upstream, in the run."""
    tree = TEI.__new__(TEI)
    tree.root = etree.fromstring(BODY)
    tree.d = "D1"

    monkeypatch.setattr("teille_douce.modernize.modernize_texts",
                        lambda *args, **kwargs: None)

    stats = tree.modernize_body()

    assert stats["server_unavailable"] is True
    assert stats["containers_found"] == 2


def test_the_denominator_survives_an_exception_from_the_service(monkeypatch):
    tree = TEI.__new__(TEI)
    tree.root = etree.fromstring(BODY)
    tree.d = "D1"

    monkeypatch.setattr("teille_douce.modernize.check_api", lambda lang="fra": True)

    def explode(*args, **kwargs):
        raise RuntimeError("the socket died")

    monkeypatch.setattr("teille_douce.modernize.modernize_texts", explode)

    stats = tree.modernize_body()

    assert stats["server_unavailable"] is True
    assert stats["containers_found"] == 2


def test_a_document_with_no_container_says_zero_of_zero_honestly(monkeypatch):
    """The one case where `0 of 0` is the truth."""
    monkeypatch.setattr(pipeline, "check_server", lambda: False)
    empty = "<TEI xmlns='http://www.tei-c.org/ns/1.0'><text><body/></text></TEI>"

    stats = pipeline.enrich_body(etree.fromstring(empty))

    assert stats["containers_found"] == 0
