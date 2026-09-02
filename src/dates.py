# -----------------------------------------------------------
# Reading the dates this corpus actually writes.
# -----------------------------------------------------------
"""
Dates, from catalogue cells and from the text itself.

TEI wants @when in a W3C form (teidata.temporal.w3c); an early-modern
corpus writes almost anything else — truncated years, compact digit
runs, "? 1666", "M.DC.LIX" on a title page. Every function here answers
the same question: what can be asserted from this string, and what has
to stay unasserted? Emitting a date the source does not support is worse
than emitting none, so the shape of the value decides the attributes it
earns, and anything unreadable earns nothing.

Shared by the metadata layer (CSV cells) and the NER layer (date
entities found in the text), which must not read the same year in two
different ways.
"""

import logging
import re

logger = logging.getLogger(__name__)

def _is_uncertain(text):
    """True when the value announces itself as an estimate.

    "?" anywhere, or a circa marker: historical catalogues write
    "? 1666", "circa 1600", "vers 1650". Shared by both readers — the
    same marker cannot mean an estimate in a CSV cell and a certainty in
    the text.
    """
    return bool(re.search(r"\?|\b(ca|circa|vers|env)\b\.?", str(text or ""), re.I))


def date_attributes(raw):
    """
    TEI date attributes for one CSV date cell.

    The corpus does not hold ISO dates. Measured on the real
    metadata_personne.csv: 126 of 327 birth values and 6 of 103 death
    values are something else — truncated years ("15" = 15xx, "159" =
    159x), compact digit runs ("16520623", "169109"), uncertain values
    ("? 1666"). Writing any of those into @when produced TEI that fails
    tei_all (teidata.temporal.w3c wants an xsd date or gYear), so the
    cell's *shape* decides which attributes it earns:

    - 1652-06-23 / 1652/06/23 / 16520623  -> @when="1652-06-23"
    - 165206 / 1652-06                    -> @when="1652-06"
    - 1652                                -> @when="1652"
    - 165 / 16                            -> @notBefore/@notAfter (the
                                             decade or century it spans)
    - "? 1666"                            -> @when + @cert="low"
    - anything else                       -> no attribute (the caller
                                             keeps the raw text instead
                                             of asserting a false date)

    Returns:
        dict: attributes to set, possibly empty.
    """
    if not raw:
        return {}
    text = str(raw).strip()
    if not text:
        return {}

    uncertain = _is_uncertain(text)
    bce = text.lstrip("? ").startswith("-")
    digits = re.sub(r"\D", "", text)
    sign = "-" if bce else ""

    def with_cert(attrs, cert=None):
        if uncertain:
            attrs["cert"] = "low"
        elif cert:
            attrs["cert"] = cert
        return attrs

    def year(value):
        # TEI wants a four-digit year, BCE years included (Ovid's
        # "-430320" is 20 March 43 BCE -> -0043-03-20).
        return f"{sign}{int(value):04d}"

    if len(digits) in (6, 8) or (bce and 5 <= len(digits) <= 8):
        # A BCE year is not zero-padded in the source ("-430320" is
        # 43 BCE, not 4303), so a compact run is split from the RIGHT:
        # day, month, then whatever remains is the year.
        if bce:
            year_digits, month, day = digits[:-4], int(digits[-4:-2]), int(digits[-2:])
        else:
            year_digits, month = digits[:4], int(digits[4:6])
            day = int(digits[6:]) if len(digits) == 8 else 1
        if year_digits and 1 <= month <= 12 and 1 <= day <= 31:
            when = f"{year(year_digits)}-{month:02d}"
            if len(digits) == 8 or bce:
                when += f"-{day:02d}"
            return with_cert({"when": when})
        # Not a date after all (an impossible month or day): fall through
        # to the unusable branch rather than assert a wrong one.
        logger.warning("Unusable date %r — impossible month or day", text)
        return {}
    if int(digits or 0) == 0:
        # "0", "00", "0000" : un remplissage d'inconnu, pas une date. XSD
        # 1.0 n'a pas d'annee zero, et l'ecrire fait echouer tei_all —
        # exactement ce que ce controle de forme existe pour eviter.
        logger.warning("Unusable date %r — year zero is not a date", text)
        return {}
    if len(digits) == 4:
        return with_cert({"when": year(digits)})
    if 1 <= len(digits) <= 3:
        # A truncated year is a span, not a date: in this early-modern
        # corpus "15" means the 1500s and "159" the 1590s. It IS an
        # inference — @cert="low" says so — and it is wrong for the rare
        # ancient author (Ovid's death year "17" is 17 CE, not the
        # 1700s): such rows are better fixed in the CSV.
        span = 10 ** (4 - len(digits))
        start = int(digits) * span
        first, last = start, start + span - 1
        if bce:
            # -1599 is EARLIER than -1590: keeping the CE order would
            # assert an interval no instant can satisfy.
            first, last = last, first
        return with_cert(
            {"notBefore": f"{sign}{first:04d}", "notAfter": f"{sign}{last:04d}"},
            cert="low",
        )

    logger.warning("Unusable date %r — no TEI date attribute emitted", text)
    return {}


