# -----------------------------------------------------------
# Async Wikidata client for entity resolution.
# -----------------------------------------------------------
"""
Wikidata lookup client.

Provides search (wbsearchentities) and SPARQL queries for enriching
NER-detected entities with Wikidata identifiers and properties.

Rate-limited and fault-tolerant: failures log warnings, never crash.
"""

import asyncio
import logging
import time

import httpx

logger = logging.getLogger(__name__)

WIKIDATA_API = "https://www.wikidata.org/w/api.php"
WIKIDATA_SPARQL = "https://query.wikidata.org/sparql"

# Properties to fetch per entity type (art history focus)
ENTITY_PROPERTIES = {
    "person": {
        "P569": "birth_date",
        "P570": "death_date",
        "P19": "birth_place",
        "P20": "death_place",
        "P106": "profession",
        "P135": "movement",
        "P1066": "master",
        "P802": "student",
        "P800": "notable_work",
        "P213": "isni",
        "P214": "viaf",
        "P268": "bnf",
        "P245": "ulan",
        "P18": "image",
    },
    "place": {
        "P625": "coordinates",
        "P17": "country",
        "P31": "instance_of",
        "P1566": "geonames",
        "P1667": "tgn",
    },
    "organization": {
        "P571": "foundation_date",
        "P112": "founder",
        "P159": "headquarters",
        "P31": "instance_of",
    },
    "artwork": {
        "P170": "creator",
        "P571": "creation_date",
        "P186": "material",
        "P2079": "technique",
        "P135": "movement",
        "P180": "subject",
        "P276": "location",
        "P195": "collection",
    },
    "literary_work": {
        "P50": "author",
        "P577": "publication_date",
        "P407": "language",
        "P921": "subject",
        "P268": "bnf",
    },
    "event": {
        "P585": "date",
        "P580": "start_date",
        "P582": "end_date",
        "P276": "location",
        "P710": "participant",
    },
    "material": {
        "P31": "instance_of",
        "P366": "use",
    },
    "technique": {
        "P31": "instance_of",
        "P366": "use",
    },
}


class RateLimiter:
    """Simple token-bucket rate limiter."""

    def __init__(self, max_rps):
        self._interval = 1.0 / max_rps
        self._last = 0.0

    async def acquire(self):
        now = time.monotonic()
        wait = self._interval - (now - self._last)
        if wait > 0:
            await asyncio.sleep(wait)
        self._last = time.monotonic()


