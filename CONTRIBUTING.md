# Contributing

TEIlle-douce is a research pipeline for the
[Projet Grand Siècle](https://github.com/Grand-Siecle). Contributions are
welcome — bug reports, corpus feedback, and code.

Before anything else: the [developer guide](docs/developer-guide.md) explains
how to set up, how the test suite is organized, and which conventions are
load-bearing. This page is the short version plus the workflow.

## Getting set up

```bash
python3.12 -m venv venv && source venv/bin/activate
pip install -r requirements-dev.txt
venv/bin/python -m pytest          # green in ~15 s
```

Python 3.12 or later. Do not upgrade `lingua-language-detector`: it is pinned
exactly because language detection results shift between its minor releases and
the end-to-end test compares output byte for byte.

## Reporting a bug

The useful report contains the input that reproduces it. For a conversion bug,
that means:

- the ALTO file or files (or the smallest excerpt that still fails);
- the command you ran, including any `TDOUCE_*` variables;
- the relevant part of the run's `pipeline_*.log`;
- what the output contains versus what it should contain.

If the output is well-formed but wrong, `scripts/validate_tei.py --odd` on it
often names the problem with an XPath. Include that.

## Working on the code

### Branch and commit

Branch off `main`, one topic per branch, named `<type>/<topic>`:

```
feat/dates-and-tagset          fix/langusage-description-never-published
refactor/hyphen-and-config     perf/offline-build
docs/project-documentation     test/odd-coverage
chore/english-only-codebase    build/pin-dependencies
```

Commit subjects are **in English**, `<type>: <what changed, as a statement>`,
lowercase, no trailing period. Describe the effect, not the mechanism:

```
fix: the header erased the language-detection method it had just written
perf: validation was forty times slower than it needed to be
feat: give the dates a machine-readable value and name the POS tagset
```

`fix: bug` and `refactor: cleanup` say nothing a reader can use.

### Every fix carries a regression test

A fix without a test reproducing the original failure is not accepted. The test
must fail on the code before your change and pass after it — that is the only
evidence the bug is actually gone, and the only thing stopping it from coming
back.

For a bug found but **not** fixed, pin it instead:

```python
@pytest.mark.xfail(strict=True, reason="audit X.Y — <what should happen>")
```

`strict=True` means the day someone fixes it, the test XPASSes and the suite
fails, forcing the marker to be removed. A known bug therefore cannot be quietly
forgotten.

### Test first

Write the failing test, then the implementation. If the change alters the
output, the end-to-end golden diff is part of the test, and you regenerate the
reference deliberately:

```bash
venv/bin/python scripts/build_test_fixture.py --golden
```

Read that diff before committing it. It is the most direct statement of what
your change did.

### Adding an element to the output means adding it to the ODD

`schema/teille-douce.odd` declares a **closed** inventory of what the pipeline
emits. Emit a new element without declaring it and `tests/test_odd.py` fails, on
purpose. Recompile and commit source plus derivatives together:

```bash
venv/bin/python scripts/build_odd.py
git add schema/teille-douce.odd schema/teille-douce.rng schema/teille-douce.sch schema/teille-douce.svrl.xsl
```

Never edit `teille-douce.rng`, `teille-douce.sch` or `teille-douce.svrl.xsl` by hand;
`build_odd.py --check` catches it if you do. See [docs/schema.md](docs/schema.md).

### Coverage only goes up

`fail_under` in `pyproject.toml` is a ratchet, currently 89.5 %. If your work
adds meaningful coverage, raise it; never lower it. Measuring requires
`combine`, because multiprocessing workers and the e2e subprocesses each write
their own data file:

```bash
venv/bin/python -m coverage run -m pytest
venv/bin/python -m coverage combine
venv/bin/python -m coverage report
```

### The language of the code is English

Code, comments, docstrings, test names, log messages and CLI help are in
English. The exceptions are deliberate and narrow:

- **TEI editorial prose** written into the output header (`src/teiheader/prose.py`,
  `RESPONSIBILITY` in `config.py`) stays in French — it is addressed to readers
  of a French corpus.
- **The ODD's documentary prose** stays in French, marked on the `<div>`
  carrying it. Never above `<schemaSpec>`: an `xml:lang="fr"` there silently
  drops every project constraint from the generated Schematron.
- **Corpus data** — test fixtures quoting early modern French, the keyword lists
  in `src/lang/heuristics.py`, SegmOnto labels, CSV column names — is data, not
  prose. It stays as it is.

## Before opening a pull request

```bash
venv/bin/python -m pytest                                   # all green
venv/bin/python -m coverage run -m pytest \
  && venv/bin/python -m coverage combine \
  && venv/bin/python -m coverage report                     # ratchet holds
venv/bin/python scripts/build_odd.py --check                # derivatives match the ODD
```

And, if you touched anything that reaches the output, validate on real
documents rather than only on the fixture:

```bash
TDOUCE_OCR_DIR=OCR_test TDOUCE_OUTPUT_DIR=tei_test \
TDOUCE_NER=0 TDOUCE_ENRICHMENT=0 TDOUCE_MODERNIZE=0 python3 main.py
venv/bin/python scripts/validate_tei.py --odd tei_test/*.xml
```

The pull request description should say what changed and why, and name anything
a reviewer would otherwise have to discover: a moved golden file, a raised
ratchet, a recompiled schema.

CI runs the default suite plus the coverage ratchet on Python 3.12 for every
pull request. It installs `requirements-dev.txt` only — no NER models, no
services — so `e2e_full` deselects itself there. If your change needs those, say
so in the description and report what you ran locally.

## A note on what this pipeline claims

Much of the design turns on one distinction: what the source asserts versus what
a machine inferred. Curated persons go in `<particDesc>`, inferred ones in
`<standOff>`. A modernized reading carries `@resp` and `@cert`, and never
replaces the original. A zone type the pipeline does not recognize gets a
container that claims no structure, rather than a guess.

New features are expected to hold that line. If a change would make a machine
inference indistinguishable from an editorial statement, it needs a different
design — and the reverse is also true: dropping content silently to keep the
output tidy is worse than emitting it with an honest label.
