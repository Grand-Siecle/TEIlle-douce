# -----------------------------------------------------------
# Text modernization client for external translation APIs
# Calls a REST API to modernize old French (or other languages)
# -----------------------------------------------------------
"""
Modernization module for ALTO2TEI pipeline.

Calls an external REST API (e.g., VieuxParler-API) to translate
old/historical text into modern spelling. Supports batch processing
and multiple languages via configuration.
"""

import requests

from config import MODERNIZE_API, MODERNIZE_BATCH_SIZE, MODERNIZE_TIMEOUT


def modernize_texts(texts, lang="fra"):
    """
    Modernize a list of text lines via the external API.

    Sends texts in batches to the /translate/batch endpoint
    and collects the results.

    Args:
        texts (list[str]): List of original text lines.
        lang (str): TEI language ident (e.g., "fra"). Used to
                    select the API endpoint from MODERNIZE_API config.

    Returns:
        list[str] or None: List of modernized texts (same length as input),
                           or None if the language has no configured API.

    Raises:
        requests.RequestException: If an API call fails.
    """
    base_url = MODERNIZE_API.get(lang)
    if not base_url:
        return None

    url = f"{base_url}/translate/batch"
    results = []

    for i in range(0, len(texts), MODERNIZE_BATCH_SIZE):
        batch = texts[i:i + MODERNIZE_BATCH_SIZE]
        response = requests.post(
            url,
            json={"texts": batch, "batch_size": MODERNIZE_BATCH_SIZE},
            timeout=MODERNIZE_TIMEOUT,
        )
        response.raise_for_status()
        data = response.json()
        results.extend(data["translations"])

    return results


def check_api(lang="fra"):
    """
    Check if the modernization API is reachable for a given language.

    Args:
        lang (str): TEI language ident.

    Returns:
        bool: True if the API is reachable and healthy.
    """
    base_url = MODERNIZE_API.get(lang)
    if not base_url:
        return False

    try:
        response = requests.get(f"{base_url}/health", timeout=5)
        return response.status_code == 200
    except requests.RequestException:
        return False
