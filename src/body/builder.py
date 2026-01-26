# -----------------------------------------------------------
# Code by: Kelly Christensen
# Builds the <body> element with text from MainZone regions.
# -----------------------------------------------------------
"""
TEI Body builder module.

This module constructs the <body> element of a TEI document by assembling
text lines extracted from the sourceDoc. It handles different zone types
(MainZone, NumberingZone, MarginTextZone, etc.) and wraps them appropriately.
"""

from lxml import etree


def build_body(root, data):
    """
    Build the TEI <body> element from extracted line data.

    Creates the text structure with appropriate TEI elements:
    - <pb/> for page breaks
    - <fw> for running titles, page numbers, quire marks
    - <note> for margin text
    - <ab> for main text blocks
    - <hi> for emphasized lines (drop capitals, headings)
    - <lb/> for line breaks

    Args:
        root (etree.Element): TEI root element to append body to.
        data (list): List of Line namedtuples from Text class.

    Returns:
        None: Modifies root in place.
    """
    text = etree.SubElement(root, "text")
    body = etree.SubElement(text, "body")
    div = etree.SubElement(body, "div")

    for line in data:
        # Prepare zone attributes
        zone_atts = {"corresp": f"#{line.zone_id}", "type": line.zone_type}

        # Create <lb/> with reference to line's xml:id
        lb = etree.Element("lb", corresp=f"#{line.id}")
        lb.tail = f"{line.text}"

        # Add page break at first line of each page
        if int(line.n) == 1:
            pb = etree.Element("pb", corresp=f"#{line.page_id}")
            div.append(pb)

        # Ensure div has at least one element
        if len(div) == 0:
            pb = etree.Element("pb", corresp=f"#{line.page_id}")
            div.append(pb)

        last_element = div[-1]

        # Handle different zone types
        if line.zone_type in ("NumberingZone", "QuireMarksZone", "RunningTitleZone"):
            # Page numbers, quire marks, running titles -> <fw>
            fw = etree.Element("fw", zone_atts)
            last_element.addnext(fw)
            fw.append(lb)

        elif line.zone_type == "MarginTextZone":
            # Margin text -> <note>
            if last_element.tag != "note":
                note = etree.Element("note", zone_atts)
                last_element.addnext(note)
                note.append(lb)
            else:
                last_element.append(lb)

        elif line.zone_type and line.zone_type.startswith("Main"):
            # Main text -> <ab>
            if last_element.tag != "ab":
                ab = etree.Element("ab", zone_atts)
                last_element.addnext(ab)
                last_element = div[-1]

            # Handle emphasized lines (drop capitals, headings)
            if line.line_type in ("DropCapitalLine", "HeadingLine"):
                ab_children = last_element.getchildren()
                # Check if we need a new <hi> or can reuse existing
                if (
                    len(ab_children) == 0
                    or ab_children[-1].tag != "hi"
                    or ab_children[-1].get("rend") != line.line_type
                ):
                    hi = etree.Element("hi", rend=line.line_type)
                    last_element.append(hi)
                    hi.append(lb)
                elif ab_children[-1].tag == "hi":
                    ab_children[-1].append(lb)

            # Regular lines
            elif line.line_type and line.line_type.startswith("Default"):
                last_element.append(lb)
