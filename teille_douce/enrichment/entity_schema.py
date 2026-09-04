# -----------------------------------------------------------
# What the pipeline encodes, and under which name.
# -----------------------------------------------------------
"""
Encoding schema of the named-entity phase.

Not user settings, though they lived in config.py (audit 4.10): the code
depends on these tables structurally. Renaming a key here changes which
element the pipeline emits, which list receives it, and which lookup
resolves a @ref back — src/enrichment/ner_align.py and ner_resolve.py
read every field. A reader opening config.py to change an output
directory should not have to step over a hundred lines of TEI mapping,
and a reader changing the mapping should know they are editing code, not
configuration.

The vocabularies are declared in the header when a document actually
uses them (see ner_resolve._declare_vocabulary).
"""

# Vocabulaires que le header declare pour les listes sans element TEI
# dedie. Ouverts : ce sont les termes qu'un modele a releves dans le
# texte, pas une nomenclature fermee — le dire evite qu'un lecteur prenne
# <list type="materials"> pour un referentiel controle (audit 1.9).
NER_VOCABULARIES = {
    "art-vocabulary": {
        "label": "Vocabulaire des oeuvres relevé dans le texte",
        "categories": {
            "materials": "Matériaux, tels que nommés par le texte "
                         "(vocabulaire ouvert, issu de l'inférence)",
            "techniques": "Techniques artistiques, telles que nommées par "
                          "le texte (vocabulaire ouvert, issu de l'inférence)",
        },
    },
}


# Entity types to detect — add/remove entries to customize
# Each key maps to a TEI annotation strategy.
#
# Toutes les listes produites par le NER vivent dans <standOff>, pas
# dans le profileDesc (audit 1.9) : <particDesc> et <settingDesc>
# decrivent ce que l'editeur affirme du texte, et y melanger une liste
# inferee par un modele rendait les deux indistinguables — le header
# portait deja deux <listPerson>, l'une curee depuis le CSV, l'autre
# devinee. Le standOff est l'endroit TEI de l'annotation detachee.
NER_ENTITY_TYPES = {
    "person": {
        "tei_element": "persName",
        "tei_list": "listPerson",
        "tei_item": "person",
        "tei_parent": "standOff",
        "gliner_label": "person name",
        "camembert_label": "PER",
        "csv_file": "entities_persons.csv",
    },
    "place": {
        "tei_element": "placeName",
        "tei_list": "listPlace",
        "tei_item": "place",
        "tei_parent": "standOff",
        "gliner_label": "place name",
        "camembert_label": "LOC",
        "csv_file": "entities_places.csv",
    },
    "organization": {
        "tei_element": "orgName",
        "tei_list": "listOrg",
        "tei_item": "org",
        "tei_parent": "standOff",
        "gliner_label": "organization",
        "camembert_label": "ORG",
        "csv_file": "entities_orgs.csv",
    },
    "date": {
        "tei_element": "date",
        # Lire la valeur machine quand le texte la porte clairement
        # ("1659", "M.DC.LIX") : sans @when, une <date> ne se trie pas,
        # ne se filtre pas et ne se place sur aucune frise (audit 1.10).
        "normalize": "date",
        "tei_list": None,
        "tei_item": None,
        "tei_parent": None,
        "gliner_label": "date",
        "camembert_label": "DATE",
        "csv_file": None,
    },
    "artwork": {
        "tei_element": "objectName",
        "tei_list": "listObject",
        "tei_item": "object",
        # TEI requires the name to sit inside an <objectIdentifier>;
        # emitting <objectName> directly under <object> made every file
        # carrying an artwork entity fail tei_all validation.
        "tei_item_wrapper": "objectIdentifier",
        "tei_parent": "standOff",
        "gliner_label": "artwork",
        "camembert_label": None,
        "csv_file": "entities_artworks.csv",
    },
    "literary_work": {
        "tei_element": "title",
        "tei_list": "listBibl",
        "tei_item": "bibl",
        "tei_parent": "standOff",
        "gliner_label": "literary work",
        "camembert_label": None,
        "csv_file": "entities_works.csv",
    },
    "material": {
        "tei_element": "material",
        # Sans liste cible, une annotation <material> ne pointait vers
        # rien : deux occurrences de « marbre » restaient deux chaines
        # sans lien, et rien ne permettait de compter les materiaux d'un
        # corpus (audit 1.9). TEI n'a pas de <listMaterial> : une <list
        # type="materials"> dans le standOff en tient lieu.
        "tei_list": "list",
        "tei_list_attrs": {"type": "materials", "ana": "#materials"},
        "tei_item": "item",
        "tei_parent": "standOff",
        "gliner_label": "material",
        "camembert_label": None,
        "csv_file": "entities_materials.csv",
    },
    "technique": {
        "tei_element": "rs",
        "tei_list": "list",
        "tei_list_attrs": {"type": "techniques", "ana": "#techniques"},
        "tei_item": "item",
        "tei_parent": "standOff",
        "tei_element_attrs": {"type": "technique"},
        "gliner_label": "artistic technique",
        "camembert_label": None,
        "csv_file": "entities_techniques.csv",
    },
    "event": {
        "tei_element": "rs",
        "tei_element_attrs": {"type": "event"},
        "tei_list": "listEvent",
        "tei_item": "event",
        "tei_parent": "standOff",
        "gliner_label": "historical event",
        "camembert_label": None,
        "csv_file": "entities_events.csv",
    },
}

