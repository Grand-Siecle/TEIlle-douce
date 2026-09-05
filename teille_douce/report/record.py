"""What a run lost, with an address for each of it.

The word "lost" covered three unrelated causes, so it is split into three
blocks that a reader can act on differently:

    the source was defective   nothing the pipeline could do
    withheld on purpose        the guards did their job
    lost to an incident        this needs a human

A number in any of them carries a document, the step it happened in, and
at least one typed locator. `Kind.DOC` is the floor — it says the pipeline
knows the volume and nothing finer, which is an admission the summary
prints rather than hides. What is never recorded is an internal index:
"container 12" and "batch_start 640" are what the pipeline says today, and
neither is anything a reader can open.
"""

from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Optional


class Block(Enum):
    """The three causes, which have three different remedies."""

    SOURCE = "the source was defective"
    WITHHELD = "withheld on purpose"
    INCIDENT = "lost to an incident"


class Kind(Enum):
    """How precisely a loss can be pointed at."""

    PAGE = "page"   # resolves to a file, an XPath and a IIIF region
    FILE = "file"   # an archive, a CSV
    DOC = "doc"     # the floor: this volume, and no finer


@dataclass(frozen=True, slots=True)
class Locator:
    """Where a loss happened, in terms the reader can follow.

    A page id is worth four things at once, because `surface/@xml:id` IS
    the ALTO file stem: the input file, an XPath into the output, the IIIF
    region of the printed page, and the zone ids under it.
    """

    # `doc` and `page_id`, not `document` and `page`: the constructors
    # below are named for what they build, and a dataclass field would
    # shadow them.
    kind: Kind
    doc: str
    page_id: Optional[str] = None
    path: Optional[Path] = None

    @classmethod
    def page(cls, document, page_id):
        return cls(Kind.PAGE, document, page_id=page_id)

    @classmethod
    def file(cls, path):
        return cls(Kind.FILE, Path(path).stem, path=Path(path))

    @classmethod
    def document(cls, document):
        return cls(Kind.DOC, document)

    def input_path(self, ocr_dir):
        """The ALTO file this came from, ready to open — or None.

        Searched rather than composed. A volume's pages live under
        `<doc>/content/data/doc_N/`, and the N is not something a
        locator can know, so `<ocr>/<doc>/<stem>.xml` named a path that
        has never existed on this corpus. None where the file is gone,
        which is a thing that happens between a run and the reading of
        its record.
        """
        if self.kind is Kind.FILE:
            return self.path
        if self.kind is not Kind.PAGE:
            raise ValueError(f"no page in a {self.kind.value} locator")
        return next(Path(ocr_dir).joinpath(self.doc)
                    .rglob(f"{self.page_id}.xml"), None)

    def xpath(self):
        """An XPath into the produced TEI that lands on the right surface.

        On the local name: the produced file is namespaced, so
        `//surface` matched nothing in it — an address that does not
        resolve is worse than no address, because the reader spends the
        afternoon believing the surface is missing.
        """
        if self.kind is not Kind.PAGE:
            raise ValueError(f"no page in a {self.kind.value} locator")
        return (f"//*[local-name()='surface']"
                f"[@xml:id='{self.page_id}']")

    def render(self):
        if self.kind is Kind.PAGE:
            return f"{self.doc}/{self.page_id}"
        if self.kind is Kind.FILE:
            return str(self.path)
        return self.doc


