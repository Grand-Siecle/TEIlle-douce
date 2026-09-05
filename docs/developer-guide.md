# Developer guide

For working *on* the pipeline. If you only want to run it, the
[user guide](user-guide.md) is enough; for how it is put together, see
[architecture.md](architecture.md).

- [Setting up](#setting-up)
- [The test suite](#the-test-suite)
- [The fixture](#the-fixture)
- [Conventions that are load-bearing](#conventions-that-are-load-bearing)
- [Adding a feature end to end](#adding-a-feature-end-to-end)
- [Debugging](#debugging)
- [Continuous integration](#continuous-integration)

---

## Setting up

```bash
git clone https://github.com/rayondemiel/TEIlle-douce.git
cd TEIlle-douce
python3.12 -m venv venv
source venv/bin/activate
pip install -e '.[dev]'   # core + pytest + coverage + saxonche
venv/bin/python -m pytest             # should be green in ~15 s
```

`requirements-dev.txt` is exactly what CI installs. It does **not** include the
NER stack: the default suite runs with no models, no services and no network.

Two optional extras:

```bash
# NER work — several GB
pip install -e '.[ner]'

# tei_all validation in the tests (~1 MB, not versioned)
curl -sSL -o tei_all.rng https://tei-c.org/release/xml/tei/custom/schema/relaxng/tei_all.rng
```

`tei_all.rng` at the project root is picked up automatically; put it elsewhere
and point `TDOUCE_TEI_RNG` at it. Without it the tei_all tests skip with an
explicit reason rather than passing silently.

For a quick manual run on a handful of real documents, `OCR_test/` holds
symlinks into the private corpus (gitignored, as are `tei_test/`, `tei_full/`,
`tei_mini/`):

```bash
teille-douce run --fast -i OCR_test -o tei_test
```

## The test suite

682 tests over 35 files, 12,400 lines. Configuration lives in `pyproject.toml`
(`testpaths`, `pythonpath`, markers, coverage).

```bash
# Default: unit tests + short end-to-end. ~15 s, no network, no models.
venv/bin/python -m pytest

# Full end-to-end, with linguistic annotation.
# Needs PyHellen (localhost:8000) and VieuxParler (localhost:8011);
# auto-skips with an explicit reason when they are down.
venv/bin/python -m pytest -m e2e_full

# One file, verbose
venv/bin/python -m pytest tests/test_body_build.py -v
```

### Coverage

```bash
venv/bin/python -m coverage run -m pytest
venv/bin/python -m coverage combine     # mandatory
venv/bin/python -m coverage report
```

`combine` is not optional. `sourcedoc/builder.py` runs inside `multiprocessing`
workers and the end-to-end tests launch `teille_douce/cli/run.py` as a subprocess; each writes
its own data file, and without `combine` those lines look uncovered. The
`concurrency`, `parallel` and `sigterm` options in `pyproject.toml` are what
make the fork data collectable at all.

`precision = 1` matters too: at the default precision coverage rounds the total
*before* comparing it to `fail_under`, and 80.51 % would clear a bar set at
"80.7".

### The two end-to-end modes

Both run on the versioned fixture, never on the real corpus.

| Marker | Covers | Cost |
|---|---|---|
| `e2e` | Base TEI: header, `sourceDoc`, `body`, notes, language detection, ODD validation, run-level behaviour (a broken document, `--skip-existing`) | ~10 s |
| `e2e_full` | All of the above plus PyHellen enrichment, modernization and NER | ~15 s |

`e2e_full` is deselected by `addopts` in `pyproject.toml`; a plain `pytest` runs
everything else.

The e2e tests launch `teille_douce/cli/run.py` as a real subprocess and compare its output to a
reference file **byte for byte**, normalizing only the generation date. Since
identifiers became `uuid5`-derived this is possible: two runs over the same
input produce identical files. It also means any intended change to the output
requires regenerating the reference:

```bash
venv/bin/python scripts/build_test_fixture.py --golden
```

## The fixture

`tests/fixtures/alto_min/` is a versioned 8-page ALTO document (175 KB),
distilled from the real corpus. It is the **smallest set that still exercises
every branch the pipeline has**, chosen by minimal set cover over the SegmOnto
labels actually present in the 27-volume corpus.

| Page | Brings |
|---|---|
| f1 | MainZone, MarginTextZone, DropCapitalZone, NumberingZone, QuireMarksZone, RunningTitleZone, `¬` hyphenation |
| f2 | GraphicZone |
| f3 | TitlePageZone |
| f4 | note call mark (`*`) + MarginTextZone |
| f5 | StampZone |
| f6 | CustomZone |
| f7 | plain-hyphen line break (`mainte-`) |
| f8 | HeadingLine — the only page in the entire corpus carrying a line label other than DefaultLine |

Regenerating it needs the private `OCR/` corpus:

```bash
venv/bin/python scripts/build_test_fixture.py            # the fixture
venv/bin/python scripts/build_test_fixture.py --golden   # the reference output
```

`tests/fixtures/metadata_livre.csv` and `metadata_personne.csv` are minimal
versioned metadata — without them the header would stay full of placeholders and
the e2e tests would prove much less. The `*.csv` rule in `.gitignore` is negated
for `tests/fixtures/`.

If you touch a page of the fixture, expect the golden diff to move. Read that
diff: it is the most direct statement of what your change did to the output.

## Conventions that are load-bearing

These are not style preferences. Each one exists because its absence caused a
concrete failure.

### Coverage is a ratchet

`fail_under` in `pyproject.toml` goes **up** with each batch of tests, never
down. It stands at 89.5 %. The comment above it records the history batch by
batch, which is also how you can tell whether a drop is your doing.

### Known bugs are pinned, not worked around

Every finding in `docs/superpowers/rapport_audit.md` gets a test describing the
behaviour expected *after* the fix, marked:

```python
@pytest.mark.xfail(strict=True, reason="audit X.Y — <what should happen>")
```

The suite stays green today. The day the bug is fixed, the test **XPASSes** —
which `strict=True` turns into a failure — forcing whoever fixed it to remove
the marker. A bug therefore cannot be fixed and forgotten, and cannot be
silently reintroduced.

### Tag assertions go through `qlocal()`

Bare and namespaced tags coexist in the same tree, because different phases
build elements differently. `el.tag == "ab"` is therefore unreliable. The 14
test files that inspect tags each define a local helper:

```python
def qlocal(el):
    return etree.QName(el).localname
```

Production code uses `utils.xml.local_tag()` for the same reason.

### Every phase must report what it lost

Skipped pages, failed containers, rejected modernizations, filtered entities: all
counted, all printed. A counter left at zero because the server died mid-run must
not be indistinguishable from a document that had nothing to process. Several
findings in the audit report are exactly this failure mode.

A phase also has to say when it is WORKING and when it has stopped, or the
live panel draws nothing for the part of the run it exists to narrate. Every
phase in `_process_document` calls `_phase_progress` while it runs and one of
`_phase_done` / `_phase_lost` when it ends — a phase left RUNNING keeps a
spinner turning beside a count that will never change again, and one that never
starts leaves a blank where its line belongs.
`tests/test_panel_is_alive.py` drives the real function and fails on either.

This is no longer a rule a new phase can forget. `teille_douce/report/counts.py`
enforces it: `render_count(n, denominator, unit)` **raises** when the denominator
is missing, because a bare integer is precisely what makes those two zeros one
string. `render_phase_loss` chooses its wording from the phase's *state* and not
from whether a number is zero — which is how "0" came to mean four different
things in one column — and reserves the red `LOST` for a phase that lost the
ground under it, with a cause and a scope or it raises.

### A loss belongs to one of three blocks

The word "lost" covered three unrelated causes with three different remedies,
so `teille_douce/report/record.py` splits them:

| | | |
|---|---|---|
| **the source was defective** | nothing the pipeline could do | an unreadable ALTO page, a corrupt archive, duplicate ALTO ids (repaired) |
| **withheld on purpose** | the guards did their job | a modernized reading under the similarity floor, an entity under 0.6, a container more than 20 % of whose block could not be anchored |
| **lost to an incident** | this needs a human | a service that died, a container that raised, a whole phase or a document lost |

What block 2 counts, precisely: a modernized reading the divergence guard
refused (`readings_rejected`, from `modernize.py`), an entity that reached
resolution and was pruned below the confidence floor (`entities_filtered`,
from `ner_filter.filter_resolved_entities`), and a container more than 20 % of
whose block could not be anchored. The earlier span- and POS-level filters run
before anything is an entity; their counts stay in the log, because a candidate
that never became an entity was not withheld from the output.

Block 3 is the definition of "important": it is what `--fail-on incident`
counts. Block 2 never counts at any level — a guard that rejects a
hallucination did its job, and failing the run for it would punish the chain
for being honest. A defect that was *repaired* carries `Code.repaired` and stays
out of loss arithmetic entirely; duplicate ALTO ids are the largest figure this
corpus produces, and counting them as losses made a clean run report six
thousand of them.

Every loss carries a document, the step it happened in, and a typed locator.
`Kind.DOC` is the floor and an admission the summary prints rather than hides.
What is never recorded is an internal index: `container 12` and `batch_start
640` are what the pipeline used to say, and neither is something a reader can
open. A page id is worth four addresses at once, because `surface/@xml:id` **is**
the ALTO file stem.

### The panel is a pure function

`teille_douce/report/panel.py` takes `(state, width, capabilities)` and returns
lines of `(text, style)`. No console, no clock, no I/O — which is what makes six
moments of a four-hour run testable in milliseconds, and what makes the
no-colour parity structural: strip every style and the panel says the same
thing. Rich does the live redraw, the colour degradation and the terminal
detection, and none of the layout; composing the lines here is what makes the
hundred-column cap an assertion rather than a hope.

### Adding an element to the output means adding it to the ODD

`schema/teille-douce.odd` is the normative description of what the pipeline emits,
with a **closed** element inventory. `tests/test_odd.py` fails until a newly
emitted element is declared there. See [schema.md](schema.md).

### Settings validate their input

Every setting is one row of the declaration table in
`teille_douce/settings.py` — attribute, `TDOUCE_*` name, TOML key, converter,
default — and the converter gates all four layers, not just the environment. It
rejects unusable values (empty, unreadable, non-finite, out of range), keeps the
default, says what the run uses instead, and records the refusal. A value typed
as a *flag* is a usage error rather than a fallback: keeping a default the
operator explicitly overrode would answer a different question.

A new setting that parsed with a bare `float(os.environ[...])` would accept
`nan`, and a NaN threshold turns every comparison against it into `False`, which
silently disables the guard reading it.

One table, so the layers, the documentation and the CLI cannot drift apart:
tests hold the user guide's variable and config-key columns to
`Settings.environment_variables()` and `Settings.config_keys()`.

## Adding a feature end to end

The order below is the one the conventions above imply.

1. **Write the failing test first.** Put it in the file covering that module, or
   create one. If the feature changes the output, the e2e golden diff is part of
   the test.
2. **Implement it** in the module that owns that responsibility. If the change
   makes a file grow past what fits in your head, that is a signal to split it,
   not to add a section header.
3. **Declare any new element or attribute in the ODD**, recompile, and commit
   source and derivatives together:
   ```bash
   venv/bin/python scripts/build_odd.py
   venv/bin/python -m pytest tests/test_odd.py tests/test_e2e_pipeline.py
   ```
4. **Regenerate the golden** if the output changed on purpose:
   ```bash
   venv/bin/python scripts/build_test_fixture.py --golden
   ```
   and read the diff before committing it.
5. **Raise the ratchet** if you added meaningful coverage:
   ```bash
   venv/bin/python -m coverage run -m pytest && venv/bin/python -m coverage combine
   venv/bin/python -m coverage report      # then bump fail_under to the new figure
   ```
6. **Validate real output**, not just the fixture:
   ```bash
   teille-douce run --fast -i OCR_test -o tei_test
   venv/bin/python scripts/validate_tei.py --odd tei_test/*.xml
   ```

`CONTRIBUTING.md` has the pre-PR checklist in short form.

## Debugging

**Turn on verbose logging.** `DEBUG = True` in `teille_douce/config.py` sends DEBUG records to
the console; they always go to the run's `pipeline_*.log` regardless. Third-party
HTTP libraries are pinned to WARNING because they were filling the log with
megabytes of connection traces.

**Work on one document.** Point `TDOUCE_OCR_DIR` at a directory holding a
single volume — or a single page. Most bugs reproduce on one page.

**Isolate the phase.** The three annotation phases are independent switches. If
the output is wrong with everything on, turn them all off: if the problem is
still there it is in the base build, which is fast to iterate on.

**Read `<sourceDoc>` first when the body is wrong.** `body/text.py` reads the
finished `sourceDoc`, never the ALTO. A missing `<ab>` almost always means a
missing or mislabelled zone upstream.

**Use `@corresp` to navigate.** Every body element points back at its
`sourceDoc` zone, every `<lb/>` at its line. Take the id from the element that
looks wrong and search for it in the same file — you land on the coordinates and
the IIIF crop URL of the region, which you can open in a browser.

**Validate before hypothesizing.** `scripts/validate_tei.py --odd` on the
suspect file often names the problem directly, with an XPath.

**Expect multiprocessing to hide things.** Exceptions inside page workers are
collected, not raised where you can see them. `tests/test_sourcedoc_builder.py`
covers that path; when in doubt, reduce to one page so the pool is trivial.

## Continuous integration

`.github/workflows/ci.yml`, on every pull request and every push to `main`:

1. Python **3.12 pinned** — on 3.10, pip resolves `lingua-language-detector`
   2.1.1 instead of 2.2.0, language detection changes, and the golden comparison
   fails on different `usage=` values in `<langUsage>`.
2. `pip install -e '.[dev]'` — no NER models, no services, so
   `e2e_full` deselects itself.
3. `tei_all.rng` downloaded and cached. The schema is ~1 MB and not versioned;
   without it the tei_all tests would skip and TEI conformance would be checked
   by nothing.
4. `coverage run -m pytest`, then `combine`, then `report` — which applies the
   `fail_under` ratchet. A PR that lowers coverage fails.

Timeout is 15 minutes and concurrent runs on the same ref cancel each other.
