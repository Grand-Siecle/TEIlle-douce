# Tests unitaires de src/lang/detector.py -- chemins d'orchestration,
# sans charger les modeles lingua (internes bouchonnes par monkeypatch).
# La detection reelle est couverte par le mode e2e court.
#
# Run: venv/bin/python -m pytest tests/test_lang_detector.py -q
from src.lang.detector import LinguaDetector

TEXTE = "un texte suffisamment long pour depasser le seuil minimal de detection"


def _det(monkeypatch, primary=("fra", 0.99), segments=None):
    det = LinguaDetector()
    monkeypatch.setattr(det, "_ensure_detector", lambda: None)
    monkeypatch.setattr(det, "_detect_single", lambda cleaned: primary)
    monkeypatch.setattr(
        det, "_foreign_segments_cleaned",
        lambda cleaned, idx_map, prim: list(segments or []),
    )
    return det


def test_detect_primary_and_segments_single_cleaning_pass(monkeypatch):
    """Audit 3.6 : l'API combinee rend le couple (primaire, segments) et
    n'appelle le nettoyage qu'une fois."""
    det = _det(monkeypatch, primary=("lat", 0.8), segments=[(0, 5, "fra")])
    appels = []
    vrai_clean = det._clean_with_map
    monkeypatch.setattr(
        det, "_clean_with_map",
        lambda text: appels.append(text) or vrai_clean(text),
    )

    primary, segments = det.detect_primary_and_segments(TEXTE)

    assert (primary, segments) == ("lat", [(0, 5, "fra")])
    assert len(appels) == 1, "le texte doit etre nettoye une seule fois"
    assert det.stats["lat"] == 1


def test_detect_primary_and_segments_short_text_defaults(monkeypatch):
    """Texte sous le seuil : langue par defaut, pas de chargement lingua."""
    det = _det(monkeypatch, segments=[])

    def explose():
        raise AssertionError("lingua ne doit pas etre charge pour un texte court")

    monkeypatch.setattr(det, "_ensure_detector", explose)
    primary, segments = det.detect_primary_and_segments("court")
    assert primary == det.default_lang
    assert segments == []


def test_detect_foreign_segments_detects_primary_when_not_given(monkeypatch):
    """Chemin de compat : sans primary_lang fourni, il est detecte d'abord."""
    det = _det(monkeypatch, primary=("ita", 0.7), segments=[(2, 8, "lat")])
    vus = []
    monkeypatch.setattr(
        det, "_foreign_segments_cleaned",
        lambda cleaned, idx_map, prim: vus.append(prim) or [(2, 8, "lat")],
    )

    assert det.detect_foreign_segments(TEXTE) == [(2, 8, "lat")]
    assert vus == ["ita"]


def test_detect_foreign_segments_passes_given_primary_through(monkeypatch):
    det = _det(monkeypatch, segments=[])
    vus = []

    def explose(cleaned):
        raise AssertionError("primaire fourni : pas de re-detection")

    monkeypatch.setattr(det, "_detect_single", explose)
    monkeypatch.setattr(
        det, "_foreign_segments_cleaned",
        lambda cleaned, idx_map, prim: vus.append(prim) or [],
    )

    assert det.detect_foreign_segments(TEXTE, primary_lang="fra") == []
    assert vus == ["fra"]


# =============================================================================
# <langUsage> : @usage est un POURCENTAGE en TEI (audit 1.13)
# =============================================================================

from lxml import etree

from src.lang.header import build_langusage


def _entete():
    return etree.fromstring(
        b"<TEI><teiHeader><profileDesc/></teiHeader><text><body/></text></TEI>"
    )


def _langues(root):
    return [
        (el.get("ident"), el.get("usage"), el.get("n"))
        for el in root.iter("language")
    ]


def test_usage_is_a_percentage_not_a_raw_count():
    """usage="1716" se lisait 1716 % : n'importe quel outil qui somme
    l'attribut, ou le lecteur, y voyait une valeur impossible."""
    root = _entete()

    build_langusage(root, {"fra": 1716, "lat": 38})

    assert _langues(root) == [("fra", "98", "1716"), ("lat", "2", "38")]


def test_a_language_actually_present_never_rounds_down_to_zero():
    """0 % declarerait absente une langue dont les segments <foreign>
    sont pourtant dans le texte."""
    root = _entete()

    build_langusage(root, {"fra": 5000, "grc": 3})

    parts = dict((ident, usage) for ident, usage, _ in _langues(root))
    assert parts["grc"] == "1"
    assert parts["fra"] == "100"


def test_the_measured_count_survives_in_n():
    root = _entete()
    build_langusage(root, {"fra": 7})
    assert _langues(root) == [("fra", "100", "7")]
