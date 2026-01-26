# -----------------------------------------------------------
# Update TEI header langUsage with detected languages
# -----------------------------------------------------------
"""
TEI header langUsage updater.

Updates the <langUsage> element in the TEI header with all detected
languages and their usage percentages.
"""

from lxml import etree

from config import SUPPORTED_LANGUAGES, LANG_DEFAULT


def update_langusage(root, lang_stats):
    """
    Update the <langUsage> element in TEI header with detected languages.

    Replaces the existing <langUsage> content with <language> elements
    for each detected language, sorted by element count.

    Args:
        root (etree.Element): TEI root element.
        lang_stats (dict): Language usage statistics from LanguageDetector.get_stats().
                          Format: {"fra": 1049, "lat": 34, "deu": 28}

    Example output:
        <langUsage>
            <language ident="fra" usage="1049">French</language>
            <language ident="lat" usage="34">Latin</language>
            <language ident="deu" usage="28">German</language>
        </langUsage>
    """
    if not lang_stats:
        return

    # Find langUsage element
    langUsage = root.find(".//{http://www.tei-c.org/ns/1.0}langUsage")
    if langUsage is None:
        # Try without namespace
        langUsage = root.find(".//langUsage")

    if langUsage is None:
        # Create langUsage if it doesn't exist
        profileDesc = root.find(".//{http://www.tei-c.org/ns/1.0}profileDesc")
        if profileDesc is None:
            profileDesc = root.find(".//profileDesc")
        if profileDesc is None:
            teiHeader = root.find(".//{http://www.tei-c.org/ns/1.0}teiHeader")
            if teiHeader is None:
                teiHeader = root.find(".//teiHeader")
            if teiHeader is not None:
                profileDesc = etree.SubElement(teiHeader, "profileDesc")
        if profileDesc is not None:
            langUsage = etree.SubElement(profileDesc, "langUsage")

    if langUsage is None:
        return

    # Clear existing language elements
    for child in list(langUsage):
        langUsage.remove(child)

    # Build reverse lookup: TEI ident -> language name
    ident_to_name = {}
    for code, info in SUPPORTED_LANGUAGES.items():
        ident_to_name[info["ident"]] = info["name"]

    # Add default language name
    ident_to_name[LANG_DEFAULT] = "Undetermined"

    # Add language elements sorted by count (descending)
    for lang_ident, count in sorted(lang_stats.items(), key=lambda x: -x[1]):
        lang_el = etree.SubElement(langUsage, "language")
        lang_el.attrib["ident"] = lang_ident

        # Use count as usage value
        lang_el.attrib["usage"] = str(count)

        # Set language name as text content
        lang_name = ident_to_name.get(lang_ident, lang_ident)
        lang_el.text = lang_name
