# -----------------------------------------------------------
# Phase 9: Entity resolution, Wikidata enrichment, CSV output,
#           TEI header injection, and @ref linking.
# -----------------------------------------------------------
"""
NER resolution pipeline (Phase 9).

Deduplicates entity mentions, links to local metadata (metadata_personne.csv),
enriches via Wikidata, writes entity CSVs, injects entity lists into the TEI
header, and adds @ref attributes to body annotations.
"""

import asyncio
import csv
import logging
import unicodedata
import uuid
from dataclasses import dataclass, field
from pathlib import Path

from lxml import etree

from ..constants import NS_TEI, NS_XML
from ..utils.xml import local_tag as _local

logger = logging.getLogger(__name__)

TEI_NS = f"{{{NS_TEI}}}"
XML_ID = f"{{{NS_XML}}}id"


# =============================================================================
# DATACLASSES
# =============================================================================


@dataclass
class ResolvedEntity:
    """An entity resolved with identifiers and external metadata."""

    entity_type: str
    canonical_name: str
    xml_id: str  # e.g. "pers-550e8400-..."
    mentions: list  # list of AlignedEntity
    local_match: str = None  # PERSXXXX from metadata_personne.csv
    wikidata_id: str = None  # e.g. "Q37577"
    wikidata_label: str = None
    wikidata_data: dict = field(default_factory=dict)


# =============================================================================
# XML ID GENERATION
# =============================================================================

_TYPE_PREFIX = {
    "person": "pers",
    "place": "place",
    "organization": "org",
    "date": "date",
    "artwork": "artwork",
    "literary_work": "work",
    "material": "mat",
    "technique": "tech",
    "event": "event",
}


def _make_xml_id(entity_type):
    """Generate an NCName-compliant xml:id."""
    prefix = _TYPE_PREFIX.get(entity_type, "ent")
    uid = str(uuid.uuid4())
    return f"{prefix}-{uid}"


# =============================================================================
# TEXT NORMALIZATION
# =============================================================================


def _normalize(text):
    """Normalize text for fuzzy matching: lowercase, strip accents, collapse whitespace."""
    text = text.lower().strip()
    # Decompose unicode, remove combining marks (accents)
    nfkd = unicodedata.normalize("NFKD", text)
    stripped = "".join(c for c in nfkd if not unicodedata.combining(c))
    # Collapse whitespace
    return " ".join(stripped.split())


# =============================================================================
# MENTION GROUPING
# =============================================================================


def _get_canonical_name(entity):
    """Extract canonical form from an AlignedEntity."""
    if entity.w_elements:
        # Use lemma attributes from <w> elements
        lemmas = []
        for w in entity.w_elements:
            lemma = w.get("lemma")
            if lemma:
                lemmas.append(lemma)
            else:
                lemmas.append(w.text or "")
        return " ".join(lemmas)
    return entity.text


def group_mentions(aligned_entities):
    """
    Group entity mentions by (type, canonical form).

    Uses fuzzy matching to group spelling variants of the same entity.

    Args:
        aligned_entities: List of AlignedEntity from Phase 8.

    Returns:
        list[ResolvedEntity]: Deduplicated entities with their mentions.
    """
    groups = {}  # (type, normalized_name) → list of AlignedEntity

    for ent in aligned_entities:
        canonical = _get_canonical_name(ent)
        norm = _normalize(canonical)
        key = (ent.entity_type, norm)

        if key not in groups:
            groups[key] = {
                "canonical": canonical,
                "type": ent.entity_type,
                "mentions": [],
                "best_confidence": 0.0,
            }
        groups[key]["mentions"].append(ent)
        if ent.confidence > groups[key]["best_confidence"]:
            groups[key]["best_confidence"] = ent.confidence
            groups[key]["canonical"] = canonical  # use highest-confidence form

    resolved = []
    for grp in groups.values():
        etype = grp["type"]
        resolved.append(
            ResolvedEntity(
                entity_type=etype,
                canonical_name=grp["canonical"],
                xml_id=_make_xml_id(etype),
                mentions=grp["mentions"],
            )
        )

    logger.info(
        "NER: grouped %d mentions into %d unique entities",
        len(aligned_entities),
        len(resolved),
    )
    return resolved


# =============================================================================
# LOCAL LINKING (metadata_personne.csv)
# =============================================================================


