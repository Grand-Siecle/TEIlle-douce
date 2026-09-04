# Tests unitaires de teille_douce/lang/detector.py -- chemins d'orchestration,
# sans charger les modeles lingua (internes bouchonnes par monkeypatch).
# La detection reelle est couverte par le mode e2e court.
#
# Run: venv/bin/python -m pytest tests/test_lang_detector.py -q
from teille_douce.lang.detector import LinguaDetector

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

from teille_douce.lang.header import build_langusage


def _entete():
    return etree.fromstring(
        b"<TEI><teiHeader><profileDesc/></teiHeader><text><body/></text></TEI>"
    )


def _langues(root):
    return [
        (el.get("ident"), el.get("usage"), el.get("n"))
        for el in root.iter("language")
    ]


def _document(containers, lignes=()):
    """TEI minimal : sourceDoc transcrit, corps typé par langue."""
    zones = "\n".join(
        f'<zone xml:id="b{i}" type="{"RunningTitleZone" if lid.startswith("l2") else "MainZone"}">'
        f'<zone xml:id="{lid}" type="DefaultLine"><line>{texte}</line></zone></zone>'
        for i, (lid, texte) in enumerate(lignes)
    )
    return etree.fromstring(f"""<TEI>
  <teiHeader><profileDesc/></teiHeader>
  <sourceDoc><surface xml:id="f1">{zones}</surface></sourceDoc>
  <text><body><div>{containers}</div></body></text>
</TEI>""".encode("utf-8"))


def test_usage_is_a_percentage_measured_by_volume():
    """usage="1716" se lisait 1716 %. Et le compte du detecteur ne peut
    pas fonder un pourcentage : il compte UN par conteneur pour la langue
    principale et UN par SEGMENT etranger pour les autres. Une page
    francaise citant trente courts passages latins ferait 1 contre 30 —
    publie en 97 % de latin."""
    lignes = [("l1", " ".join(["mot"] * 500))] + [
        (f"n{i}", "breve citation latine") for i in range(30)
    ]
    corps = (
        '<ab xml:lang="fra"><lb corresp="#l1"/>' + " ".join(["mot"] * 500) + "</ab>"
        + "".join(
            f'<note xml:lang="lat"><lb corresp="#n{i}"/>breve citation latine</note>'
            for i in range(30)
        )
    )
    root = _document(corps, lignes)

    build_langusage(root, {"fra": 1, "lat": 30})

    # par conteneur : 1 contre 30, soit 97 % de latin. Par volume :
    # 500 mots francais contre 90 latins.
    parts = dict((ident, int(usage)) for ident, usage, _ in _langues(root))
    assert parts["fra"] > parts["lat"], parts
    assert 80 <= parts["fra"] <= 90, parts


def test_a_foreign_span_is_credited_to_its_own_language():
    """Un passage plus court qu'une ligne : ses mots vont a sa langue et
    sont retires de celle du conteneur."""
    root = _document(
        '<ab xml:lang="fra"><lb corresp="#l1"/>le peintre a ecrit '
        '<foreign xml:lang="lat">ars longa vita brevis</foreign></ab>',
        [("l1", "le peintre a ecrit ars longa vita brevis")],
    )

    build_langusage(root, {})

    parts = dict((ident, int(usage)) for ident, usage, _ in _langues(root))
    assert parts == {"fra": 50, "lat": 50}, parts


def test_a_language_actually_present_never_rounds_down_to_zero():
    """0 % declarerait absente une langue dont les segments <foreign>
    sont pourtant dans le texte."""
    lignes = [("l1", " ".join(["mot"] * 500)), ("l2", "graece")]
    root = _document(
        '<ab xml:lang="fra"><lb corresp="#l1"/>' + " ".join(["mot"] * 500) + "</ab>"
        '<ab xml:lang="grc"><lb corresp="#l2"/>graece</ab>',
        lignes,
    )

    build_langusage(root, {})

    parts = dict((ident, usage) for ident, usage, _ in _langues(root))
    assert parts["grc"] == "1"
    assert parts["fra"] == "100"


def test_the_measured_volume_and_its_unit_survive_in_n():
    root = _document(
        '<ab xml:lang="fra"><lb corresp="#l1"/>sept mots dans cette ligne precise ici</ab>',
        [("l1", "sept mots dans cette ligne precise ici")],
    )
    build_langusage(root, {})
    assert _langues(root) == [("fra", "100", "7 words")]


def test_without_any_xml_lang_the_detector_stats_are_the_fallback():
    """Un run sans detection de langue : la part est par conteneur, faute
    de mieux, et @n le dit."""
    root = _document('<ab><lb corresp="#l1"/>texte sans langue</ab>', [("l1", "texte")])
    build_langusage(root, {"fra": 3, "lat": 1})
    assert _langues(root) == [("fra", "75", "3 containers"), ("lat", "25", "1 containers")]


def test_the_language_volumes_add_up_to_the_extent():
    """Deux chiffres dans le meme header ne peuvent pas etre tous les deux
    la longueur du texte : la somme des @n doit faire l'<extent>."""
    from teille_douce.volumetry import language_volume, text_volume

    root = _document(
        '<ab xml:lang="fra"><lb corresp="#l1"/>quatre mots en francais</ab>'
        '<fw xml:lang="lat" type="RunningTitleZone"><lb corresp="#l2"/>DE PICTVRA</fw>',
        [("l1", "quatre mots en francais"), ("l2", "DE PICTVRA")],
    )

    # le titre courant est de l'apparat : hors du texte des deux cotes
    assert language_volume(root) == {"fra": 4}
    assert sum(language_volume(root).values()) == text_volume(root)
