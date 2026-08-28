# Tests unitaires de main.py -- parties testables sans lancer le pipeline.
# (Le comportement de bout en bout de main() est couvert par
# tests/test_e2e_pipeline.py, qui l'execute en sous-processus.)
#
# Run: venv/bin/python -m pytest tests/test_main.py -q
from datetime import datetime
from pathlib import Path

from main import _run_log_path


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