def link_local(entities, person_db):
    """
    Link person entities against the local PersonDatabase.

    Matches by normalized surname/forename comparison.

    Args:
        entities: List of ResolvedEntity.
        person_db: PersonDatabase instance or None.
    """
    if not person_db or len(person_db) == 0:
        return

    linked = 0
    for ent in entities:
        if ent.entity_type != "person":
            continue

        norm_name = _normalize(ent.canonical_name)

        # Try matching against all persons in the database
        for pid in person_db:
            person = person_db.get(pid)
            if not person:
                continue

            surname = _normalize(person.get("surname") or "")
            forename = _normalize(person.get("forename") or "")
            full = f"{forename} {surname}".strip()

            if not surname:
                continue

            # Match: full name, surname only, or surname as whole word in canonical
            surname_words = surname.split()
            name_words = norm_name.split()
            surname_in_name = all(sw in name_words for sw in surname_words) if surname_words else False
            if norm_name == full or norm_name == surname or surname_in_name:
                ent.local_match = pid
                linked += 1
                logger.debug(
                    "Local link: '%s' → %s (%s %s)",
                    ent.canonical_name,
                    pid,
                    person.get("forename", ""),
                    person.get("surname", ""),
                )
                break

    if linked:
        logger.info("NER: linked %d entities to local person database", linked)


# =============================================================================
# WIKIDATA ENRICHMENT
# =============================================================================


def resolve_wikidata(entities, entity_types_config, min_confidence, max_rps, timeout):
    """
    Enrich entities with Wikidata identifiers and properties.

    Runs async internally but presents a sync interface.

    Args:
        entities: List of ResolvedEntity.
        entity_types_config: NER_ENTITY_TYPES from config.
        min_confidence: Minimum confidence for Wikidata lookup.
        max_rps: Maximum requests per second.
        timeout: HTTP timeout in seconds.
    """
    # Filter entities eligible for Wikidata
    eligible = []
    for ent in entities:
        cfg = entity_types_config.get(ent.entity_type, {})
        if not cfg.get("wikidata_lookup", False):
            continue
        best_conf = max((m.confidence for m in ent.mentions), default=0)
        if best_conf < min_confidence:
            continue
        eligible.append(ent)

    if not eligible:
        logger.info("NER: no entities eligible for Wikidata lookup")
        return

    logger.info("NER: resolving %d entities against Wikidata", len(eligible))

    try:
        asyncio.run(_resolve_wikidata_async(eligible, max_rps, timeout))
    except Exception as e:
        logger.warning("Wikidata resolution failed: %s", e)


async def _resolve_one_entity(client, ent, semaphore):
    """Resolve a single entity against Wikidata."""
    async with semaphore:
        try:
            qid = None

            # Try identifier-based lookup if local match has ISNI/ARK
            if ent.local_match:
                from ..metadata.csv_person import get_person_database

                pdb = get_person_database()
                if pdb:
                    person = pdb.get(ent.local_match)
                    if person:
                        isni = person.get("isni")
                        if isni:
                            qid = await client.search_by_identifier("P213", isni)
                        if not qid:
                            ark = person.get("ark")
                            if ark:
                                qid = await client.search_by_identifier("P268", ark)

            # Fall back to name search
            if not qid:
                qid = await client.search_entity(
                    ent.canonical_name, ent.entity_type
                )

            if not qid:
                return False

            # Fetch properties
            props = await client.fetch_properties(qid, ent.entity_type)
            ent.wikidata_id = qid
            ent.wikidata_label = props.get("label", "")
            ent.wikidata_data = props
            return True

        except Exception as e:
            logger.debug("Wikidata failed for '%s': %s", ent.canonical_name, e)
            return False


async def _resolve_wikidata_async(entities, max_rps, timeout):
    """Async Wikidata resolution with concurrent requests."""
    from ..utils.wikidata import WikidataClient

    semaphore = asyncio.Semaphore(max_rps)

    async with WikidataClient(max_rps=max_rps, timeout=timeout) as client:
        results = await asyncio.gather(
            *(_resolve_one_entity(client, ent, semaphore) for ent in entities)
        )

    resolved = sum(1 for r in results if r)
    logger.info("NER: resolved %d entities via Wikidata", resolved)


# =============================================================================
# CSV OUTPUT
# =============================================================================

