# -----------------------------------------------------------
# Phase 9: Entity resolution, CSV output,
#           TEI header injection, and @ref linking.
# -----------------------------------------------------------
"""
NER resolution pipeline (Phase 9).

Deduplicates entity mentions, links to local metadata (metadata_personne.csv),
writes entity CSVs, injects entity lists into the TEI
header, and adds @ref attributes to body annotations.
"""

import csv
import logging
import uuid
from dataclasses import dataclass
from pathlib import Path

from lxml import etree

from config import NER_CERT_THRESHOLDS
from .entity_schema import NER_VOCABULARIES
from ..constants import UUID_NAMESPACE, XML_ID, tag_like
from .ner_align import _confidence_to_cert
from ..utils.xml import content_root, declare_responsibility, local_tag as _local
from .ner_filter import (
    _normalize,
    fix_canonical_names,
    fuzzy_merge_entities,
    filter_resolved_entities,
)

logger = logging.getLogger(__name__)



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


def _make_xml_id(entity_type, normalized_name):
    """
    NCName-compliant, deterministic xml:id (audit 2.8).

    uuid5 over (type, normalized canonical name): the same entity gets
    the same id on every run — and in every document, which is exactly
    what the external reconciliation step needs to link entities across
    the corpus. Uniqueness within a document holds because entities are
    grouped by this very key beforehand.
    """
    prefix = _TYPE_PREFIX.get(entity_type, "ent")
    uid = uuid.uuid5(UUID_NAMESPACE, f"{entity_type}\x1f{normalized_name}")
    return f"{prefix}-{uid}"


# =============================================================================
# MENTION GROUPING
# =============================================================================


def _get_canonical_name(entity):
    """Extract canonical form from an AlignedEntity."""
    if entity.w_elements:
        # Use lemma attributes from <w> elements; fall back to surface text
        # when lemma is a foreign-language sentinel (e.g. "@latin", "@italian").
        tokens = []
        for w in entity.w_elements:
            lemma = w.get("lemma")
            if lemma and not lemma.startswith("@"):
                tokens.append(lemma)
            else:
                tokens.append(w.text or "")
        return " ".join(tokens)
    return entity.text


def group_mentions(aligned_entities):
    """
    Group entity mentions by (type, normalized canonical form).

    Exact match on the normalized canonical only — fuzzy merging of
    spelling variants is performed separately by ``fuzzy_merge_entities``.

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
    for (etype, norm), grp in groups.items():
        resolved.append(
            ResolvedEntity(
                entity_type=etype,
                canonical_name=grp["canonical"],
                xml_id=_make_xml_id(etype, norm),
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

    if not any(e.entity_type == "person" for e in entities):
        # Nothing to link: normalizing 478 people to discover it would
        # cost more than the old code did in this case.
        return

    # The database is normalized ONCE. It was re-normalized for every
    # entity — 478 people times every person entity of the document, the
    # same lowercasing and accent-stripping over and over (audit 3.9).
    catalogue = []
    for pid in person_db:
        person = person_db.get(pid)
        if not person:
            continue
        surname = _normalize(person.get("surname") or "")
        if not surname:
            continue
        forename = _normalize(person.get("forename") or "")
        catalogue.append((
            pid, person, surname, f"{forename} {surname}".strip(),
            surname.split(),
        ))

    linked = 0
    for ent in entities:
        if ent.entity_type != "person":
            continue

        norm_name = _normalize(ent.canonical_name)
        name_words = set(norm_name.split())

        for pid, person, surname, full, surname_words in catalogue:
            # Match: full name, surname only, or surname as whole word in canonical
            surname_in_name = (
                bool(surname_words) and all(sw in name_words for sw in surname_words)
            )
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
# CSV OUTPUT
# =============================================================================

# Column definitions per entity type
_CSV_COLUMNS = {
    "person": [
        "xml_id", "canonical_name", "mention_count", "local_id",
    ],
    "place": [
        "xml_id", "canonical_name", "mention_count",
    ],
    "organization": [
        "xml_id", "canonical_name", "mention_count",
    ],
    "artwork": [
        "xml_id", "canonical_name", "mention_count",
    ],
    "literary_work": [
        "xml_id", "canonical_name", "mention_count",
    ],
    "event": [
        "xml_id", "canonical_name", "mention_count",
    ],
    "material": [
        "xml_id", "canonical_name", "mention_count",
    ],
    "technique": [
        "xml_id", "canonical_name", "mention_count",
    ],
}


def write_entity_csvs(entities, entity_types_config, output_dir, document_name):
    """
    Write entity CSVs, one per type, inside a per-document subfolder.

    The subfolder is mandatory: these files are opened in mode "w", and
    resolve_entities() runs once per document against the same shared
    output_dir. Writing them all side by side would make each document
    truncate the previous one's CSVs, destroying every entity but the last
    document's -- see docs/rapport_audit.md #2.4. The downstream entity
    reconciliation work needs the entities of ALL documents.

    Args:
        entities: List of ResolvedEntity.
        entity_types_config: NER_ENTITY_TYPES from src/enrichment/entity_schema.py.
        output_dir: Path to the root entities directory.
        document_name: Document folder name; used as the subfolder name, so
            that re-running a single document rewrites only its own files.
    """
    output_dir = Path(output_dir) / document_name
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
                }
                writer.writerow(row)

            written += 1

    logger.info("NER: wrote %d entity CSV files to %s", written, output_dir)


# =============================================================================
# TEI HEADER INJECTION
# =============================================================================


def _sub(parent, tag, **attrs):
    """
    Create a SubElement following the host tree's namespace convention.

    The pipeline's own tree is bare in memory; a TEI file read back from
    disk is namespaced. Injecting one fixed convention made the two
    coexist: body.iter("persName") missed the NER entities and
    find(".//particDesc") missed the CSV one, so a second particDesc got
    appended beside it (audit 4.2). Following the parent works in both.
    """
    return etree.SubElement(parent, tag_like(parent, tag), **attrs)


def _find_or_create(parent, tag):
    """Find a direct child by local name, or create one following the
    host tree's namespace convention (see _sub)."""
    for child in parent:
        if _local(child.tag) == tag:
            return child
    return _sub(parent, tag)


