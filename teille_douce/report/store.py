"""What a run leaves behind for the reader who comes back on Thursday.

    tei_output/.teille-douce/runs/20260903-180824-0031415/
        run.json         settings, argv, status per document
        incidents.jsonl  one incident per line, append-only, greppable
        pipeline.log     this run's log, beside its own index

The log is the transcript and the JSONL is its index. No fact is stored
twice in two forms that could diverge: the record carries structured
fields, the log carries the prose and the tracebacks.

Written line by line, and from the parent only. `execute` catches
`Exception`; a KeyboardInterrupt is a BaseException and passes straight
through it, so a Ctrl-C in the fourth hour used to take four hours of
knowledge with it. And a `logger` called in a forkserver worker never
reaches the parent's file, while concurrent writes to one descriptor
interleave — so nothing here is called from a child.
"""

import json
import os
import shutil
from datetime import datetime
from pathlib import Path

from .record import Block

RUNS = Path(".teille-douce") / "runs"
KEEP = 10


class RunStore:
    """One run's directory, created the first time something is written."""

    def __init__(self, output_dir, now=None):
        self.output_dir = Path(output_dir)
        # The pid, for the reason `_run_log_path` already carries one: a
        # launcher firing several volumes at once starts them inside the
        # same second, and two runs sharing this directory means one
        # replaces the other's log and overwrites its manifest — while
        # both exit reporting a record that is not theirs. After the
        # timestamp, so `latest()` still sorts by when a run started.
        stamp = (now or datetime.now()).strftime("%Y%m%d-%H%M%S")
        # Zero-padded, because `latest()` and `prune()` sort on the name:
        # unpadded, "1234" sorts before "987" and the newer of two runs
        # started in the same second was both missed by --retry-failed and
        # deleted first by the retention.
        self.path = self.output_dir / RUNS / f"{stamp}-{os.getpid():07d}"
        # A reporter may not be the thing that ends a four-hour job. If
        # the directory cannot be written — a mistyped `-o` pointing at a
        # file, a read-only mount — the run says so once and carries on.
        self.problems = []

    @property
    def unwritable(self):
        return bool(self.problems)

    def _ready(self):
        # Not in __init__: a run that refuses to start leaves nothing
        # behind, which is the rule the lazy log handler already follows.
        #
        # A previous failure is recorded but does not latch: one blip on
        # an incident append at minute three used to cost the manifest,
        # and with it the next `--retry-failed`.
        try:
            self.path.mkdir(parents=True, exist_ok=True)
        except OSError as reason:
            self._note(reason)
            return None
        return self.path

    def _note(self, reason):
        """Record a failure once. Recorded, not latched: the run keeps
        trying the other artefacts, because one blip on an incident append
        used to cost the manifest and with it the next --retry-failed."""
        message = str(reason)
        if message not in self.problems:
            self.problems.append(message)

    def adopt_log(self, path):
        """Move this run's log in beside its own index.

        Moved at the end rather than written here from the start: the
        directory sits under the output directory, and creating it while
        the run may still refuse to start would break the promise that
        exit 3 leaves nothing behind. Together afterwards, they are also
        pruned together, so an index can never outlive its transcript.

        `log_file` was the one setting with no home, which is how
        twenty-one orphan logs came to sit at the root of the repository.
        """
        if path is None:
            return None
        where = self._ready()
        if where is None:
            return path
        try:
            destination = where / "pipeline.log"
            Path(path).replace(destination)
            return destination
        except OSError as reason:
            # A log left where it was is still a log. Losing the run over
            # a rename would not be — and cross-device is an ordinary
            # layout, not a corruption: cwd on one filesystem, `-o` on
            # another.
            self._note(reason)
            return path

    def incident(self, loss):
        """Index one incident. Blocks 1 and 2 stay in the summary and the
        log — an index of everything is an index of nothing."""
        if loss.block is not Block.INCIDENT:
            return
        entry = {
            "code": loss.code.value,
            "document": loss.document,
            "step": loss.step,
            "locator": loss.locator.render(),
            "kind": loss.locator.kind.value,
            "count": loss.count,
            "total": loss.total,
            "detail": loss.detail,
        }
        where = self._ready()
        if where is None:
            return
        try:
            with open(where / "incidents.jsonl", "a",
                      encoding="utf-8") as handle:
                handle.write(json.dumps(entry, ensure_ascii=False) + "\n")
                # Flushed as it happens: the point of the format is that
                # an interruption keeps everything written before it.
                handle.flush()
        except OSError as reason:
            self._note(reason)

    def finish(self, argv, settings, documents, exit_code):
        """The manifest. Written on every way out, 130 included."""
        manifest = {
            "argv": list(argv),
            "settings": settings,
            "documents": dict(documents),
            "exit_code": exit_code,
        }
        where = self._ready()
        if where is None:
            return
        try:
            (where / "run.json").write_text(
                json.dumps(manifest, ensure_ascii=False, indent=2) + "\n",
                encoding="utf-8")
        except OSError as reason:
            self._note(reason)

    # -- coming back later -------------------------------------------------

    @staticmethod
    def _runs(output_dir):
        root = Path(output_dir) / RUNS
        try:
            if not root.is_dir():
                return []
            return sorted((p for p in root.iterdir() if p.is_dir()),
                          key=lambda p: p.name)
        except OSError:
            return []

    @classmethod
    def latest(cls, output_dir):
        runs = cls._runs(output_dir)
        return runs[-1] if runs else None

    @classmethod
    def failed_last_time(cls, output_dir):
        """What `--retry-failed` converts.

        An empty tuple means the last run had no failures. A manifest
        that cannot be read is a different thing and raises: reporting it
        as "nothing failed" is a lie, and the caller can say so.
        """
        # `_runs` swallows OSError and answers "no runs", which the
        # caller then reads as "the last run was clean" — so an output
        # directory whose `.teille-douce` is a file, or a permission this
        # process does not have, sent the operator away believing
        # nothing had failed. Asked here, where the answer can be told
        # apart from an empty one.
        root = Path(output_dir) / RUNS
        try:
            # The holder as well as the runs directory: with
            # `.teille-douce` a file, nothing UNDER it reports existing,
            # so asking only about `runs` answered "no record" for a path
            # that cannot hold one.
            unusable = next(
                (str(step) for step in (root.parent, root)
                 if step.exists() and not (step.is_dir()
                                           and os.access(step, os.R_OK))),
                None)
        except OSError as reason:
            raise ValueError(f"{root} could not be read: {reason}")
        if unusable:
            raise ValueError(f"{unusable} is not a readable directory, so "
                             f"the last run's failures could not be read")
        latest = cls.latest(output_dir)
        if latest is None:
            return ()
        try:
            manifest = json.loads(
                (latest / "run.json").read_text(encoding="utf-8"))
        except FileNotFoundError:
            raise ValueError(
                f"{latest.name} kept no manifest, so its failures could not "
                f"be read")
        except (OSError, ValueError) as reason:
            raise ValueError(
                f"the record of {latest.name} could not be read: {reason}")
        # Shape-checked, not assumed. `json.loads` is happy with `null`,
        # `[]` or a bare string — a truncated write flushed mid-object is
        # all three at different moments — and `.get` on any of them
        # raised AttributeError, which is not a ValueError, so it went
        # straight past the caller's guard and out of the CLI as a
        # traceback with exit 1: a wrapper read "some volumes failed".
        documents = manifest.get("documents") if isinstance(manifest, dict) else None
        if not isinstance(documents, dict):
            raise ValueError(
                f"the record of {latest.name} is not a run manifest")
        return tuple(name for name, status in documents.items()
                     if status == "failed")

    @classmethod
    def prune(cls, output_dir, keep=KEEP, spare=None):
        """Whole directories, log included: an index and its transcript
        must not be able to survive each other.

        `spare` is the run doing the pruning. Every run prunes, so a long
        one outranked by ten later short ones was rmtree'd from under
        itself: `_ready` then quietly recreated the directory, the
        manifest was rewritten into it and the incident index was not —
        an index outlived by its transcript, which is the one thing this
        class says cannot happen — and `--retry-failed` afterwards
        reported nothing to retry.
        """
        spare = Path(spare).resolve() if spare is not None else None
        for old in cls._runs(output_dir)[:-keep or None]:
            if spare is not None and old.resolve() == spare:
                continue
            shutil.rmtree(old, ignore_errors=True)