class WikidataClient:
    """
    Async Wikidata client with rate limiting and retry.

    Usage:
        async with WikidataClient(max_rps=10, timeout=30) as client:
            qid = await client.search_entity("Jean Calvin", "person")
            data = await client.fetch_properties(qid, "person")
    """

    def __init__(self, max_rps=10, timeout=30):
        self._limiter = RateLimiter(max_rps)
        self._timeout = timeout
        self._client = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(self, *_exc):
        if self._client:
            await self._client.aclose()

    async def _get(self, url, params, retries=2):
        """GET with rate limiting and retry."""
        for attempt in range(retries + 1):
            await self._limiter.acquire()
            try:
                resp = await self._client.get(url, params=params)
                resp.raise_for_status()
                return resp.json()
            except (httpx.HTTPError, httpx.TimeoutException) as e:
                if attempt < retries:
                    wait = 2 ** attempt
                    logger.debug("Wikidata retry %d after %s: %s", attempt + 1, wait, e)
                    await asyncio.sleep(wait)
                else:
                    logger.warning("Wikidata request failed after %d retries: %s", retries, e)
                    return None

    async def search_entity(self, name, entity_type=None, language="fr"):
        """
        Search for an entity by name using wbsearchentities.

        Args:
            name: Entity name to search for.
            entity_type: Optional type hint (not used in API, for logging).
            language: Search language.

        Returns:
            str or None: Wikidata QID (e.g., "Q37577") or None.
        """
        params = {
            "action": "wbsearchentities",
            "search": name,
            "language": language,
            "format": "json",
            "limit": 5,
            "type": "item",
        }
        data = await self._get(WIKIDATA_API, params)
        if not data or "search" not in data or not data["search"]:
            return None

        # Return the top result
        result = data["search"][0]
        qid = result.get("id")
        label = result.get("label", "")
        logger.debug(
            "Wikidata search '%s' (%s) → %s (%s)",
            name,
            entity_type,
            qid,
            label,
        )
        return qid

    async def search_by_identifier(self, prop, value):
        """
        Search for an entity by external identifier (e.g., ISNI, ARK).

        Args:
            prop: Wikidata property (e.g., "P213" for ISNI).
            value: Identifier value.

        Returns:
            str or None: Wikidata QID or None.
        """
        sparql = f"""
        SELECT ?item WHERE {{
          ?item wdt:{prop} "{value}" .
        }} LIMIT 1
        """
        data = await self._sparql(sparql)
        if not data:
            return None
        bindings = data.get("results", {}).get("bindings", [])
        if not bindings:
            return None
        uri = bindings[0].get("item", {}).get("value", "")
        return uri.rsplit("/", 1)[-1] if "/" in uri else None

    async def fetch_properties(self, qid, entity_type):
        """
        Fetch entity properties via SPARQL.

        Args:
            qid: Wikidata QID (e.g., "Q37577").
            entity_type: Entity type key for property selection.

        Returns:
            dict: Property name → value mapping, or empty dict.
        """
        props = ENTITY_PROPERTIES.get(entity_type, {})
        if not props:
            return {"qid": qid}

        # Build SPARQL SELECT with OPTIONAL for each property
        selects = ["?itemLabel"]
        optionals = []
        for prop_id, prop_name in props.items():
            var = f"?{prop_name}"
            selects.append(var)
            var_label = f"?{prop_name}Label"
            selects.append(var_label)
            optionals.append(
                f"OPTIONAL {{ wd:{qid} wdt:{prop_id} {var} . }}"
            )

        sparql = f"""
        SELECT {" ".join(selects)} WHERE {{
          BIND(wd:{qid} AS ?item)
          {chr(10).join(optionals)}
          SERVICE wikibase:label {{ bd:serviceParam wikibase:language "fr,en" . }}
        }} LIMIT 1
        """

        data = await self._sparql(sparql)
        if not data:
            return {"qid": qid}

        bindings = data.get("results", {}).get("bindings", [])
        if not bindings:
            return {"qid": qid}

        row = bindings[0]
        result = {"qid": qid}
        result["label"] = _sparql_value(row, "itemLabel")

        for prop_id, prop_name in props.items():
            val = _sparql_value(row, prop_name)
            val_label = _sparql_value(row, f"{prop_name}Label")
            # Prefer label over URI for entity-valued properties
            result[prop_name] = val_label if val_label and val_label != val else val

        return result

    async def _sparql(self, query):
        """Execute a SPARQL query against Wikidata."""
        params = {"query": query, "format": "json"}
        headers = {"Accept": "application/sparql-results+json"}
        await self._limiter.acquire()
        try:
            resp = await self._client.get(
                WIKIDATA_SPARQL, params=params, headers=headers
            )
            resp.raise_for_status()
            return resp.json()
        except (httpx.HTTPError, httpx.TimeoutException) as e:
            logger.warning("Wikidata SPARQL failed: %s", e)
            return None


def _sparql_value(row, var_name):
    """Extract a value from a SPARQL result row."""
    binding = row.get(var_name)
    if not binding:
        return None
    return binding.get("value")


async def resolve_entities_wikidata(entities, max_rps=10, timeout=30):
    """
    Resolve a list of entities against Wikidata.

    Each entity should have: canonical_name, entity_type, and optionally
    local identifiers (isni, ark) for precise lookup.

    Args:
        entities: List of dicts with keys: canonical_name, entity_type,
                  and optionally isni, ark.
        max_rps: Maximum requests per second.
        timeout: HTTP timeout in seconds.

    Returns:
        list[dict]: Each dict has qid, label, and type-specific properties.
    """
    results = []

    async with WikidataClient(max_rps=max_rps, timeout=timeout) as client:
        for ent in entities:
            name = ent.get("canonical_name", "")
            etype = ent.get("entity_type", "")

            # Try identifier-based lookup first
            qid = None
            isni = ent.get("isni")
            if isni:
                qid = await client.search_by_identifier("P213", isni)

            ark = ent.get("ark")
            if not qid and ark:
                qid = await client.search_by_identifier("P268", ark)

            # Fall back to name search
            if not qid:
                qid = await client.search_entity(name, etype)

            if not qid:
                results.append(None)
                continue

            # Fetch properties
            props = await client.fetch_properties(qid, etype)
            results.append(props)

    return results