def _declare_vocabulary(root, ana):
    """Declare the open vocabulary a generic <list> points at.

    <list type="materials"> names a vocabulary nothing described, and a
    reader had no way to tell an inferred term from a controlled
    descriptor. The categories are declared where the SegmOnto taxonomy
    already lives, and only for the lists a document actually carries.
    """
    category_id = (ana or "").lstrip("#")
    if not category_id:
        return None
    for tax_id, vocabulary in NER_VOCABULARIES.items():
        description = vocabulary["categories"].get(category_id)
        if description is None:
            continue

        encoding_desc = None
        for el in root.iter():
            if _local(el.tag) == "encodingDesc":
                encoding_desc = el
                break
        if encoding_desc is None:
            return None
        # `or` would be wrong here: an empty <classDecl> is falsy, so a
        # freshly created one would be discarded and a second appended
        # beside it — the trap lxml's FutureWarning is about.
        class_decl = next(
            (c for c in encoding_desc if _local(c.tag) == "classDecl"),
            None,
        )
        if class_decl is None:
            class_decl = _sub(encoding_desc, "classDecl")

        taxonomy = next(
            (t for t in class_decl
             if _local(t.tag) == "taxonomy" and t.get(XML_ID) == tax_id),
            None,
        )
        if taxonomy is None:
            taxonomy = _sub(class_decl, "taxonomy")
            taxonomy.set(XML_ID, tax_id)
            _sub(taxonomy, "bibl").text = vocabulary["label"]
        if any(c.get(XML_ID) == category_id for c in taxonomy):
            return taxonomy

        category = _sub(taxonomy, "category")
        category.set(XML_ID, category_id)
        _sub(category, "catDesc").text = description
        return taxonomy
    return None


def _entity_cert(entity, cert_thresholds=None):
    """Certainty of the entity itself, from its best-read mention.

    An entity exists as surely as its most confident mention: reading
    "Poussin" thirty times with one shaky occurrence does not make the
    painter doubtful. Falls back to None when no mention carries a score.
    """
    scores = [
        m.confidence for m in entity.mentions
        if getattr(m, "confidence", None) is not None
    ]
    if not scores:
        return None
    return _confidence_to_cert(max(scores), cert_thresholds or NER_CERT_THRESHOLDS)


