# -----------------------------------------------------------
# Code by: Kelly Christensen
# Python class to build elements inside the <sourceDoc> and map data to them.
# -----------------------------------------------------------

from collections import defaultdict
from lxml import etree
import re
import uuid
from .constants import NS_ALTO  # namespace for the Alto xml

def labels(filepath):
    root = etree.parse(filepath).getroot()
    elements = [t.attrib for t in root.findall('.//a:OtherTag', namespaces=NS_ALTO)]
    collect = defaultdict(dict)
    for d in elements:
        collect[d["ID"]] = d["LABEL"]
    tags = dict(collect)
    return tags

class SurfaceTree:
    """Creates a <surface> element and its children for one ALTO page."""

    def __init__(self, doc, folio, alto_root):
        self.doc = doc
        self.folio = folio
        self.root = alto_root

        # stockage des UUID pour pouvoir cibler les éléments TEI
        self.ids = {}

    def _uuid(self):
        return "id" + uuid.uuid4().hex

    def surface(self, surface_group, page_attributes):
        surface_uuid = self._uuid()
        self.ids[("surface", self.folio)] = surface_uuid

        surface = etree.SubElement(
            surface_group,
            "surface",
            {"{http://www.w3.org/XML/1998/namespace}id": surface_uuid, **page_attributes},
        )

        etree.SubElement(
            surface,
            "graphic",
            url=f"https://gallica.bnf.fr/iiif/ark:/12148/{self.doc}/f{self.folio}/full/full/0/native.jpg"
        )
        return surface

    # ---------------------------------------------------------------
    # TEXTBLOCK (zone1)
    # ---------------------------------------------------------------
    def zone1(self, surface, attributes, block_id, blocks_on_page):

        zone_uuid = self._uuid()
        self.ids[("block", self.folio, block_id)] = zone_uuid

        zone = etree.SubElement(
            surface,
            "zone",
            {"{http://www.w3.org/XML/1998/namespace}id": zone_uuid}
        )

        for k, v in attributes.items():
            zone.attrib[k] = v

        return zone

    # ---------------------------------------------------------------
    # TEXTLINE (zone2)
    # ---------------------------------------------------------------
    def zone2(self, textblock, block_parent, attributes, line_id, lines_on_page):

        zone_uuid = self._uuid()
        self.ids[("linezone", self.folio, block_parent, line_id)] = zone_uuid

        zone = etree.SubElement(
            textblock,
            "zone",
            {"{http://www.w3.org/XML/1998/namespace}id": zone_uuid}
        )

        for k, v in attributes.items():
            zone.attrib[k] = v

        # baseline <path>
        path_uuid = self._uuid()
        baseline = etree.SubElement(
            zone,
            "path",
            {"{http://www.w3.org/XML/1998/namespace}id": path_uuid}
        )

        b = self.root.find(f'.//a:TextLine[@ID="{line_id}"]', namespaces=NS_ALTO).get("BASELINE")
        baseline.attrib["points"] = " ".join([re.sub(r"\s", ",", x) for x in re.findall(r"(\d+ \d+)", b)])

        return zone

    # ---------------------------------------------------------------
    # LINE element (<line>)
    # ---------------------------------------------------------------
    def line(self, textline, block_parent, line_parent, lines_on_page, extracted_words):

        line_uuid = self._uuid()
        self.ids[("line", self.folio, block_parent, line_parent)] = line_uuid

        line_el = etree.SubElement(
            textline,
            "line",
            {"{http://www.w3.org/XML/1998/namespace}id": line_uuid}
        )
        line_el.attrib["n"] = str(lines_on_page)

        if extracted_words:
            line_el.text = extracted_words
        else:
            line_el.text = self.root.find(
                f'.//a:TextLine[@ID="{line_parent}"]/a:String',
                namespaces=NS_ALTO
            ).get("CONTENT")

        return line_el

    # ---------------------------------------------------------------
    # WORD / STRING (zone3)
    # ---------------------------------------------------------------
    def zone3(self, textline, block_parent, line_parent, attributes, seg_id, strings_on_page):

        string_uuid = self._uuid()
        self.ids[("string", self.folio, block_parent, line_parent, seg_id)] = string_uuid

        zone = etree.SubElement(
            textline,
            "zone",
            {"{http://www.w3.org/XML/1998/namespace}id": string_uuid}
        )
        for k, v in attributes.items():
            zone.attrib[k] = v

        alto_string = self.root.find(
            f'.//a:String[@ID="{seg_id}"]',
            namespaces=NS_ALTO
        )
        if alto_string is not None:
            wc = alto_string.get("WC")
            if wc:
                cert_uuid = self._uuid()
                cert = etree.SubElement(zone, "certainty", {
                    "{http://www.w3.org/XML/1998/namespace}id": cert_uuid,
                    "locus": "value",
                    "degree": wc,
                    "target": f"#{string_uuid}"
                })

        return zone

    # ---------------------------------------------------------------
    # GLYPH (zone4)
    # ---------------------------------------------------------------
    def zone4(self, string, block_parent, line_parent, seg_parent, attributes, glyph_id, glyphs_on_page):

        glyph_uuid = self._uuid()
        self.ids[("glyphzone", self.folio, block_parent, line_parent, seg_parent, glyph_id)] = glyph_uuid

        zone = etree.SubElement(
            string,
            "zone",
            {"{http://www.w3.org/XML/1998/namespace}id": glyph_uuid}
        )

        for k, v in attributes.items():
            zone.attrib[k] = v

        alto_glyph = self.root.find(
            f'.//a:Glyph[@ID="{glyph_id}"]',
            namespaces=NS_ALTO
        )
        if alto_glyph is not None:
            gc = alto_glyph.get("GC")
            if gc:
                cert_uuid = self._uuid()
                etree.SubElement(zone, "certainty", {
                    "{http://www.w3.org/XML/1998/namespace}id": cert_uuid,
                    "locus": "value",
                    "degree": gc,
                    "target": f"#{glyph_uuid}"
                })

        return zone

    # ---------------------------------------------------------------
    # CHARACTER (<c>)
    # ---------------------------------------------------------------
    def car(self, zone, glyph, block_parent, line_parent, seg_parent, glyph_id, glyphs_on_page):

        car_uuid = self._uuid()
        self.ids[("car", self.folio, block_parent, line_parent, seg_parent, glyph_id)] = car_uuid

        car_el = etree.SubElement(zone, "c", {
            "{http://www.w3.org/XML/1998/namespace}id": car_uuid
        })

        alto_glyph = self.root.find(
            f'.//a:Glyph[@ID="{glyph_id}"]',
            namespaces=NS_ALTO
        )
        if alto_glyph is not None:
            wc = alto_glyph.get("WC")
            if wc:
                cert_uuid = self._uuid()
                etree.SubElement(car_el, "certainty", {
                    "{http://www.w3.org/XML/1998/namespace}id": cert_uuid,
                    "locus": "value",
                    "degree": wc,
                    "target": f"#{car_uuid}"
                })

        car_el.text = glyph.attrib.get("CONTENT", "")
        return car_el
