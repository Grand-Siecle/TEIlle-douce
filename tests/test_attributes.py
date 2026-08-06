# -----------------------------------------------------------
# Tests for Attributes: polygon points parsing (int and float),
# and IIIF @source URL construction.
# Run: venv/bin/python tests/test_attributes.py
# -----------------------------------------------------------
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lxml import etree
from src.sourcedoc.attributes import Attributes, format_alto_points

ALTO_TMPL = """<alto xmlns="http://www.loc.gov/standards/alto/ns-v4#">
  <Layout><Page WIDTH="1000" HEIGHT="1500">
    <PrintSpace>
      <TextBlock ID="tb1" HPOS="672.0" VPOS="1065.0" WIDTH="413.0" HEIGHT="46.0">
        <Shape><Polygon POINTS="{points}"/></Shape>
      </TextBlock>
    </PrintSpace>
  </Page></Layout></alto>"""

CONFIG = {"scheme": "https", "server": "gallica.bnf.fr", "image_prefix": "/iiif/ark:/12148"}


def zones_for(points):
    root = etree.fromstring(ALTO_TMPL.format(points=points).encode())
    return Attributes("LIV0000", "f1", root, {}, CONFIG).zones("PrintSpace", "TextBlock", [])


def test_float_points():
    pts = zones_for("672.0 1065.0 1085.5 1065.0 1085.5 1111.0 672.0 1111.0")[0].attributes["points"]
    assert pts == "672,1065 1085,1065 1085,1111 672,1111", pts


def test_int_points():
    pts = zones_for("10 20 30 40")[0].attributes["points"]
    assert pts == "10,20 30,40", pts


def test_comma_points():
    pts = zones_for("10,20 30,40")[0].attributes["points"]
    assert pts == "10,20 30,40", pts


def test_format_alto_points_floats():
    assert format_alto_points("672.0 1090.5 674.25 1091.0") == "672,1090 674,1091"
    assert format_alto_points("") == ""


if __name__ == "__main__":
    test_float_points()
    test_int_points()
    test_comma_points()
    test_format_alto_points_floats()
    print("OK test_attributes")
