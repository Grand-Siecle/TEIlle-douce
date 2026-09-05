# -----------------------------------------------------------
# A modernization batch that fails must not look like nothing to do.
#
# Today a failed batch is swallowed by a `continue` and a DEBUG line, so
# sixty-four lines that never reached VieuxParler are indistinguishable
# from sixty-four lines that were already modern. One of those is a loss
# and the other is a success, and the summary has to tell them apart.
#
# Run: venv/bin/python -m pytest tests/test_modernize_losses.py -q
# -----------------------------------------------------------
import teille_douce.modernize as modernize


def test_a_failed_batch_is_counted_and_says_how_many_lines_it_took(monkeypatch):
    async def refuse(client, base_url, batch_texts, batch_size=None):
        raise RuntimeError("VieuxParler said no")

    monkeypatch.setattr(modernize, "_send_batch", refuse)
    losses = {}

    result = modernize.modernize_texts(["une ligne", "une autre"],
                                       losses=losses)

    assert result is None
    assert losses["batches_failed"] == 1
    assert losses["lines_lost"] == 2


def test_a_batch_that_succeeds_costs_nothing(monkeypatch):
    async def answer(client, base_url, batch_texts, batch_size=None):
        return list(batch_texts)

    monkeypatch.setattr(modernize, "_send_batch", answer)
    losses = {}

    modernize.modernize_texts(["une ligne"], losses=losses)

    assert losses == {"batches_failed": 0, "lines_lost": 0,
                      "readings_rejected": 0, "retries_unreachable": 0,
                      "lines_offered": 1}


def test_the_counters_are_optional_so_no_caller_has_to_change(monkeypatch):
    async def answer(client, base_url, batch_texts, batch_size=None):
        return list(batch_texts)

    monkeypatch.setattr(modernize, "_send_batch", answer)

    assert modernize.modernize_texts(["une ligne"]) == ["une ligne"]


def test_a_reading_the_guard_refused_is_counted_and_not_only_debugged(
        monkeypatch):
    """`still_bad` — a modernized line that came back diverging from its
    original and was therefore not used — was counted into a local, sent
    to a DEBUG line and dropped. So block 2 of every summary read
    "withheld on purpose … nothing" over runs that had withheld hundreds,
    which is the one thing CLAUDE.md names for that block."""
    async def diverging(client, base_url, batch_texts, batch_size=None):
        # One word in, three words out: the guard's own definition of a
        # reading it will not trust.
        return [f"{text} et encore" for text in batch_texts]

    monkeypatch.setattr(modernize, "_send_batch", diverging)
    losses = {}

    modernize.modernize_texts(["une", "deux"], losses=losses)

    assert losses["readings_rejected"] == 2
    assert losses["lines_offered"] == 2
    assert losses["batches_failed"] == 0


def test_a_retry_the_service_never_answered_is_an_incident_not_a_guard(
        monkeypatch):
    """`still_bad` counted two unrelated things: a reading the divergence
    guard refused, and a retry REQUEST that failed. Both went to block 2
    — "the guards did their job" — so a VieuxParler that died during the
    retry phase produced zero incidents and `--fail-on incident` passed,
    because block 2 never counts at any level by design."""
    import httpx

    calls = []

    async def answers_then_dies(client, base_url, batch_texts,
                                batch_size=None):
        calls.append(batch_texts)
        if len(batch_texts) == 1:
            # The per-line retry: the service has gone.
            raise httpx.ConnectError("connection refused")
        return [f"{text} et encore" for text in batch_texts]

    monkeypatch.setattr(modernize, "_send_batch", answers_then_dies)
    losses = {}

    modernize.modernize_texts(["une", "deux"], losses=losses)

    assert losses["retries_unreachable"] == 2
    assert losses["readings_rejected"] == 0
    # And they are lost lines, not withheld readings.
    assert losses["lines_lost"] == 2
