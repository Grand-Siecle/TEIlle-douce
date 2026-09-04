# Run: venv/bin/python -m pytest tests/test_dates.py -q
"""
Lecture des dates : ce qui peut etre affirme, et ce qui doit rester
sans attribut.

Le corpus n'ecrit pas de dates ISO. Emettre un @when que la source ne
soutient pas est pire que n'en emettre aucun : chaque cas ci-dessous
verifie de quel cote de cette ligne tombe une valeur.
"""
import pytest

from teille_douce.dates import date_attributes, roman_year, text_date_attributes


# =============================================================================
# Chiffres romains — la forme imprimee sur les pages de titre
# =============================================================================

@pytest.mark.parametrize("texte, annee", [
    ("M.DC.LIX", 1659),
    ("MDCXLVIII", 1648),
    ("M. DC. XL.", 1640),
    ("MCDXCII", 1492),
])
def test_roman_year_reads_the_printed_forms(texte, annee):
    assert roman_year(texte) == annee


@pytest.mark.parametrize("texte", [
    "MIL",        # un mot francais en capitales, pas un nombre
    "MDCLIIII",   # forme non canonique : deviner serait affirmer
    "LIVRE",
    "",
    "1659",
])
def test_roman_year_refuses_what_it_cannot_certify(texte):
    assert roman_year(texte) is None


# =============================================================================
# Dates lues dans le texte
# =============================================================================

@pytest.mark.parametrize("texte, attendu", [
    ("1659", {"when": "1659"}),
    ("M.DC.LIX", {"when": "1659"}),
    ("l'an 1659", {"when": "1659"}),
    ("en 1659.", {"when": "1659"}),
    ("(1659)", {"when": "1659"}),
])
def test_a_plain_date_in_the_text_gets_its_value(texte, attendu):
    assert text_date_attributes(texte) == attendu


@pytest.mark.parametrize("texte", [
    "le 23 juin 1652",   # une date qu'un humain lit, pas cette fonction
    "seiziesme siecle",
    "vers la fin",
    "",
    None,
])
def test_the_text_keeps_what_cannot_be_read_as_a_value(texte):
    """Prendre les chiffres de « le 23 juin 1652 » comme on prend ceux
    d'une cellule de CSV donnerait l'annee 2316. Aucun attribut est la
    reponse honnete : le texte reste, et dit ce qu'il dit."""
    assert text_date_attributes(texte) == {}


def test_a_bare_three_digit_run_in_the_text_is_not_a_date():
    """Dans du texte courant, « 159 » est bien plus souvent une page, un
    folio ou un article qu'une annee tronquee. Une cellule de CSV est un
    champ date et se lit comme tel ; un passage de texte ne l'est que
    s'il le dit clairement."""
    assert text_date_attributes("159") == {}
    # la meme valeur dans une cellule reste une decennie
    assert date_attributes("159") == {
        "notBefore": "1590", "notAfter": "1599", "cert": "low",
    }


def test_an_approximation_marker_is_read_the_same_way_by_both():
    """« vers 1650 » ne peut pas etre une estimation dans une cellule et
    une certitude dans le texte."""
    assert text_date_attributes("vers 1650") == date_attributes("vers 1650")
    assert text_date_attributes("vers 1650")["cert"] == "low"


@pytest.mark.parametrize("cellule", [
    "environ 1650",   # \benv\b ne peut pas matcher « environ »
    "env. 1650",
    "vers 1650",
    "ca. 1650",
    "circa 1650",
    "? 1650",
])
def test_every_circa_marker_the_stripper_knows_is_read_as_an_estimate(cellule):
    """Le detecteur d'incertitude et la regex qui retire les mots
    d'introduction doivent connaitre les memes marqueurs : « environ
    1650 » etait retire cote texte et lu comme une date exacte cote
    attribut, publiant une approximation comme une certitude."""
    assert date_attributes(cellule) == {"when": "1650", "cert": "low"}
    # et les deux lecteurs (cellule de CSV, date lue dans le texte)
    # partagent ce verdict : _is_uncertain les sert tous les deux
    assert text_date_attributes(cellule) == {"when": "1650", "cert": "low"}


def test_a_lowercase_word_is_never_read_as_a_roman_year():
    """« dix » vaut 509 en chiffres romains si l'on plie la casse."""
    for mot in ("dix", "vi", "ci", "li"):
        assert roman_year(mot) is None
        assert text_date_attributes(f"l'an {mot}") == {}


def test_year_zero_is_not_a_date():
    """XSD 1.0 n'a pas d'annee zero : l'ecrire fait echouer tei_all,
    exactement ce que ce controle de forme evite. « 0 » et « 0000 » sont
    des remplissages d'inconnu."""
    for cellule in ("0", "00", "0000"):
        assert date_attributes(cellule) == {}


def test_a_bce_span_reads_in_the_right_order():
    """-1599 est ANTERIEUR a -1590 : garder l'ordre des dates de notre
    ere affirmerait un intervalle qu'aucun instant ne satisfait."""
    attrs = date_attributes("-159")
    assert (attrs["notBefore"], attrs["notAfter"]) == ("-1599", "-1590")


def test_the_csv_parser_and_the_text_parser_agree_on_a_plain_year():
    assert text_date_attributes("1659") == date_attributes("1659")
