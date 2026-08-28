"""
Active la mesure de couverture dans les sous-processus lances par les tests E2E.

`coverage` n'observe pas un processus cree par `subprocess.run`. Le mecanisme
officiel consiste a faire appeler `coverage.process_startup()` au demarrage de
l'interpreteur fils ; ce module est place sur le PYTHONPATH du fils par
`tests/test_e2e_pipeline.py::lancer_pipeline`, uniquement quand une mesure est
en cours dans le parent.

Sans COVERAGE_PROCESS_START dans l'environnement, l'appel est un no-op : ce
fichier est donc inoffensif hors mesure.
"""

try:
    import coverage

    coverage.process_startup()
except Exception:  # coverage absent ou indisponible : on n'empeche pas le run
    pass
