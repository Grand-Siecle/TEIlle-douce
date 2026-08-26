# -----------------------------------------------------------
# TEI Header Language Usage Builder
# -----------------------------------------------------------
"""
TEI Header language usage management.

This module provides the `LangUsageBuilder` class for constructing and updating
the `<langUsage>` element in TEI headers according to TEI P5 guidelines.

TEI P5 Reference:
    https://tei-c.org/release/doc/tei-p5-doc/en/html/ref-langUsage.html

The `<langUsage>` element describes the languages, sublanguages, registers,
dialects, etc. represented within a text. Each `<language>` child element
specifies a language used in the text with:
    - @ident: ISO 639 language code (required)
    - @usage: approximate percentage or count of usage (optional)
    - text content: human-readable language name (optional)

Example TEI output:
    <langUsage>
        <language ident="fra" usage="1716">French</language>
        <language ident="lat" usage="38">Latin</language>
        <language ident="grc" usage="7">Ancient Greek</language>
    </langUsage>
"""

from lxml import etree

from config import SUPPORTED_LANGUAGES, LANG_DEFAULT

# TEI namespace
NS_TEI = "http://www.tei-c.org/ns/1.0"


class LangUsageBuilder:
    """
    Builder for TEI `<langUsage>` elements.

    This class handles the creation and update of language usage information
    in TEI document headers based on detected language statistics.

    Attributes:
        root (etree.Element): TEI root element.
        lang_names (dict): Mapping of language idents to human-readable names.

    Example:
        >>> builder = LangUsageBuilder(tei_root)
        >>> builder.update({"fra": 1716, "lat": 38, "grc": 7})
    """

    def __init__(self, root):
        """
        Initialize the LangUsageBuilder.

        Args:
            root (etree.Element): TEI root element containing the header.
        """
        self.root = root
        self.lang_names = self._build_lang_names()

    def _build_lang_names(self):
        """
        Build mapping from language idents to human-readable names.

        Returns:
            dict: Mapping like {"fra": "French", "lat": "Latin", ...}
        """
        names = {}

        # Add names from supported languages config
        for code, info in SUPPORTED_LANGUAGES.items():
            names[info["ident"]] = info["name"]

        return names

    def _find_or_create_langusage(self):
        """
        Find or create the `<langUsage>` element in the TEI header.

        Searches for an existing `<langUsage>` element. If not found,
        creates one within `<profileDesc>` (creating that too if needed).

        Returns:
            etree.Element or None: The `<langUsage>` element, or None if
                                   the header structure cannot be created.
        """
        # Try to find existing langUsage (with or without namespace)
        langUsage = self.root.find(f".//{{{NS_TEI}}}langUsage")
        if langUsage is None:
            langUsage = self.root.find(".//langUsage")

        if langUsage is not None:
            return langUsage

        # Need to create langUsage - first find or create profileDesc
        profileDesc = self.root.find(f".//{{{NS_TEI}}}profileDesc")
        if profileDesc is None:
            profileDesc = self.root.find(".//profileDesc")

        if profileDesc is None:
            # Create profileDesc within teiHeader
            teiHeader = self.root.find(f".//{{{NS_TEI}}}teiHeader")
            if teiHeader is None:
                teiHeader = self.root.find(".//teiHeader")

            if teiHeader is None:
                return None

            profileDesc = etree.SubElement(teiHeader, "profileDesc")

        # Create langUsage within profileDesc
        return etree.SubElement(profileDesc, "langUsage")

    def _clear_langusage(self, langUsage):
        """
        Remove all existing `<language>` children from `<langUsage>`.

        Args:
            langUsage (etree.Element): The `<langUsage>` element to clear.
        """
        for child in list(langUsage):
            langUsage.remove(child)

    def _add_language(self, langUsage, ident, count):
        """
        Add a `<language>` element to `<langUsage>`.

        Args:
            langUsage (etree.Element): Parent `<langUsage>` element.
            ident (str): ISO 639 language code (e.g., "fra", "lat").
            count (int): Number of text elements in this language.
        """
        lang_el = etree.SubElement(langUsage, "language")
        lang_el.attrib["ident"] = ident
        lang_el.attrib["usage"] = str(count)
        lang_el.text = self.lang_names.get(ident, ident)

    def update(self, lang_stats):
        """
        Update the `<langUsage>` element with language statistics.

        Replaces any existing language declarations with new ones based
        on the provided statistics. Languages are sorted by usage count
        in descending order.

        Args:
            lang_stats (dict): Language statistics from LanguageDetector.
                              Format: {"fra": 1716, "lat": 38, "grc": 7}

        Returns:
            bool: True if update succeeded, False if header structure
                  could not be found or created.

        Example:
            >>> builder.update({"fra": 1716, "lat": 38})
            True
        """
        if not lang_stats:
            return False

        langUsage = self._find_or_create_langusage()
        if langUsage is None:
            return False

        # Clear existing and add new language elements
        self._clear_langusage(langUsage)

        # Sort by count descending and add each language
        for ident, count in sorted(lang_stats.items(), key=lambda x: -x[1]):
            self._add_language(langUsage, ident, count)

        return True


def build_langusage(root, lang_stats):
    """
    Build or update the `<langUsage>` element in a TEI document.

    Convenience function that wraps `LangUsageBuilder`. For simple use cases
    where you just need to update language statistics once.

    Args:
        root (etree.Element): TEI root element.
        lang_stats (dict): Language statistics from LanguageDetector.
                          Format: {"fra": 1716, "lat": 38, "grc": 7}

    Returns:
        bool: True if update succeeded, False otherwise.

    Example:
        >>> from src.lang import build_langusage
        >>> build_langusage(tei_root, {"fra": 100, "lat": 10})
        True
    """
    builder = LangUsageBuilder(root)
    return builder.update(lang_stats)