def normalize_date(date_str):
    """ISO form of a date cell, or "" when it has none."""
    return date_attributes(date_str).get("when", "")

# Canonical Roman numeral, the form printed on title pages
# ("M.DC.LIX"). Non-canonical spellings (MDCLIIII for 1654) are
# deliberately NOT matched: guessing at them would assert a date the
# printer wrote in a way we did not verify.
_ROMAN_RE = re.compile(
    r"^M{0,4}(CM|CD|D?C{0,3})(XC|XL|L?X{0,3})(IX|IV|V?I{0,3})$"
)
_ROMAN_VALUES = {"I": 1, "V": 5, "X": 10, "L": 50, "C": 100, "D": 500, "M": 1000}

# Words a date is introduced by, in French and in Latin, that carry no
# value of their own.
# Words a date is introduced by, in French and in Latin, that carry no
# value of their own. The circa markers are stripped for the SHAPE test
# only: the original string is what goes to date_attributes, so "vers
# 1650" still comes back as an estimate.
_DATE_PREFIX_RE = re.compile(
    r"^[?\s]*(l\s*['\u2019]\s*an|en|le|la|anno|aetatis|vers|circa|ca|env(iron)?)"
    r"\b[\s.]*", re.I
)

# Un millesime imprime est un nombre a quatre chiffres. En dessous, une
# suite de lettres romaines est presque toujours autre chose : DIX vaut
# 509, VI vaut 6, CI vaut 101 — des mots, des initiales, une numerotation.
_ROMAN_MIN_YEAR = 1000


def roman_year(text):
    """
    Year written as a Roman numeral, or None.

    Reads what a title page prints — capitals — and nothing else. Folding
    case would turn ordinary words into years: "dix" is 509, "vi" is 6,
    "ci" is 101, and a stray capital "M" is 1000. A printed millesime is
    a four-figure number, so anything under 1000 is refused as well.

    Returns:
        int or None: the year, when *text* is a canonical Roman numeral
        a printed date can plausibly hold.
    """
    letters = re.sub(r"[.\s]", "", str(text))
    if not letters or letters != letters.upper() or not _ROMAN_RE.match(letters):
        return None
    total = 0
    for i, char in enumerate(letters):
        value = _ROMAN_VALUES[char]
        following = (_ROMAN_VALUES[c] for c in letters[i + 1:])
        total += -value if any(v > value for v in following) else value
    return total if _ROMAN_MIN_YEAR <= total <= 2100 else None


def text_date_attributes(text):
    """
    TEI date attributes for a date READ IN THE TEXT, or {}.

    Deliberately narrower than :func:`date_attributes`: a catalogue cell
    is a date field and can be read as one, while a span of running text
    is only a date if it says so plainly. "1659" and "M.DC.LIX" are
    plain; "le 23 juin 1652" is a date a human reads and this function
    does not, because taking its digits apart the way a CSV cell is taken
    apart would read it as the year 2316. No attribute is the honest
    answer there — the text stays, and says what it says.

    Returns:
        dict: attributes to set, possibly empty.
    """
    if not text:
        return {}
    cleaned = str(text).strip().strip(".,;:()[]«»?\"' \t\n")
    cleaned = _DATE_PREFIX_RE.sub("", cleaned).strip()
    if not cleaned:
        return {}

    year = roman_year(cleaned)
    if year is not None:
        attrs = {"when": f"{year:04d}"}
        if _is_uncertain(text):
            attrs["cert"] = "low"
        return attrs

    # Four figures, not three: in running text a bare "159" is a page, a
    # folio, an article or a paragraph far more often than a truncated
    # year, and a span of text is a date only when it says so plainly.
    # The original string goes back to date_attributes so its circa
    # markers ("vers 1650") are read there rather than stripped here.
    if re.fullmatch(r"-?\d{4}", cleaned):
        return date_attributes(text)

    return {}
