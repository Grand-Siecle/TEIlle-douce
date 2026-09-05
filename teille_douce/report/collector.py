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


@dataclass(frozen=True, slots=True)
class Event:
    """Something worth a line in the journal, as it happened."""

    kind: str
    document: str = ""
    text: str = ""


class Run:
    """Everything one run knows about itself, as it goes."""

    def __init__(self, input_dir, output_dir, volumes, pages, log_path=None,
                 services=(), started_at=None):
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

        self._events = []
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

    # -- what the pipeline tells it ---------------------------------------

    def document_started(self, name, pages, at=None):
        self._open = name
        self._open_pages = pages
        self._open_read = 0
        self._open_started = at if at is not None else time.monotonic()
        self._phases = {}
        self._events.append(Event("document", name, f"{pages} pages"))

    def pages_read(self, document, count):
        """Pages read in the volume still open: work done and not saved.

        Counted apart from what is written, because the bar's middle zone
        is exactly "at risk" — claiming it as written would make the bar
        promise more than the disk holds.
        """
        if document == self._open:
            self._open_read = count

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
        if state is PhaseState.LOST:
            self.lost(Loss(Code.PHASE_LOST, document, name,
                           Locator.document(document), count=done,
                           total=total if total is not None else 0,
                           detail=reason))
            # Through `render_phase_loss` and not a sentence of its own:
            # the denominator is what makes the zero readable, and a
            # second way of writing a loss is a second way of writing it
            # wrongly.
            self._events.append(Event(
                "phase-lost", document,
                render_phase_loss(PhaseState.LOST, count=done, total=total,
                                  unit=unit, reason=reason)))

    def warning(self, document, message):
        """Straight to the digest, never to the console.

        Eighty of these a volume, printed one at a time, push everything
        else off the screen — and that is how a run that is repairing a
        source defect comes to look like a run that is failing.
        """
        self.digest.add(document, message)

    def lost(self, loss):
        self.record.add(loss)

    def archive_failed(self, name, reason):
        self._failed_archives.append((name, reason))
        self.lost(Loss(Code.ARCHIVE_CORRUPT, name, "expand",
                       Locator.file(self.input_dir / name), count=1, total=1,
                       detail=reason))
        self._events.append(Event("archive-failed", name, reason))

    def document_finished(self, name, ok, reason="", at=None):
        if ok:
            self._written += 1
            self._pages_written += self._open_pages
            self._events.append(Event("done", name, ""))
        else:
            self._failed.append((name, reason))
            # Nothing was written, so nothing may be claimed: the at-risk
            # pages go with it, or the bar keeps growing on work that was
            # thrown away.
            self.lost(Loss(Code.DOCUMENT_FAILED, name,
                           self._steps.get(name, "run"),
                           Locator.document(name), count=1, total=1,
                           detail=reason))
            self._events.append(Event("failed", name, reason))
        self._open = None
        self._open_read = 0
        self._open_pages = 0

    # -- what the reporters read ------------------------------------------

    def drain(self):
        """The events not yet said out loud. Said once, never twice."""
        events, self._events = self._events, []
        return tuple(events)

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
            volumes_failed=len(self._failed),
            volumes_to_go=max(0, self.volumes_total - self._written
                              - len(self._failed)),
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
    def failed_archives(self):
        return tuple(self._failed_archives)

    def finished(self, exit_code, elapsed=None, fail_on="never", headline=""):
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
            headline=headline)
