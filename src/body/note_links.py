# -----------------------------------------------------------
# Links margin notes to the main-text lines they face,
# by vertical overlap of zone coordinates on the same surface.
# -----------------------------------------------------------
"""Margin note -> facing line linking.

Each <note corresp="#zone_x"> gets a @target listing the xml:ids of the
MainZone line zones (zoneLine_*) that vertically overlap the note's margin
zone on the same surface.

All <note> elements in the body are produced by build_body() exclusively
for MarginTextZone lines (see src/body/builder.py, zone_atts / "Margin
text -> <note>"); NumberingZone/QuireMarksZone/RunningTitleZone lines are
rendered as <fw>, not <note>. So linking "every <note> with a resolvable
corresp" is equivalent to "every MarginTextZone note" here - there is no
other note-producing zone type to filter out.
"""
import logging

logger = logging.getLogger(__name__)

XML_ID = "{http://www.w3.org/XML/1998/namespace}id"


def _floats(zone, *names):
    try:
        return [float(zone.get(n)) for n in names]
    except (TypeError, ValueError):
        return None


def link_notes_to_lines(root):
    """Add @target to margin <note> elements. Returns the number linked."""
    sourcedoc = root.find(".//sourceDoc")
    body = root.find(".//body")
    if sourcedoc is None or body is None:
        return 0

    zone_pos = {}    # zone xml:id -> (surface_id, uly, lry)
    main_lines = {}  # surface_id -> [(line_id, uly, lry)]
    for surface in sourcedoc.findall("surface"):
        sid = surface.get(XML_ID)
        for zone in surface.iter("zone"):
            zid = zone.get(XML_ID)
            coords = _floats(zone, "uly", "lry")
            if not zid or coords is None:
                continue
            zone_pos[zid] = (sid, coords[0], coords[1])
            parent = zone.getparent()
            # startswith("Main") mirrors body/builder.py's definition of
            # main-text zones, so both modules stay in sync if new Main*
            # SegmOnto types appear.
            if (zid.startswith("zoneLine_")
                    and parent is not None
                    and (parent.get("type") or "").startswith("Main")):
                main_lines.setdefault(sid, []).append((zid, coords[0], coords[1]))

    linked = 0
    for note in body.iter("note"):
        corresp = (note.get("corresp") or "").lstrip("#")
        pos = zone_pos.get(corresp)
        if pos is None:
            continue
        sid, n_uly, n_lry = pos
        targets = [
            lid for lid, uly, lry in main_lines.get(sid, [])
            if uly < n_lry and lry > n_uly
        ]
        if targets:
            note.set("target", " ".join(f"#{t}" for t in targets))
            linked += 1

    logger.info("Linked %d margin notes to facing lines", linked)
    return linked
