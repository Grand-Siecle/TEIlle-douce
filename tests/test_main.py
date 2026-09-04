# Tests unitaires de main.py -- parties testables sans lancer le pipeline.
# (Le comportement de bout en bout de main() est couvert par
# tests/test_e2e_pipeline.py, qui l'execute en sous-processus.)
#
# Run: venv/bin/python -m pytest tests/test_main.py -q
from datetime import datetime
from pathlib import Path
from zipfile import ZipFile

from teille_douce.cli.app import build_parser
from teille_douce.cli.run import _run_log_path, expand_archives


def _parse_args(argv):
    """Argument parsing moved to teille_douce.cli.app; the behaviour these
    tests pin did not."""
    return build_parser().parse_args(argv)


# =============================================================================
# Analyse des arguments -- audit 2.5 : --skip-existing
# =============================================================================

def test_parse_args_skip_existing_flag():
    assert _parse_args(["--skip-existing"]).skip_existing is True
    assert _parse_args([]).skip_existing is False


def _zip_valide(chemin):
    """Archive ZIP valide, ALTO minimal. L'extraction se fait *dans* target :
    l'archive contient content/..., pas de dossier racine."""
    with ZipFile(chemin, "w") as zf:
        zf.writestr("content/data/doc_1/f1.xml", "<alto/>")
    return chemin


# =============================================================================
# expand_archives -- audit 2.2 : ZIP corrompu, extraction atomique
# =============================================================================

def test_expand_archives_extracts_valid_zip(tmp_path):
    _zip_valide(tmp_path / "DOC0001.zip")

    ready, failed = expand_archives(tmp_path)

    assert failed == []
    assert ready == [tmp_path / "DOC0001"]
    assert (tmp_path / "DOC0001" / "content" / "data" / "doc_1" / "f1.xml").exists()


def test_expand_archives_corrupt_zip_is_skipped_and_reported(tmp_path):
    """Audit 2.2 : un ZIP corrompu ne tue plus le run, il est signale."""
    (tmp_path / "CASSE.zip").write_bytes(b"pas un zip du tout")
    _zip_valide(tmp_path / "DOC0001.zip")

    ready, failed = expand_archives(tmp_path)

    # le ZIP sain est extrait malgre le corrompu
    assert ready == [tmp_path / "DOC0001"]
    assert len(failed) == 1 and failed[0][0] == "CASSE.zip"
    # aucun dossier partiel laisse derriere
    assert not (tmp_path / "CASSE").exists()
    assert not (tmp_path / "CASSE.extracting").exists()


