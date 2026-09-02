# Run: venv/bin/python -m pytest tests/test_dates.py -q
"""
Lecture des dates : ce qui peut etre affirme, et ce qui doit rester
sans attribut.

Le corpus n'ecrit pas de dates ISO. Emettre un @when que la source ne
soutient pas est pire que n'en emettre aucun : chaque cas ci-dessous
verifie de quel cote de cette ligne tombe une valeur.
"""
import pytest

from src.dates import date_attributes, roman_year, text_date_attributes


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


def test_a_truncated_year_in_the_text_stays_a_span():
    """Meme regle que pour une cellule : « 159 » est une decennie, et le
    dit avec @cert."""
    assert text_date_attributes("159") == {
        "notBefore": "1590", "notAfter": "1599", "cert": "low",
    }


def test_the_csv_parser_and_the_text_parser_agree_on_a_plain_year():
    assert text_date_attributes("1659") == date_attributes("1659")