# Column definitions per entity type
_CSV_COLUMNS = {
    "person": [
        "xml_id", "canonical_name", "mention_count", "local_id",
        "wikidata_id", "wikidata_label", "birth_date", "death_date",
        "birth_place", "death_place", "profession", "movement",
        "master", "notable_work", "isni", "viaf", "bnf", "ulan",
    ],
    "place": [
        "xml_id", "canonical_name", "mention_count",
        "wikidata_id", "wikidata_label", "country", "instance_of",
        "coordinates", "geonames", "tgn",
    ],
    "organization": [
        "xml_id", "canonical_name", "mention_count",
        "wikidata_id", "wikidata_label", "foundation_date", "founder",
        "headquarters", "instance_of",
    ],
    "artwork": [
        "xml_id", "canonical_name", "mention_count",
        "wikidata_id", "wikidata_label", "creator", "creation_date",
        "material", "technique", "movement", "subject",
        "location", "collection",
    ],
    "literary_work": [
        "xml_id", "canonical_name", "mention_count",
        "wikidata_id", "wikidata_label", "author", "publication_date",
        "language", "subject", "bnf",
    ],
    "event": [
        "xml_id", "canonical_name", "mention_count",
        "wikidata_id", "wikidata_label", "date", "start_date",
        "end_date", "location", "participant",
    ],
    "material": [
        "xml_id", "canonical_name", "mention_count",
        "wikidata_id", "wikidata_label", "instance_of", "use",
    ],
    "technique": [
        "xml_id", "canonical_name", "mention_count",
        "wikidata_id", "wikidata_label", "instance_of", "use",
    ],
}


