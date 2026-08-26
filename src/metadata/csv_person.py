# -----------------------------------------------------------
# Person metadata loading module (csv_person)
# Loads person metadata from semicolon-delimited CSV files
# -----------------------------------------------------------
"""
Person metadata loading module.

This module provides functions to load and parse person metadata from
CSV files (metadata_personne.csv). The expected format is semicolon-delimited
with headers. Person identifiers follow the pattern PERSXXXX.
"""

import logging
from pathlib import Path
from collections import defaultdict

import pandas as pd

from config import CSV_DELIMITER

logger = logging.getLogger(__name__)


# Role mapping from CSV Label_categ to TEI role names
ROLE_MAPPING = {
    "AUT": "author",
    "LIBR": "bookseller",
    "EDIT": "editor",
    "TRAD": "translator",
}


class PersonDatabase:
    """
    Database of persons loaded from a CSV file.

    Provides lookup by PERSXXXX identifier and enrichment of metadata
    with detailed person information.

    Attributes:
        _persons (dict): Dictionary mapping person IDs to their data.
    """

    def __init__(self, csv_path=None):
        """
        Initialize the PersonDatabase.

        Args:
            csv_path (Path or str, optional): Path to the person CSV file.
        """
        self._persons = {}
        if csv_path:
            self.load(csv_path)

    def load(self, csv_path):
        """
        Load person data from a CSV file.

        Args:
            csv_path (Path or str): Path to the person metadata CSV file.

        Returns:
            bool: True if loading succeeded, False otherwise.
        """
        csv_path = Path(csv_path)
        if not csv_path.exists():
            return False

        try:
            df = pd.read_csv(csv_path, sep=CSV_DELIMITER)
            self._build_index(df)
            return True
        except Exception as e:
            logger.warning("Failed to read person metadata %s: %s", csv_path, e)
            return False

    def _build_index(self, df):
        """
        Build the person index from DataFrame.

        Args:
            df (pd.DataFrame): Person metadata DataFrame.
        """
        if "BDD" not in df.columns:
            return

        for _, row in df.iterrows():
            person_id = str(row.get("BDD", "")).strip()
            if not person_id or person_id.lower() == "nan":
                continue

            self._persons[person_id] = self._parse_person_row(row)

    def _parse_person_row(self, row):
        """
        Parse a single person row into a structured dictionary.

        Args:
            row (pd.Series): A row from the person DataFrame.

        Returns:
            dict: Structured person data.
        """
        def safe_val(key):
            val = row.get(key)
            if val is None or pd.isna(val):
                return None
            s = str(val).strip()
            if s == "" or s.lower() == "nan":
                return None
            # Clean numeric strings that may have .0 suffix (e.g., ISNI)
            if s.endswith(".0") and s[:-2].isdigit():
                s = s[:-2]
            return s

        def safe_roles(key):
            """Parse roles from Label_categ (can be AUT|LIBR)."""
            raw = safe_val(key)
            if not raw:
                return []
            return [ROLE_MAPPING.get(r.strip(), r.strip().lower())
                    for r in raw.split("|") if r.strip()]

        return {
            "id": safe_val("BDD"),
            "ark": safe_val("ARK"),
            "isni": safe_val("ISNI"),
            "roles": safe_roles("Label_categ"),
            "forename": safe_val("Prenoms"),
            "surname": safe_val("Nom"),
            "sex": safe_val("Sexe"),
            "role_name": safe_val("RoleName"),
            "gen_name": safe_val("GenName"),
            "nicknames": safe_val("Surnoms"),
            "birth_date": safe_val("Annee_naissance"),
            "birth_place_id": safe_val("ID_Ville_naissance"),
            "birth_place": safe_val("Ville_naissance"),
            "death_date": safe_val("Annee_mort"),
            "death_place_id": safe_val("ID_Ville_mort"),
            "death_place": safe_val("Ville_mort"),
            "confession": safe_val("Confession"),
            "formation": safe_val("Formation"),
            "professions": safe_val("Professions"),
            "portraits": safe_val("Portraits"),
            "oeuvre": safe_val("Oeuvre"),
            "milieux_reseaux": safe_val("Milieux_reseaux"),
            "contacts_artistes": safe_val("Contacts_artistes"),
            "fortune_critique": safe_val("Fortune_critique"),
            "publications": safe_val("Publications"),
            "citations": safe_val("Citations"),
            "bibliographie": safe_val("Bibliographie"),
            "webographie": safe_val("Webographie"),
            "note": safe_val("Notes"),
            "commentaires": safe_val("Commentaires"),
        }

    def get(self, person_id):
        """
        Get person data by identifier.

        Args:
            person_id (str): Person identifier (e.g., "PERS0001").

        Returns:
            dict or None: Person data or None if not found.
        """
        if not person_id:
            return None
        # Handle both "PERS0001" and possible ARK references
        person_id = str(person_id).strip()
        return self._persons.get(person_id)

    def get_display_name(self, person_id):
        """
        Get a formatted display name for a person.

        Args:
            person_id (str): Person identifier.

        Returns:
            str: Display name (e.g., "Jean Dupont") or the ID if not found.
        """
        person = self.get(person_id)
        if not person:
            return person_id

        parts = []
        if person.get("forename"):
            parts.append(person["forename"])
        if person.get("surname"):
            parts.append(person["surname"])

        return " ".join(parts) if parts else person_id

    def enrich_author_data(self, person_id, role=None):
        """
        Create an enriched author dictionary for TEI header.

        Args:
            person_id (str): Person identifier.
            role (str, optional): Role override (author, printer, editor, etc.).

        Returns:
            dict: Author data dictionary for TEI header.
        """
        person = self.get(person_id)

        if not person:
            return {
                "xmlid": person_id,
                "name": person_id,
                "forename": None,
                "surname": None,
                "namelink": None,
                "isni": None,
                "ark": None,
                "birth_date": None,
                "death_date": None,
                "role": role,
            }

        return {
            "xmlid": person.get("id") or person_id,
            "name": self.get_display_name(person_id),
            "forename": person.get("forename"),
            "surname": person.get("surname"),
            "namelink": person.get("gen_name"),
            "isni": person.get("isni"),
            "ark": person.get("ark"),
            "birth_date": person.get("birth_date"),
            "death_date": person.get("death_date"),
            "birth_place": person.get("birth_place"),
            "birth_place_id": person.get("birth_place_id"),
            "death_place": person.get("death_place"),
            "death_place_id": person.get("death_place_id"),
            "role": role or (person.get("roles")[0] if person.get("roles") else None),
            "note": person.get("note"),
        }

    def __contains__(self, person_id):
        """Check if a person ID exists in the database."""
        return person_id in self._persons

    def __iter__(self):
        """Iterate over person IDs in the database."""
        return iter(self._persons)

    def __len__(self):
        """Return the number of persons in the database."""
        return len(self._persons)


# Global person database instance
_person_db = None


def load_person_database(csv_path):
    """
    Load or get the global person database.

    Args:
        csv_path (Path or str): Path to the person CSV file.

    Returns:
        PersonDatabase: The loaded person database.
    """
    global _person_db
    if _person_db is None:
        _person_db = PersonDatabase(csv_path)
    return _person_db


def get_person_database():
    """
    Get the global person database.

    Returns:
        PersonDatabase or None: The person database if loaded.
    """
    return _person_db