def test_expand_archives_truncated_zip_leaves_no_partial_dir(tmp_path):
    """Audit 2.2 : une archive tronquee ne laisse pas de dossier partiel que
    target.exists() prendrait pour une extraction complete au run suivant."""
    entier = _zip_valide(tmp_path / "autre.zip")
    donnees = entier.read_bytes()
    (tmp_path / "TRONQUE.zip").write_bytes(donnees[: len(donnees) // 2])
    entier.unlink()

    ready, failed = expand_archives(tmp_path)

    assert ready == []
    assert len(failed) == 1 and failed[0][0] == "TRONQUE.zip"
    assert not (tmp_path / "TRONQUE").exists()
    assert not (tmp_path / "TRONQUE.extracting").exists()


def test_expand_archives_cleans_leftover_extracting_dir(tmp_path):
    """Un run interrompu laisse DOC.extracting : il est remplace, jamais
    liste comme document, et l'extraction repart de zero."""
    _zip_valide(tmp_path / "DOC0001.zip")
    reste = tmp_path / "DOC0001.extracting" / "content"
    reste.mkdir(parents=True)
    (reste / "vieux.xml").write_text("<alto/>")

    ready, failed = expand_archives(tmp_path)

    assert failed == []
    assert ready == [tmp_path / "DOC0001"]
    assert not (tmp_path / "DOC0001.extracting").exists()
    assert not (tmp_path / "DOC0001" / "content" / "vieux.xml").exists()


def test_expand_archives_existing_dir_not_reextracted(tmp_path):
    """Un dossier deja extrait n'est pas re-extrait (garde target.exists())."""
    _zip_valide(tmp_path / "DOC0001.zip")
    deja = tmp_path / "DOC0001" / "content"
    deja.mkdir(parents=True)
    (deja / "present.xml").write_text("<alto/>")

    ready, failed = expand_archives(tmp_path)

    assert failed == []
    assert ready == [tmp_path / "DOC0001"]
    # le contenu existant n'a pas ete ecrase par l'archive
    assert (tmp_path / "DOC0001" / "content" / "present.xml").exists()
    # et l'archive n'a PAS ete re-extraite par-dessus (assertion
    # discriminante : une re-extraction ajouterait f1.xml sans toucher
    # au fichier sentinelle ci-dessus)
    assert not (tmp_path / "DOC0001" / "content" / "data" / "doc_1" / "f1.xml").exists()


def test_expand_archives_empty_zip_is_not_reported_corrupt(tmp_path):
    """Une archive structurellement valide mais vide n'est pas une archive
    corrompue : pas d'echec signale, pas de code retour non nul a la cle."""
    with ZipFile(tmp_path / "VIDE.zip", "w"):
        pass

    ready, failed = expand_archives(tmp_path)

    assert failed == []
    # le dossier extrait existe (vide), mais sans XML il n'est pas un document
    assert (tmp_path / "VIDE").is_dir()
    assert not (tmp_path / "VIDE.extracting").exists()


def test_expand_archives_zlib_corruption_mid_stream_is_skipped(tmp_path):
    """La corruption au milieu du flux compresse (repertoire central intact)
    sort de zipfile en zlib.error, pas en BadZipFile : elle doit etre
    rattrapee comme les autres."""
    import zipfile as zf_mod
    payload = ("lorem ipsum " * 2000).encode()
    with ZipFile(tmp_path / "ROT.zip", "w", compression=zf_mod.ZIP_DEFLATED) as zf:
        zf.writestr("content/data/doc_1/f1.xml", payload)
    donnees = bytearray((tmp_path / "ROT.zip").read_bytes())
    # ecrase 64 octets au milieu du membre compresse, loin du repertoire central
    milieu = len(donnees) // 3
    donnees[milieu:milieu + 64] = b"\x00" * 64
    (tmp_path / "ROT.zip").write_bytes(bytes(donnees))

    ready, failed = expand_archives(tmp_path)

    assert ready == []
    assert len(failed) == 1 and failed[0][0] == "ROT.zip"
    assert not (tmp_path / "ROT").exists()
    assert not (tmp_path / "ROT.extracting").exists()


def test_extract_archive_cleans_tmp_dir_when_extraction_fails(tmp_path, monkeypatch):
    """Un echec en pleine extraction (disque plein, interruption) ne laisse
    pas de dossier .extracting orphelin derriere lui."""
    from zipfile import ZipFile as RealZipFile
    _zip_valide(tmp_path / "DOC0001.zip")

    def extractall_explose(self, path=None, *a, **k):
        (Path(path) / "partiel.xml").write_text("<alto/>")
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(RealZipFile, "extractall", extractall_explose)

    ready, failed = expand_archives(tmp_path)

    assert len(failed) == 1 and failed[0][0] == "DOC0001.zip"
    assert not (tmp_path / "DOC0001").exists()
    assert not (tmp_path / "DOC0001.extracting").exists()


# =============================================================================
# _run_log_path -- audit 2.10 : logs horodates
# =============================================================================


def test_run_log_path_is_timestamped_per_run():
    """Audit 2.10 : chaque run ecrit son propre log, plus d'ecrasement."""
    base = Path("pipeline.log")
    horodate = _run_log_path(base, datetime(2026, 8, 28, 9, 30, 0))
    assert horodate == Path("pipeline_20260828_093000.log")


def test_run_log_path_two_runs_two_files():
    base = Path("pipeline.log")
    matin = _run_log_path(base, datetime(2026, 8, 28, 9, 30, 0))
    soir = _run_log_path(base, datetime(2026, 8, 28, 21, 0, 5))
    assert matin != soir