def write_entity_csvs(entities, entity_types_config, output_dir):
    """
    Write entity CSVs, one per type.

    Args:
        entities: List of ResolvedEntity.
        entity_types_config: NER_ENTITY_TYPES from config.
        output_dir: Path to output directory.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # Group by type
    by_type = {}
    for ent in entities:
        by_type.setdefault(ent.entity_type, []).append(ent)

    written = 0
    for etype, ents in by_type.items():
        cfg = entity_types_config.get(etype, {})
        csv_file = cfg.get("csv_file")
        if not csv_file:
            continue

        columns = _CSV_COLUMNS.get(etype, ["xml_id", "canonical_name", "mention_count"])
        path = output_dir / csv_file

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=columns, delimiter=";", extrasaction="ignore")
            writer.writeheader()

            for ent in sorted(ents, key=lambda e: len(e.mentions), reverse=True):
                row = {
                    "xml_id": ent.xml_id,
                    "canonical_name": ent.canonical_name,
                    "mention_count": len(ent.mentions),
                    "local_id": ent.local_match or "",
                    "wikidata_id": ent.wikidata_id or "",
                    "wikidata_label": ent.wikidata_label or "",
                }
                # Add Wikidata properties
                for col in columns:
                    if col not in row:
                        row[col] = ent.wikidata_data.get(col, "")
                writer.writerow(row)

            written += 1

    logger.info("NER: wrote %d entity CSV files to %s", written, output_dir)


# =============================================================================
# TEI HEADER INJECTION
# =============================================================================


def _tei(tag):
    """Prefix a tag with the TEI namespace."""
    return f"{TEI_NS}{tag}"


def _sub(parent, tag, **attrs):
    """Create a TEI-namespaced SubElement."""
    return etree.SubElement(parent, _tei(tag), **attrs)


def _find_or_create(parent, tag):
    """Find or create a direct child element in TEI namespace."""
    for child in parent:
        if _local(child.tag) == tag:
            return child
    return _sub(parent, tag)


def inject_header_entities(root, entities, entity_types_config):
    """
    Inject entity lists (<listPerson>, <listPlace>, etc.) into the TEI header.

    Args:
        root: TEI root element.
        entities: List of ResolvedEntity.
        entity_types_config: NER_ENTITY_TYPES from config.
    """
    # Find or create profileDesc
    tei_header = None
    for elem in root.iter():
        if _local(elem.tag) == "teiHeader":
            tei_header = elem
            break
    if tei_header is None:
        return

    profile_desc = _find_or_create(tei_header, "profileDesc")

    # Group entities by type
    by_type = {}
    for ent in entities:
        by_type.setdefault(ent.entity_type, []).append(ent)

    for etype, ents in by_type.items():
        cfg = entity_types_config.get(etype, {})
        list_tag = cfg.get("tei_list")
        item_tag = cfg.get("tei_item")
        parent_tag = cfg.get("tei_parent")

        if not list_tag or not item_tag:
            continue

        # Find or create parent container
        if parent_tag == "standOff":
            parent = _find_or_create(root, "standOff")
        elif parent_tag == "settingDesc":
            parent = _find_or_create(profile_desc, "settingDesc")
        elif parent_tag == "particDesc":
            parent = _find_or_create(profile_desc, "particDesc")
        else:
            parent = profile_desc

        # Create the list element with source="#ner-auto"
        list_elem = _sub(parent, list_tag, source="#ner-auto")

        for ent in sorted(ents, key=lambda e: len(e.mentions), reverse=True):
            item = _sub(list_elem, item_tag)
            item.set(XML_ID, ent.xml_id)

            # Add the name element
            name_tag = cfg.get("tei_element", "name")
            extra_attrs = cfg.get("tei_element_attrs", {})

            # For events, use <label> instead of <rs>
            if etype == "event":
                name_elem = _sub(item, "label")
            else:
                name_elem = _sub(item, name_tag, **extra_attrs)
            name_elem.text = ent.canonical_name

            # Add identifiers
            if ent.wikidata_id:
                idno = _sub(item, "idno", type="wikidata")
                idno.text = ent.wikidata_id

            # Add type-specific properties from Wikidata
            wd = ent.wikidata_data
            if wd:
                _inject_type_specific(item, etype, wd)

    logger.info("NER: injected entity lists into TEI header")


def _inject_type_specific(item, entity_type, wd):
    """Add type-specific TEI elements from Wikidata data."""
    if entity_type == "person":
        # Authority identifiers
        for prop, idno_type in [("viaf", "viaf"), ("bnf", "bnf"), ("isni", "isni"), ("ulan", "ulan")]:
            val = wd.get(prop)
            if val:
                idno = _sub(item, "idno", type=idno_type)
                idno.text = val

        # Birth/death
        birth = wd.get("birth_date")
        if birth:
            b = _sub(item, "birth")
            if len(birth) <= 10:
                b.set("when", birth)
            bp = wd.get("birth_place")
            if bp:
                pn = _sub(b, "placeName")
                pn.text = bp

        death = wd.get("death_date")
        if death:
            d = _sub(item, "death")
            if len(death) <= 10:
                d.set("when", death)
            dp = wd.get("death_place")
            if dp:
                pn = _sub(d, "placeName")
                pn.text = dp

    elif entity_type == "place":
        coords = wd.get("coordinates")
        if coords:
            loc = _sub(item, "location")
            geo = _sub(loc, "geo")
            geo.text = coords

        for prop, idno_type in [("geonames", "geonames"), ("tgn", "tgn")]:
            val = wd.get(prop)
            if val:
                idno = _sub(item, "idno", type=idno_type)
                idno.text = val

    elif entity_type == "artwork":
        creator = wd.get("creator")
        if creator:
            note = _sub(item, "note", type="creator")
            note.text = creator


# =============================================================================
# @ref INJECTION IN BODY
# =============================================================================


def add_refs_to_body(root, entities, entity_types_config):
    """
    Add @ref attributes to entity annotations in the body.

    Matches injected entity elements (with @resp="#ner-auto") to their
    ResolvedEntity xml_id.

    Args:
        root: TEI root element.
        entities: List of ResolvedEntity.
        entity_types_config: NER_ENTITY_TYPES from config.
    """
    # Build lookup: (entity_type, frozenset of w_ids) → xml_id
    # and (entity_type, text) → xml_id for raw text entities
    #
    # NOTE: id() relies on lxml reusing the same element proxies throughout
    # the single-pass pipeline. If the tree is re-parsed or serialized between
    # Phase 8 injection and this point, all id() values will change and no
    # @ref attributes will be set.
    w_lookup = {}
    text_lookup = {}

    for ent in entities:
        for mention in ent.mentions:
            if mention.w_elements:
                w_ids = frozenset(id(w) for w in mention.w_elements)
                w_lookup[w_ids] = ent.xml_id
            elif mention.text_node is not None:
                text_lookup[(ent.entity_type, mention.text)] = ent.xml_id

    # Find all elements with @resp="#ner-auto"
    body = None
    for elem in root.iter():
        if _local(elem.tag) == "body":
            body = elem
            break
    if body is None:
        return

    ref_count = 0
    for elem in body.iter():
        if elem.get("resp") != "#ner-auto":
            continue

        # Try to match by child <w> elements
        w_children = [ch for ch in elem if _local(ch.tag) == "w"]
        if w_children:
            w_ids = frozenset(id(w) for w in w_children)
            xml_id = w_lookup.get(w_ids)
            if xml_id:
                elem.set("ref", f"#{xml_id}")
                ref_count += 1
                continue

        # Try to match by text content
        local_tag = _local(elem.tag)
        for etype, cfg in entity_types_config.items():
            if cfg.get("tei_element") == local_tag:
                text = elem.text or ""
                xml_id = text_lookup.get((etype, text))
                if xml_id:
                    elem.set("ref", f"#{xml_id}")
                    ref_count += 1
                break

    if ref_count:
        logger.info("NER: added @ref to %d entity annotations", ref_count)


# =============================================================================
# EDITORIAL DECLARATION
# =============================================================================


def inject_editorial_declaration(root):
    """
    Add NER methodology description to <encodingDesc>/<editorialDecl>
    and <respStmt xml:id="ner-auto"> to <titleStmt>.

    Args:
        root: TEI root element.
    """
    tei_header = None
    for elem in root.iter():
        if _local(elem.tag) == "teiHeader":
            tei_header = elem
            break
    if tei_header is None:
        return

    # Add respStmt to titleStmt
    file_desc = None
    for child in tei_header:
        if _local(child.tag) == "fileDesc":
            file_desc = child
            break
    if file_desc is not None:
        title_stmt = None
        for child in file_desc:
            if _local(child.tag) == "titleStmt":
                title_stmt = child
                break
        if title_stmt is not None:
            resp_stmt = _sub(title_stmt, "respStmt")
            resp_stmt.set(XML_ID, "ner-auto")
            resp = _sub(resp_stmt, "resp")
            resp.text = "Automatic named entity recognition"
            name = _sub(resp_stmt, "name")
            name.text = "NER Pipeline (CamemBERT + GLiNER)"

    # Add interpretation to encodingDesc/editorialDecl
    encoding_desc = _find_or_create(tei_header, "encodingDesc")
    editorial_decl = _find_or_create(encoding_desc, "editorialDecl")
    interpretation = _sub(editorial_decl, "interpretation")
    p = _sub(interpretation, "p")
    p.text = (
        "Named entities were automatically detected using a hybrid NER pipeline. "
        "French text was processed with CamemBERT-classical-fr-ner on original "
        "orthography and GLiNER-multi-v2.1 on modernized text. Non-French text "
        "was processed with GLiNER only. Annotations carry @resp=\"#ner-auto\" "
        "and @cert (low < 0.6, mid 0.6\u20130.85, high > 0.85). Identifiers "
        "were resolved against local authority files and Wikidata where "
        "confidence exceeded 0.7."
    )


# =============================================================================
# PUBLIC API — PHASE 9 ORCHESTRATOR
# =============================================================================


def resolve_entities(
    root,
    aligned_entities,
    entity_types_config,
    person_db,
    output_dir,
    min_confidence,
    max_rps,
    wikidata_timeout,
):
    """
    Phase 9 orchestrator: group, link, Wikidata, CSV, header, @ref.

    Args:
        root: TEI root element.
        aligned_entities: List of AlignedEntity from Phase 8.
        entity_types_config: NER_ENTITY_TYPES from config.
        person_db: PersonDatabase instance or None.
        output_dir: Path for entity CSV files.
        min_confidence: Minimum confidence for Wikidata lookup.
        max_rps: Wikidata rate limit.
        wikidata_timeout: Wikidata HTTP timeout.

    Returns:
        list[ResolvedEntity]: All resolved entities.
    """
    if not aligned_entities:
        return []

    # Step 1: Group mentions into unique entities
    entities = group_mentions(aligned_entities)

    # Step 2: Link to local person database
    link_local(entities, person_db)

    # Step 3: Wikidata enrichment
    resolve_wikidata(
        entities, entity_types_config, min_confidence, max_rps, wikidata_timeout
    )

    # Step 4: Write CSV files
    write_entity_csvs(entities, entity_types_config, output_dir)

    # Step 5: Inject editorial declaration
    inject_editorial_declaration(root)

    # Step 6: Inject entity lists into header
    inject_header_entities(root, entities, entity_types_config)

    # Step 7: Add @ref to body annotations
    add_refs_to_body(root, entities, entity_types_config)

    # Summary stats
    by_type = {}
    for ent in entities:
        by_type[ent.entity_type] = by_type.get(ent.entity_type, 0) + 1
    type_summary = ", ".join(f"{c} {t}" for t, c in sorted(by_type.items(), key=lambda x: -x[1]))
    logger.info("NER: %d unique entities (%s)", len(entities), type_summary)

    wd_count = sum(1 for e in entities if e.wikidata_id)
    local_count = sum(1 for e in entities if e.local_match)
    if wd_count or local_count:
        logger.info(
            "NER: %d resolved via Wikidata, %d linked to local metadata",
            wd_count,
            local_count,
        )

    return entities
