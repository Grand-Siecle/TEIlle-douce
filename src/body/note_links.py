# -----------------------------------------------------------
# Links margin notes to the main-text lines they face,
# by vertical overlap of zone coordinates on the same surface.
# -----------------------------------------------------------
"""Margin note -> facing line linking.

Each <note corresp="#zone_x"> gets a @target listing the xml:ids of the
MainZone line zones (zoneLine_*) that vertically overlap the note's margin
zone on the same surface. target[0] is always the anchor line.

All <note> elements in the body are produced by build_body() exclusively
for MarginTextZone lines (see src/body/builder.py, zone_atts / "Margin
text -> <note>"); NumberingZone/QuireMarksZone/RunningTitleZone lines are
rendered as <fw>, not <note>. So linking "every <note> with a resolvable
corresp" is equivalent to "every MarginTextZone note" here - there is no
other note-producing zone type to filter out.

Anchor selection: by default the anchor is the highest overlapping line
(uly ascending), regardless of the order zones happen to appear in the
document. In this corpus (17th-century prints), some notes open with a
call mark (*, sometimes dagger/double-dagger) that is repeated in the
main-text line it annotates. When the note's margin-zone first line
starts with one of CALL_MARKS and that same character also occurs in one
of the overlapping candidate lines, that line is promoted to be the
anchor instead. This is strictly best-effort - OCR is noisy, so a
non-match silently falls back to the highest line.

The call-mark text comes from the raw OCR <line> text already present in
sourceDoc (not from the body/enrichment layer): linguistic annotation
does not preserve call-mark punctuation like "*", so relying on the
sourceDoc text keeps this available even when NER/enrichment is disabled.

When two or more notes on the same surface end up sharing the same
anchor line, they are numbered @n="1", "2", ... in the vertical order of
their own margin zones (uly ascending); notes with a unique anchor are
left without @n.
"""
import logging

from ..constants import XML_ID

logger = logging.getLogger(__name__)


CALL_MARKS = {"*", "†", "‡"}


def _floats(zone, *names):
    try:
        return [float(zone.get(n)) for n in names]
    except (TypeError, ValueError):
        return None


def _zone_text(zone):
    """Text of the zone's first line, in top-to-bottom order, or None.

    sourceDoc has two zone shapes: a DefaultLine zone that wraps a single
    <line> directly (this is the shape of every zoneLine_* main-text
    line), and a container zone - notably MarginTextZone - that instead
    wraps one or more nested DefaultLine zones, each with its own <line>.
    Both are handled here so margin-zone call-mark text can be found.
    """
    own_line = zone.find("line")
    if own_line is not None:
        text = "".join(own_line.itertext())
        return text or None

    nested = []
    for child in zone.findall("zone"):
        coords = _floats(child, "uly")
        child_line = child.find("line")
        if coords is None or child_line is None:
            continue
        nested.append((coords[0], child_line))
    if not nested:
        return None
    nested.sort(key=lambda t: t[0])
    text = "".join(nested[0][1].itertext())
    return text or None


def link_notes_to_lines(root):
    """Add @target (and, when ambiguous, @n) to margin <note> elements.

    Returns the number of notes linked (i.e. that got a @target).
    """
    sourcedoc = root.find(".//sourceDoc")
    body = root.find(".//body")
    if sourcedoc is None or body is None:
        return 0

    zone_pos = {}    # zone xml:id -> (surface_id, uly, lry, first_line_text)
    main_lines = {}  # surface_id -> [(line_id, uly, lry, first_line_text)]
    for surface in sourcedoc.findall("surface"):
        sid = surface.get(XML_ID)
        for zone in surface.iter("zone"):
            zid = zone.get(XML_ID)
            coords = _floats(zone, "uly", "lry")
            if not zid or coords is None:
                continue
            text = _zone_text(zone)
            zone_pos[zid] = (sid, coords[0], coords[1], text)
            parent = zone.getparent()
            # startswith("Main") mirrors body/builder.py's definition of
            # main-text zones, so both modules stay in sync if new Main*
            # SegmOnto types appear.
            if (zid.startswith("zoneLine_")
                    and parent is not None
                    and (parent.get("type") or "").startswith("Main")):
                main_lines.setdefault(sid, []).append(
                    (zid, coords[0], coords[1], text)
                )

    for lines in main_lines.values():
        lines.sort(key=lambda t: t[1])  # uly ascending: topmost line first

    # Group notes by surface, keeping their margin zone's vertical position
    # so notes on the same surface can be processed top-to-bottom.
    by_surface = {}  # surface_id -> [(margin_uly, margin_lry, note, margin_text)]
    for note in body.iter("note"):
        corresp = (note.get("corresp") or "").lstrip("#")
        pos = zone_pos.get(corresp)
        if pos is None:
            continue
        sid, n_uly, n_lry, n_text = pos
        by_surface.setdefault(sid, []).append((n_uly, n_lry, note, n_text))

    linked = 0
    refined = 0
    for sid, notes in by_surface.items():
        notes.sort(key=lambda t: t[0])  # vertical order of the margin zone

        # anchor line id -> notes anchored there, in processing order
        anchor_groups = {}
        for n_uly, n_lry, note, n_text in notes:
            candidates = [
                cand for cand in main_lines.get(sid, [])
                if cand[1] < n_lry and cand[2] > n_uly
            ]
            if not candidates:
                continue

            anchor = candidates[0]
            call = None
            stripped = (n_text or "").strip()
            if stripped and stripped[0] in CALL_MARKS:
                call = stripped[0]
            if call:
                starred = [c for c in candidates if call in (c[3] or "")]
                if starred:
                    if starred[0][0] != anchor[0]:
                        refined += 1
                    anchor = starred[0]

            rest = [c for c in candidates if c[0] != anchor[0]]
            targets = [anchor] + rest
            note.set("target", " ".join(f"#{t[0]}" for t in targets))
            linked += 1
            anchor_groups.setdefault(anchor[0], []).append(note)

        for group in anchor_groups.values():
            if len(group) > 1:
                for i, grouped_note in enumerate(group, start=1):
                    grouped_note.set("n", str(i))

    logger.info("Linked %d margin notes to facing lines", linked)
    logger.info("Refined %d note anchors via call marks", refined)
    return linked
