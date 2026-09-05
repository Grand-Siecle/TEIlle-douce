"""What the run talks to.

One object, whichever reporter is drawing. The panel and the journal read
the same collector, which is the only way the two can be guaranteed to
agree — and the reason the end-of-run summary is byte-identical in both.

Nothing here prints. It collects, and hands out either a `PanelState` for
the live panel, a list of events for the journal, or a `RunOutcome` for
the summary.
"""

import time
from dataclasses import dataclass
from pathlib import Path

from .counts import PhaseState, render_phase_loss
from .digest import WarningDigest
from .panel import DocumentLine, PanelState, PhaseLine
from .record import Block, Code, Locator, Loss, RunRecord
from .summary import RunOutcome


def _spoken(seconds):
    """`3h04`, `12m`, `10s` — a duration, not a number to divide."""
    hours, rest = divmod(int(seconds), 3600)
    minutes, seconds = divmod(rest, 60)
    if hours:
        return f"{hours}h{minutes:02d}"
    if minutes:
        return f"{minutes}m{seconds:02d}"
    return f"{seconds}s"


class Run:
    """Everything one run knows about itself, as it goes."""

    def __init__(self, input_dir, output_dir, volumes, pages, log_path=None,
                 services=(), started_at=None, on_change=None):
        self.input_dir = Path(input_dir)
        self.output_dir = Path(output_dir)
        self.volumes_total = volumes
        self.pages_total = pages
        # `--no-log-file` is a supported way to run: the panel then says
        # there is no log rather than pointing at one that is not there.
        self.log_path = Path(log_path) if log_path is not None else None
        self.services = tuple(services)
        self.started_at = started_at if started_at is not None else time.monotonic()

        self.record = RunRecord()
        self.digest = WarningDigest()

        self._written = 0
        self._pages_written = 0
        self._failed = []
        self._failed_archives = []
        self._open = None
        self._open_pages = 0
        self._open_read = 0
        self._open_started = None
        self._phases = {}
        self._steps = {}
        # Seconds per page, one entry per volume that finished. The
        # estimate is a median of these: one volume that took twenty
        # minutes against a dead service must not move the figure for the
        # other twenty-six.
        self._seconds_per_page = []
        self._pages_lost = 0
        # The panel redraws on this. Called after a change and never
        # before: a frame drawn from half-updated state is worse than one
        # frame late.
        self._on_change = on_change or (lambda: None)
        # Set by the run when it has somewhere to index incidents.
        self.on_incident = None

    # -- what the pipeline tells it ---------------------------------------

    def document_started(self, name, pages, at=None):
        self._open = name
        self._open_pages = pages
        self._open_read = 0
        self._open_started = at if at is not None else time.monotonic()
        self._phases = {}
        self._on_change()

    def pages_read(self, document, count):
        """Pages read in the volume still open: work done and not saved.

        Counted apart from what is written, because the bar's middle zone
        is exactly "at risk" — claiming it as written would make the bar
        promise more than the disk holds.
        """
        if document == self._open:
            self._open_read = count
        self._on_change()

    def step(self, document, step):
        """Where in the sequence this document is.

        Kept because `_process_document`'s return value is thrown away
        today, so a document that raises loses the step it raised in —
        which is what a reader needs first.
        """
        self._steps[document] = step

    def phase(self, document, name, state, done=0, total=None, unit="",
              rate=None, waiting=None, timeout=None, note="", reason="",
              elapsed=None):
        self._phases[name] = PhaseLine(
            name=name, state=state, done=done, total=total, unit=unit,
            rate=rate, waiting=waiting, timeout=timeout, note=note,
            reason=reason, elapsed=elapsed)
        self._on_change()
        if state is PhaseState.LOST:
            # A reporter must not be able to kill the run it reports on.
            # `render_phase_loss` refuses a lost phase with no cause and
            # no denominator — rightly, when a call site is writing one —
            # but here the caller is already in trouble, and raising would
            # also leave the record holding a loss the journal never got.
            reason = reason or "cause unknown"
            total = 0 if total is None else total
            self.lost(Loss(Code.PHASE_LOST, document, name,
                           Locator.document(document), count=done,
                           total=total, detail=reason))
            # Through `render_phase_loss` and not a sentence of its own:
            # the denominator is what makes the zero readable, and a
            # second way of writing a loss is a second way of writing it
            # wrongly.)
        self._on_change()

    def warning(self, document, message):
        """Straight to the digest, never to the console.

        Eighty of these a volume, printed one at a time, push everything
        else off the screen — and that is how a run that is repairing a
        source defect comes to look like a run that is failing.
        """
        self.digest.add(document, message)

    def lost(self, loss):
        self.record.add(loss)
        if self.on_incident is not None:
            self.on_incident(loss)

    def archive_failed(self, name, reason):
        self._failed_archives.append((name, reason))
        self.lost(Loss(Code.ARCHIVE_CORRUPT, name, "expand",
                       Locator.file(self.input_dir / name), count=1, total=1,
                       detail=reason))

    def volume_unreadable(self, name, reason):
        """A volume this process may not open.

        An incident and not a source defect: the ALTO may be perfectly
        good, and the remedy is a mode change rather than a repack.
        """
        self._failed_archives.append((name, reason))
        self.lost(Loss(Code.VOLUME_UNREADABLE, name, "expand",
                       Locator.document(name), count=1, total=1,
                       detail=reason))

    def document_finished(self, name, ok, reason="", at=None):
        if ok:
            if self._open_started is not None and self._open_pages:
                took = (at if at is not None else time.monotonic()) \
                    - self._open_started
                if took > 0:
                    self._seconds_per_page.append(took / self._open_pages)
            self._written += 1
            # What was read, not what was offered: a volume of 754 pages
            # losing 3 wrote 751 surfaces, and claiming 754 one line above
            # "pages unusable 3 of 754" is the summary contradicting
            # itself. `pages_read` is tracked for exactly this.
            self._pages_written += (self._open_read or self._open_pages)
        else:
            self._pages_lost += self._open_pages
            self._failed.append((name, reason))
            # Nothing was written, so nothing may be claimed: the at-risk
            # pages go with it, or the bar keeps growing on work that was
            # thrown away.
            self.lost(Loss(Code.DOCUMENT_FAILED, name,
                           self._steps.get(name, "run"),
                           Locator.document(name), count=1, total=1,
                           detail=reason))
        self._open = None
        self._open_read = 0
        self._open_pages = 0
        self._on_change()

    # -- what the reporters read ------------------------------------------

    def eta(self, now=None):
        """How long is left, or nothing.

        Nothing until a volume has finished: there is nothing to
        extrapolate from, and a made-up figure on a four-hour run is
        worse than none. The count of samples is part of the answer, so
        the reader can judge how much to trust it.
        """
        if not self._seconds_per_page:
            return ""
        # Minus what will never be processed: a failed volume's pages
        # stayed in the denominator for ever, so a run with failures
        # over-stated to the end and never said "nothing left".
        remaining = self.pages_total - self._pages_written - self._pages_lost
        if remaining <= 0:
            return ""
        ordered = sorted(self._seconds_per_page)
        middle = len(ordered) // 2
        median = (ordered[middle] if len(ordered) % 2
                  else (ordered[middle - 1] + ordered[middle]) / 2)
        seconds = int(median * remaining)
        return f"~{_spoken(seconds)} (median of {len(ordered)})"

    def panel(self, now=None, eta="", pages_per_second=0.0):
        now = now if now is not None else time.monotonic()
        current = None
        if self._open is not None:
            current = DocumentLine(
                name=self._open, pages=self._open_pages,
                elapsed=int(now - (self._open_started or now)),
                phases=tuple(self._phases.values()))
        return PanelState(
            input_dir=str(self.input_dir), output_dir=str(self.output_dir),
            elapsed=int(now - self.started_at), eta=eta,
            services=self.services,
            log_path=str(self.log_path) if self.log_path else "(none)",
            pages_written=self._pages_written,
            pages_in_flight=self._open_read,
            pages_total=self.pages_total,
            volumes_written=self._written,
            # Archives and unreadable directories are failures too: they
            # are in the denominator, so leaving them out of the failed
            # count made a finished run end on "3 written · 0 failed · 1
            # to go" with nothing left to do.
            volumes_failed=len(self._failed) + len(self._failed_archives),
            volumes_to_go=max(0, self.volumes_total - self._written
                              - len(self._failed)
                              - len(self._failed_archives)),
            pages_per_second=round(pages_per_second, 1),
            current=current,
            source_lost=self.record.total(Block.SOURCE),
            withheld=self.record.total(Block.WITHHELD),
            incidents=self.record.total(Block.INCIDENT),
            incident_lines=self._incident_lines(),
            digest=self.digest)

    def _incident_lines(self):
        lines = []
        for index, loss in enumerate(self.record.losses(Block.INCIDENT), 1):
            lines.append(f"I{index} {loss.locator.render()}  "
                         f"{loss.step}.{loss.code.value}  {loss.detail}".strip())
        return tuple(lines)

    @property
    def open_document(self):
        """The volume being converted, or None between two of them.

        The digest counts volumes and a logging record does not name one,
        so the run is what supplies it.
        """
        return self._open

    def pages_lost(self, document):
        """Pages of one volume that produced no <surface>."""
        return sum(loss.count for loss in self.record.losses()
                   if loss.document == document
                   and loss.code is Code.PAGE_UNUSABLE)

    @property
    def failed_archives(self):
        return tuple(self._failed_archives)

    def finished(self, exit_code, elapsed=None, fail_on="never", headline="",
                 page_loss_failures=(), max_page_loss=100.0):
        return RunOutcome(
            input_dir=self.input_dir, output_dir=self.output_dir,
            volumes_total=self.volumes_total,
            volumes_written=self._written,
            pages_total=self.pages_total,
            pages_written=self._pages_written,
            record=self.record,
            elapsed=(elapsed if elapsed is not None
                     else time.monotonic() - self.started_at),
            exit_code=exit_code, fail_on=fail_on,
            failed_documents=tuple(self._failed),
            failed_archives=tuple(self._failed_archives),
            headline=headline,
            page_loss_failures=tuple(page_loss_failures),
            max_page_loss=max_page_loss)