def inject_header_entities(root, entities, entity_types_config,
                           cert_thresholds=None):
    """
    Inject entity lists (<listPerson>, <listPlace>, etc.) into the TEI header.

    Args:
        root: TEI root element.
        entities: List of ResolvedEntity.
        entity_types_config: NER_ENTITY_TYPES from src/enrichment/entity_schema.py.
        cert_thresholds: score -> @cert mapping. Travels with the type
            config: read from the module default instead, an override
            given to run_ner would grade the mentions one way and the
            entities another, and the file would carry two certainties
            for one reading.
    """
    # Find the header
    tei_header = None
    for elem in root.iter():
        if _local(elem.tag) == "teiHeader":
            tei_header = elem
            break
    if tei_header is None:
        return

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

        # Find or create parent container. <standOff> is a sibling of
        # the header; the two profileDesc containers are named because
        # anything else would be created verbatim — a typo in the config
        # would then produce <profileDesc><particDes/> and fail the
        # schema with nothing said. profileDesc itself is only created
        # when something is actually routed under it: appending an empty
        # one after <revisionDesc> is invalid TEI.
        if parent_tag == "standOff":
            parent = _find_or_create(root, "standOff")
        elif parent_tag in ("particDesc", "settingDesc"):
            parent = _find_or_create(
                _find_or_create(tei_header, "profileDesc"), parent_tag
            )
        else:
            if parent_tag:
                logger.warning(
                    "Entity type %r asks for an unknown container %r; "
                    "its list goes to <profileDesc>", etype, parent_tag,
                )
            parent = _find_or_create(tei_header, "profileDesc")

        # Replace-not-append (audit 6.13, same re-entry class as the
        # editorial declaration): a second pass over the same tree must
        # regenerate the auto-built list, not add a sibling whose items
        # carry the same xml:ids — that would be an invalid TEI.
        #
        # The element name alone does NOT identify a list: materials and
        # techniques are both a plain <list>, told apart by @type only,
        # so matching on the name deleted the one built a moment before
        # and left its entities' @ref pointing at nothing.
        list_attrs = dict(cfg.get("tei_list_attrs") or {})
        for old_list in list(parent):
            if _local(old_list.tag) != list_tag or old_list.get("source") != "#ner-auto":
                continue
            if all(old_list.get(k) == v for k, v in list_attrs.items()):
                parent.remove(old_list)

        # Create the list element with source="#ner-auto"
        list_elem = _sub(parent, list_tag, source="#ner-auto", **list_attrs)
        _declare_vocabulary(root, list_attrs.get("ana"))

        for ent in sorted(ents, key=lambda e: len(e.mentions), reverse=True):
            item = _sub(list_elem, item_tag)
            item.set(XML_ID, ent.xml_id)
            # The entity carries the certainty of its own existence, not
            # only of each mention (audit 1.9): a reader of the standOff
            # alone had no way to tell a name read once with a shaky
            # score from one found thirty times.
            cert = _entity_cert(ent, cert_thresholds)
            if cert:
                item.set("cert", cert)

            # Add the name element
            name_tag = cfg.get("tei_element", "name")
            extra_attrs = cfg.get("tei_element_attrs", {})

            # Some item elements have a mandatory intermediate level in
            # TEI's content model (<object> wants <objectIdentifier>).
            wrapper_tag = cfg.get("tei_item_wrapper")
            name_parent = _sub(item, wrapper_tag) if wrapper_tag else item

            # For events, use <label> instead of <rs>
            if etype == "event":
                name_elem = _sub(name_parent, "label")
            else:
                name_elem = _sub(name_parent, name_tag, **extra_attrs)
            name_elem.text = ent.canonical_name

    logger.info("NER: injected entity lists into TEI header")


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
        entity_types_config: NER_ENTITY_TYPES from src/enrichment/entity_schema.py.
    """
    # Build lookup: (entity_type, frozenset of w_ids) → xml_id
    # and (entity_type, text) → xml_id for raw text entities
    #
    # NOTE: id() relies on lxml reusing the same element proxies throughout
    # the single-pass pipeline. If the tree is re-parsed or serialized between
    # Phase 8 injection and this point, all id() values will change and no
    # @ref attributes will be set.
    # Dual anchoring: a mention can be injected both in <orig> (around <w>)
    # and in <reg> (mixed content), so populate every applicable lookup.
    w_lookup = {}
    w_single = {}  # id(w) → xml_id (fallback for per-parent wrapper groups)
    text_lookup = {}
    reg_lookup = {}  # id(reg_elem) → {(start, end): xml_id}

    for ent in entities:
        # Types explicitly configured without a header list (tei_list:
        # None — only "date" now, whose @when IS its identity) have no
        # xml:id target in the document: a @ref would dangle. Their body
        # annotations stay, identified by CSV export only. Configs
        # lacking the key keep the default behavior.
        cfg = entity_types_config.get(ent.entity_type, {})
        if "tei_list" in cfg and cfg["tei_list"] is None:
            continue
        for mention in ent.mentions:
            reg_fragments = getattr(mention, "reg_fragments", None) or []
            for reg_elem, fstart, fend in reg_fragments:
                reg_lookup.setdefault(id(reg_elem), {})[
                    (fstart, fend)
                ] = ent.xml_id
            if mention.w_elements:
                w_ids = frozenset(id(w) for w in mention.w_elements)
                w_lookup[w_ids] = ent.xml_id
                for w in mention.w_elements:
                    w_single[id(w)] = ent.xml_id
            elif not reg_fragments and mention.text_node is not None:
                text_lookup[(ent.entity_type, mention.text)] = ent.xml_id

    # Find all elements with @resp="#ner-auto"
    body = content_root(root)
    if body is None:
        return

    # Reverse map: (element name, @type) → entity type. The element name
    # alone is not a key: <rs> is shared by event and technique, and a
    # last-wins dict made every <rs> an event — technique mentions then
    # matched nothing and stayed without @ref.
    tag_to_etype = {}
    for etype, cfg in entity_types_config.items():
        tag = cfg.get("tei_element")
        if not tag:
            continue
        tag_to_etype[(tag, (cfg.get("tei_element_attrs") or {}).get("type"))] = etype

    ref_count = 0
    for elem in body.iter():
        if elem.get("resp") != "#ner-auto":
            continue

        # Try to match by child <w> elements (entities wrapping <w> in <orig>)
        w_children = [ch for ch in elem if _local(ch.tag) == "w"]
        if w_children:
            w_ids = frozenset(id(w) for w in w_children)
            xml_id = w_lookup.get(w_ids)
            if xml_id is None:
                # A multi-parent entity yields one wrapper per <w> group, so
                # the exact-set match fails; fall back to per-<w> identity
                # (overlap resolution guarantees a <w> belongs to at most
                # one entity).
                candidates = {w_single.get(id(w)) for w in w_children}
                if len(candidates) == 1 and None not in candidates:
                    xml_id = candidates.pop()
            if xml_id:
                elem.set("ref", f"#{xml_id}")
                ref_count += 1
                continue

        # Try to match entities injected inside <reg> via parent identity
        parent = elem.getparent()
        if parent is not None and _local(parent.tag) == "reg":
            reg_ents = reg_lookup.get(id(parent))
            if reg_ents:
                wrapped_text = elem.text or ""
                for (rs, re_), xml_id in reg_ents.items():
                    # Match on wrapped text length and content (positions are
                    # in the original pre-injection <reg> coordinates).
                    if rs is None or re_ is None:
                        continue
                    if (re_ - rs) == len(wrapped_text):
                        elem.set("ref", f"#{xml_id}")
                        ref_count += 1
                        break
                if elem.get("ref"):
                    continue

        # Try to match by text content (raw text injection)
        etype = tag_to_etype.get((_local(elem.tag), elem.get("type")))
        if etype is None:
            etype = tag_to_etype.get((_local(elem.tag), None))
        if etype:
            xml_id = text_lookup.get((etype, elem.text or ""))
            if xml_id:
                elem.set("ref", f"#{xml_id}")
                ref_count += 1

    if ref_count:
        logger.info("NER: added @ref to %d entity annotations", ref_count)


# =============================================================================
# EDITORIAL DECLARATION
# =============================================================================


def inject_editorial_declaration(root):
    """
    Add <respStmt xml:id="ner-auto"> to <editionStmt> (created after
    <titleStmt> if absent).

    The methodology description itself lives in
    src/teiheader/prose.py and is injected by the header builder.

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

    declare_responsibility(
        root, "ner-auto",
        "Automatic named entity recognition",
        "NER Pipeline (CamemBERT + GLiNER)",
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
    document_name,
    cert_thresholds=None,
):
    """
    Phase 9 orchestrator: group, link, CSV, header, @ref.

    Args:
        root: TEI root element.
        aligned_entities: List of AlignedEntity from Phase 8.
        entity_types_config: NER_ENTITY_TYPES from src/enrichment/entity_schema.py.
        person_db: PersonDatabase instance or None.
        output_dir: Root directory for entity CSV files.
        document_name: Document folder name; entity CSVs go into a
            subfolder of that name (see write_entity_csvs).

    Returns:
        list[ResolvedEntity]: All resolved entities.
    """
    if not aligned_entities:
        return []

    # Step 1: Group mentions into unique entities
    entities = group_mentions(aligned_entities)

    # Step 1b: Fix duplicated canonical names ("bonus bonus" → "bonus")
    fix_canonical_names(entities)

    # Step 1c: Fuzzy-merge spelling variants (Tertulus/tertules/Tertullies)
    entities = fuzzy_merge_entities(entities)

    # Step 1d: Prune single-mention low-confidence entities
    entities = filter_resolved_entities(entities)

    # Step 2: Link to local person database
    link_local(entities, person_db)

    # Step 3: Write CSV files
    write_entity_csvs(entities, entity_types_config, output_dir, document_name)

    # Step 4: Inject editorial declaration
    inject_editorial_declaration(root)

    # Step 5: Inject entity lists into header
    inject_header_entities(
        root, entities, entity_types_config, cert_thresholds=cert_thresholds
    )

    # Step 6: Add @ref to body annotations
    add_refs_to_body(root, entities, entity_types_config)

    # Step 7: Write the way back, entity -> the passages that name it
    link_mentions(root)

    # Summary stats
    by_type = {}
    for ent in entities:
        by_type[ent.entity_type] = by_type.get(ent.entity_type, 0) + 1
    type_summary = ", ".join(f"{c} {t}" for t, c in sorted(by_type.items(), key=lambda x: -x[1]))
    logger.info("NER: %d unique entities (%s)", len(entities), type_summary)

    local_count = sum(1 for e in entities if e.local_match)
    if local_count:
        logger.info("NER: %d linked to local metadata", local_count)

    return entities