class Code(Enum):
    """Every way this pipeline can lose something, and where it belongs.

    Closed on purpose. A loss with no code cannot be printed by the
    summary, and a code in two blocks would let one failure be counted
    twice.
    """

    # the source was defective
    PAGE_UNUSABLE = ("page_unusable", Block.SOURCE)
    ARCHIVE_CORRUPT = ("archive_corrupt", Block.SOURCE)
    VOLUME_UNREADABLE = ("volume_unreadable", Block.INCIDENT)
    # A per-line retry the service never answered. Not a failed batch:
    # the batch went through, and what is counted here is lines.
    RETRY_UNANSWERED = ("retry_unanswered", Block.INCIDENT)
    ALTO_IDS_REPAIRED = ("alto_ids_repaired", Block.SOURCE, True)

    # withheld on purpose
    READING_REJECTED = ("reading_rejected", Block.WITHHELD)
    ENTITY_FILTERED = ("entity_filtered", Block.WITHHELD)
    CONTAINER_UNANCHORED = ("container_unanchored", Block.WITHHELD)

    # lost to an incident
    PHASE_LOST = ("phase_lost", Block.INCIDENT)
    CONTAINER_FAILED = ("container_failed", Block.INCIDENT)
    DOCUMENT_FAILED = ("document_failed", Block.INCIDENT)
    BREAKER_SKIPPED = ("breaker_skipped", Block.INCIDENT)
    BATCH_FAILED = ("batch_failed", Block.INCIDENT)

    def __init__(self, label, block, repaired=False):
        self._value_ = label
        self.block = block
        # A defect that was repaired rather than suffered. Nothing is
        # lost; the repair is written down so the biggest number in the
        # summary stops reading as an alarm.
        self.repaired = repaired

    @property
    def needs_a_human(self):
        return self.block is Block.INCIDENT


@dataclass(frozen=True, slots=True)
class Loss:
    """One measured loss, with its address.

    `total` is not optional. The renderer refuses a count with no
    denominator, and refusing it here instead means the call site — which
    knows the total — is the one that has to supply it.
    """

    code: Code
    document: str
    step: str
    locator: Locator
    count: int
    total: int
    detail: str = ""
    # What the count counts. Empty where the label already says it; set
    # where two accounts of the same loss are measured differently and a
    # reader has to join them — a whole phase lost is `3 of 27 volumes`
    # in the summary and 69 containers on the panel's incident counter.
    unit: str = ""

    def __post_init__(self):
        if self.locator is None:
            raise ValueError(
                f"{self.code.value} in {self.document}: a loss needs a "
                f"locator, at least Kind.DOC"
            )
        if self.total is None:
            raise ValueError(
                f"{self.code.value} in {self.document}: a loss needs the "
                f"total it is measured against"
            )

    @property
    def block(self):
        return self.code.block


class RunRecord:
    """Everything one run lost, in the order it happened."""

    def __init__(self):
        self._losses = []

    def add(self, loss):
        self._losses.append(loss)

    def losses(self, block=None):
        return tuple(entry for entry in self._losses
                     if block is None or entry.block is block)

    def total(self, block):
        """How much that block LOST. Answers 0 rather than nothing: a line
        that disappears cannot be told from a phase never checked.

        Repairs are not in it. `Code.repaired` exists to keep a defect
        that cost nothing out of loss arithmetic, and this is the largest
        such figure the corpus produces — counted, a clean run would
        report six thousand losses.
        """
        return sum(entry.count for entry in self.losses(block)
                   if not entry.code.repaired)

    def whole_phases_lost(self):
        """Documents written with no annotation at all.

        Never folded into a container count. A document that carries none
        of a phase is damage of a different kind, and averaging the two
        would be the convenient lie.
        """
        return len({entry.document for entry in self._losses
                    if entry.code is Code.PHASE_LOST})

    def located(self):
        """How much of what was lost can actually be looked at.

        Counted in losses and not in records: the reader wants to know how
        many of the 31 667 things this run dropped they can open, not how
        many lines the collector happened to write.
        """
        lost = [entry for entry in self._losses if not entry.code.repaired]
        precise = sum(entry.count for entry in lost
                      if entry.locator.kind is not Kind.DOC)
        vague = sum(entry.count for entry in lost
                    if entry.locator.kind is Kind.DOC)
        return {"precise": precise, "document_only": vague}
