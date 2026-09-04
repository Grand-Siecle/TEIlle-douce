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

from teille_douce.config import SUPPORTED_LANGUAGES, LANG_DEFAULT
from ..volumetry import language_volume

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
        Empty `<langUsage>` of every child before the measured languages
        are posted.

        Every child, not only the `<language>` elements the docstring
        used to promise: TEI gives `<langUsage>` a content model by
        choice, (model.pLike+ | language+), so anything surviving next
        to the `<language>` elements would make the document invalid.
        Prose about the languages belongs in `<encodingDesc>`, where
        src/teiheader/default.py writes it.

        Args:
            langUsage (etree.Element): The `<langUsage>` element to clear.
        """
        for child in list(langUsage):
            langUsage.remove(child)

    def _add_language(self, langUsage, ident, count, total, unit="words"):
        """
        Add a `<language>` element to `<langUsage>`.

        @usage is a PERCENTAGE in TEI ("the approximate percentage, by
        volume, of the text which uses this language"), and we were
        writing raw container counts: a reader — or any tool summing the
        attribute — read usage="1716" as 1716 %. The measured quantity
        is kept in @n, where an arbitrary label is allowed, with the
        unit it was counted in — because "by volume" is a claim about
        words, and a count of containers cannot support it.

        The datatype is nonNegativeInteger, so the percentage is rounded;
        a language present in the document never rounds down to 0, which
        would declare it absent while its <foreign> segments sit in the
        text. Rounding up the rare languages can push the total to 101 %
        — TEI asks for an approximate share, and "1 % of Greek" is a
        truer statement than "0 % of a language that is there".

        Args:
            langUsage (etree.Element): Parent `<langUsage>` element.
            ident (str): ISO 639 language code (e.g., "fra", "lat").
            count (int): Number of text containers in this language.
            total (int): Total across all languages, i.e. the 100 %.
        """
        share = round(100 * count / total) if total else 0
        if count and share < 1:
            share = 1
        lang_el = etree.SubElement(langUsage, "language")
        lang_el.attrib["ident"] = ident
        lang_el.attrib["usage"] = str(share)
        lang_el.attrib["n"] = f"{count} {unit}"
        lang_el.text = self.lang_names.get(ident, ident)

    def update(self, lang_stats):
        """
        Update the `<langUsage>` element with language statistics.

        Replaces any existing language declarations with new ones based
        on the provided statistics. Languages are sorted by usage count
        in descending order.

        The share is measured on the built text, in words. The
        detector's own statistics cannot support a percentage: they
        count ONE unit per text container for the primary language and
        one per <foreign> SEGMENT for the others, so a French page
        quoting thirty short Latin passages counts 1 against 30 — and
        would be published as 97 % Latin. Words are the volume TEI asks
        about, so words are what is counted (src/volumetry.py).

        *lang_stats* remains the fallback for a tree with no xml:lang at
        all — a run with language detection disabled — where the share
        is by container for want of anything better, and @n says so.

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
        volume = language_volume(self.root)
        stats, unit = (volume, "words") if volume else (lang_stats, "containers")
        if not stats:
            return False

        langUsage = self._find_or_create_langusage()
        if langUsage is None:
            return False

        # Clear existing and add new language elements
        self._clear_langusage(langUsage)

        # Sort by volume descending and add each language
        total = sum(stats.values())
        for ident, count in sorted(stats.items(), key=lambda x: -x[1]):
            self._add_language(langUsage, ident, count, total, unit)

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
        >>> from teille_douce.lang import build_langusage
        >>> build_langusage(tei_root, {"fra": 100, "lat": 10})
        True
    """
    builder = LangUsageBuilder(root)
    return builder.update(lang_stats)