def _in_reg(elem):
    """True when *elem* sits in the modernized layer of a <choice>."""
    parent = elem.getparent()
    while parent is not None:
        name = _local(parent.tag)
        if name == "reg":
            return True
        if name in ("choice", "ab", "note", "head", "titlePart", "fw"):
            return False
        parent = parent.getparent()
    return False


def link_mentions(root):
    """
    Write the way back: from an entity to the passages that name it.

    The body points at the standOff — every annotation carries
    @ref="#ent-x" — but nothing pointed the other way (audit 1.9), so a
    reader of the entity list had to scan the whole text to find where a
    name occurs. A <linkGrp type="mentions"> in the standOff states it
    directly, one <link> per entity.

    Each annotated passage gets an xml:id derived from its entity's,
    which keeps the identifiers deterministic and readable
    (``pers-…-m1``), and gives the mention itself something to be
    referred to by — a note, a correction, an external alignment.

    Idempotent: the linkGrp is rebuilt, not appended to.

    Args:
        root: TEI root element.

    Returns:
        int: number of entities that got a link.
    """
    body = content_root(root)
    if body is None:
        return 0

    mentions = {}  # entity xml:id -> [mention xml:id]
    for elem in body.iter():
        if elem.get("resp") != "#ner-auto":
            continue
        # One passage, not one element. A mention is injected on BOTH
        # layers of a <choice> — the diplomatic reading and the
        # modernized one — and a mention cut by a line break is one
        # wrapper per fragment, chained by @next/@prev. Counting elements
        # would report two or three passages where the page has one.
        if elem.get("prev"):
            continue
        if _in_reg(elem):
            continue
        # @ref is a pointer LIST in TEI, and csv_book already writes
        # external ones (geonames URLs) beside ours. Take the first
        # local pointer: anything else would end up inside an xml:id.
        ref = next(
            (r[1:] for r in (elem.get("ref") or "").split() if r.startswith("#")),
            None,
        )
        if not ref:
            continue
        found = mentions.setdefault(ref, [])
        mention_id = elem.get(XML_ID)
        if not mention_id:
            mention_id = f"{ref}-m{len(found) + 1}"
            elem.set(XML_ID, mention_id)
        found.append(mention_id)

    if not mentions:
        return 0

    standoff = _find_or_create(root, "standOff")
    for old in list(standoff):
        if _local(old.tag) == "linkGrp" and old.get("type") == "mentions":
            standoff.remove(old)

    link_grp = _sub(standoff, "linkGrp", type="mentions")
    for entity_id, mention_ids in sorted(mentions.items()):
        targets = " ".join([f"#{entity_id}"] + [f"#{mid}" for mid in mention_ids])
        _sub(link_grp, "link", target=targets)

    logger.info("NER: linked %d entities to their mentions", len(mentions))
    return len(mentions)
