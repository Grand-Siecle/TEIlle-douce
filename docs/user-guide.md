# User guide

Everything needed to run TEIlle-douce on your own documents and read what comes
out. For how the pipeline works internally, see
[architecture.md](architecture.md).

- [Installation](#installation)
- [Preparing your input](#preparing-your-input)
- [The commands](#the-commands)
- [Before you run: `teille-douce check`](#before-you-run-teille-douce-check)
- [Running the pipeline](#running-the-pipeline)
- [Configuration](#configuration)
- [The annotation services](#the-annotation-services)
- [Reading the output](#reading-the-output)
- [Validating the output](#validating-the-output)
- [Going back to a run: `teille-douce report`](#going-back-to-a-run-teille-douce-report)
- [Shell completion](#shell-completion)
- [Troubleshooting](#troubleshooting)

---

## Installation

Python **3.12 or later** is required. `lingua-language-detector` 2.2 will not
install on anything older, and language detection results change between its
minor releases — which is why the version is pinned exactly.

```bash
git clone https://github.com/rayondemiel/TEIlle-douce.git
cd TEIlle-douce
python3.12 -m venv venv
source venv/bin/activate
pip install -e .
```

This installs the `teille-douce` command. Install it **editable** (`-e`): the
package then points at your clone, so `teille_douce/config.py` — which is meant
to be edited — stays the file you actually edit rather than a copy buried in
`site-packages`.

Three dependency tiers. The versions live in `pyproject.toml`; the
`requirements*.txt` files are one-line references kept so that the commands in
this documentation and in CI keep working:

| Command | Adds | Use it when |
|---|---|---|
| `pip install -e .` | lxml, pandas, rich, lingua, httpx | Always |
| `pip install -e '.[dev]'` | pytest, coverage, saxonche | You run the tests, or recompile the ODD |
| `pip install -e '.[ner]'` | torch, transformers, gliner, flair, huggingface_hub | You want named-entity recognition (several GB of wheels and models) |

`python3 main.py` still works, and does exactly what `teille-douce` does — the
launcher at the repository root is kept for wrapper scripts written before the
package existed.

`saxonche` is a pip wheel providing XSLT 2.0 with no JVM. It is needed to apply
the Schematron half of the project schema and to recompile the ODD; RELAX NG
validation works without it.

## Preparing your input

### Document directories

The pipeline processes **one directory per volume** inside `OCR/`. Each
directory holds the ALTO XML files of that volume, at any depth:

```
OCR/
├── LIV0044_reconciled/          # a volume, already unpacked
│   ├── f1.xml
│   ├── f2.xml
│   └── …
├── LIV0031_t2_reconciled.zip    # a volume still zipped — unpacked automatically
└── LIV0031_t2_reconciled/       # …into a sibling directory of the same name
```

ZIP archives are expanded before processing. Extraction is atomic: an
interrupted run leaves no half-unpacked directory that the next run would
mistake for a complete one. A corrupt archive is reported and skipped, and the
run continues.

**ALTO version:** ALTO 4 (`http://www.loc.gov/standards/alto/ns-v4#`). The
pipeline reads `<Page>`, `<TextBlock>`, `<TextLine>`, `<String>`, `<Polygon>`,
`<Baseline>` and the `TAGREFS` that carry SegmOnto labels.

**Page ordering** comes from the first number in each filename — `f12.xml`,
`page_001.xml`, `001.xml` all work. A file with no number in its name is placed
at the end and a warning names it. Two files can share that number (`f12.xml`
and `f12-np.xml` both give 12): both pages are converted, in discovery order,
and a warning names them — but they then share one IIIF view in their zones'
`@source`, and `surface/@n` is that same number, so rename them if either
matters.

### Zone labels

Layout labels must follow the [SegmOnto](https://segmonto.github.io/)
controlled vocabulary, carried by ALTO's `TAGREFS` in the form
`MainZone:column#1`. The pipeline recognizes 15 zone types and 6 line types;
`schema/teille-douce.odd` holds the closed list. An unknown label does not lose
text — it falls back to a plain `<ab type="…">` — but it gets no dedicated TEI
structure either.

### Document names and metadata matching

A directory name is parsed into an internal identifier and an optional volume
marker:

| Directory | Internal id | Volume | `xml:id` of the TEI root |
|---|---|---|---|
| `LIV0008_reconciled` | `LIV0008` | — | `LIV0008` |
| `LIV0002a_reconciled` | `LIV0002` | `a` | `LIV0002a` |
| `LIV0031_t2_reconciled` | `LIV0031` | `t2` | `LIV0031_t2` |

The internal id is what links the document to its catalogue row.

### The metadata CSVs

Two semicolon-delimited CSV files at the project root. Both are optional — the
pipeline runs without them and fills the header with explicit placeholders
(`Information not available.`, `Metadata not found in catalogue.`) rather than
inventing values.

**`metadata_livre.csv`** — one row per volume, matched on the `BDD` column
against the internal id:

| Column | Goes into |
|---|---|
| `BDD` | `<altIdentifier><idno type="internal">` and the match key |
| `Titre_long`, `Titre_abrege` | `<title>` |
| `ID_auteur`, `ID_imprimeurs`, `ID_Editeur`, `ID_Traducteurs`, `ID_libraires` | `<author>`, `<respStmt>` — values are `PERSxxxx` keys into the person CSV |
| `Lieu_publication` | `<pubPlace>` |
| `Date_01`, `Date_02` | `<date>` and `<creation><date>`, normalized to `@when`/`@from`–`@to` |
| `Localisation`, `Cote` | `<settlement>`, `<repository>`, `<idno>` |
| `ARK` | `<idno type="ark">` and the catalogue `<ptr>` |
| `manifest_iiif` | `<idno type="iiif">`, and the IIIF image base is derived from it |
| `langues` | seeds `<langUsage>` |
| `Sujet`, `Matiere` | `<keywords scheme="#catalogue-subjects">` |
| `Format` | `<physDesc><objectDesc>` |

**`metadata_personne.csv`** — one row per person, keyed on `BDD` (`PERSxxxx`),
becoming `<person>` entries in `<particDesc><listPerson>`: `Prenoms`, `Nom`,
`Sexe`, `RoleName`, `GenName`, `Surnoms`, `Annee_naissance`/`Ville_naissance`,
`Annee_mort`/`Ville_mort`, `Confession`, `Formation`, `Professions`, `ISNI`,
`ARK`, `Notes`.

Anyone referenced from the book CSV gets an `xml:id`, and every mention in the
document points at it with `@ref`.

### IIIF images

Page images are referenced, never copied. Two ways to supply them:

1. **A Gallica manifest URL** in the `manifest_iiif` column. The IIIF Image API
   base is derived from it, and each page gets a `<graphic url="…"/>` plus a
   `@source` crop URL on every zone.
2. **A mapping CSV** dropped in the document directory, auto-detected by name
   (`*iiif*.csv`, `*mapping*.csv`, `*manifest*.csv`). No header row; column 0 is
   the IIIF URL, column 1 a source identifier, column 2 the ALTO filename. Files
   over 10 MB are skipped, and a mapping matching fewer than 30 % of the sampled
   filenames is rejected rather than half-applied.

For a non-Gallica server whose Image API base cannot be guessed from its
manifest URL, set `IIIF_URI["image_base"]` in `teille_douce/config.py`.

## The commands

```
teille-douce run         convert ALTO volumes to TEI                (the default)
             check       is this installation usable, and what will it cost
             validate    check produced TEI against the project schema
             info        which layer set this value
             report      go back to a run that is over
             completion  a shell completion for bash, zsh or fish
             odd         compile the project schema from its ODD    (maintainers)
             fixture     rebuild the versioned test fixture         (maintainers)
```

With no command, `run` is assumed: `teille-douce`, `teille-douce LIV0044` and
`teille-douce --fast` all convert. Five of the commands write nothing at all —
everything but `run`, `odd` and `fixture` — so they are safe to type on a
machine mid-run. `teille-douce --help` lists them; each has its own `--help`.

## Before you run: `teille-douce check`

A full conversion takes tens of minutes. `check` answers, in about two
seconds and without writing anything, the questions that used to need one:

```bash
teille-douce check              # is this installation usable, and what will it cost?
teille-douce check -i OCR_test  # ask about the input a run would be given
teille-douce check --strict     # the same, and exit 1 if there is anything to look at
teille-douce check --no-probe   # do not ask the two services anything
```

`-i/--input`, `-o/--output`, `--metadata` and `--persons` mean what they mean
on `run`, so a preflight can be asked about exactly what the run will be
given. Everything else comes from the same four configuration layers.

It reports what it found — volumes, pages, archives waiting to be unpacked,
how much of the corpus the catalogue covers, whether the persons table
loaded, what each service answered — and then, under **needs attention**,
each thing worth looking at *and what a run would do about it*:

```
  input      OCR_test                                        2 volumes · 18 pages
  output     tei_test                                                    writable
  catalogue  metadata_livre.csv           1 rows · 1 matched · 1 unmatched
             metadata_personne.csv                                     2 persons
  services   VieuxParler modernization                                not probed
             PyHellen    enrichment                                   not probed
             NER models  entity recognition                                   up

  needs attention
    LIV0326_v1_reconciled  no catalogue row for 'LIV0326_v1_reconciled'
                           — its header would keep every placeholder
    LIV0326_v1_reconciled  2 files claim page 2 (f2-np.xml, f2.xml)
                           — the page numbering is ambiguous
    LIV0326_v1_reconciled  the IIIF mapping would be refused — matches 0% of …
                           — the zones would carry no @source
    LIV9001_reconciled     1 file with no page number in the name (plate.xml)
                           — it is placed last and takes the sentinel IIIF view
    LIV0099_vide           the directory holds no ALTO
                           — nothing would be converted from it

  usable, with 5 things to look at      teille-douce check --strict fails on these
```

Four of the ten [troubleshooting](#troubleshooting) entries below are
diagnoses this poses before the run rather than after it. The analyses are
the run's own — the same page ordering, the same catalogue lookup, the same
IIIF detection — because a preflight that disagreed with the run it precedes
would be worse than none.

**Exit codes.** `0` nothing to say · `1` usable, and `--strict` was asked
for · `3` unusable: no input directory, no volume in it, an output that
cannot be written, or a path that is configured and unreadable. Degraded is
not unusable: seventeenth-century OCR is imperfect by nature, and a run that
stopped on that would never start. `--strict` is for a CI that wants the
distinction to be fatal.

## Running the pipeline

```bash
source venv/bin/activate
teille-douce run
```

Output goes to `tei_output/`, one `<name>.tei.xml` per volume. Progress is shown
per document and per page.

`run` takes the volumes to convert as arguments — a directory name, the
internal id parsed from it, or a glob:

```bash
teille-douce run LIV0044              # one volume
teille-douce run 'LIV003*' -x LIV0038 # a glob, minus one
teille-douce run --fast --dry-run     # what would happen, writing nothing
```

| Option | Effect |
|---|---|
| `DOC ...` | Volumes to convert. A selector matching nothing stops the run — a typo must not look like an empty corpus. |
| `-x, --exclude PATTERN` | Drop volumes after selection. Repeatable. **A pattern matching nothing stops the run**, like a selector: an exclusion that silently misses converts at full cost the volume it was meant to hold back. A standing `-x` in a wrapper therefore has to be removed the day its volume leaves the corpus. |
| `--limit N` | Convert at most N of those selected, in order. |
| `--skip-existing` / `--force` | Skip volumes already converted / convert them anyway. |
| `--retry-failed` | Convert only the volumes the last run reported `FAILED`, read from its `run.json`. Exits 3 when the last run had no failures — there is no selector, and nothing to retry is not a typo. |
| `-i, --input` · `-o, --output` | Input and output directories. |
| `--entities` · `--metadata` · `--persons` | Entity CSVs, and the two catalogues. |
| `--fast` | No annotation phase at all: no service, no model. |
| `--phases LIST` | Set the phases outright: `enrich,modernize,ner`, `all` or `none`. |
| `--enrich`/`--no-enrich`, and likewise for `modernize` and `ner` | Add or remove one phase from the current set. |
| `--pyhellen URL` · `--vieuxparler URL` · `--health-timeout S` | The two services. |
| `-j, --jobs N` · `--batch-size N` · `--concurrency N` | Workers, lines per request, in-flight requests. |
| `--device DEV` | Where the NER models run: `auto` (the default: a CUDA GPU if there is one, the CPU otherwise), `cpu`, `cuda`, `cuda:1`, `mps`. Naming one matters on a shared GPU somebody else has filled and on a machine with more than one — neither of which the pipeline can guess. Apple silicon is not chosen automatically: ask for `mps`. The run says which device it chose before loading several gigabytes of model. |
| `--no-probe` | Do not probe the services; assume they answer. |
| `--require-services` | A phase whose service is down is fatal (exit 3) **before anything is written**, instead of a warning and a run without that annotation. |
| `--fail-on never\|incident\|loss` · `--strict` | What counts as a failure at the end of a run. `never` (the default) means losses do not change the exit status. `incident` fails on the third block — a service died, a container raised, a whole phase or a document was lost — which is the block titled *this needs a human*. `loss` adds the defects of the source, which on seventeenth-century OCR is never empty: that level is for a CI freezing an already-clean corpus, not for a daily run. The second block never counts at any level — a guard that rejects a hallucination did its job. `--strict` is `--fail-on incident`. |
| `--max-page-loss PCT` | A volume losing more than PCT % of its pages is a failure rather than a degraded success. The chain already fails a document whose pages are *all* unusable; this lowers that implicit 100. |
| `--dashboard` · `--plain` | Draw the live panel, or print the per-document lines without one. Chosen automatically: a panel in a terminal, plain output in a pipe, a CI log (`CI`, `GITHUB_ACTIONS`, …), a `TERM=dumb`, a window under 56×16 (the panel is fifteen rows before a single incident), or under `-q`. `TDOUCE_UI=auto\|plain\|dashboard` sits between the automatic rules and these flags. The end-of-run report is rendered from the same record either way, so it is identical to the byte. |
| `-n, --dry-run` | Resolve everything, list the plan, write nothing. |
| `--fail-fast` | Stop at the first volume that fails. |
| `--max-failures N` | Stop after N failed volumes. |
| `-v` / `-vv` / `-q` · `--log-level` · `--log-file` / `--no-log-file` | Console detail and the run log. `-q` drops the per-document chatter and the progress bars; the warnings a phase prints about what it lost, the failures and the summary stay. `-qq` additionally raises the log threshold to ERROR, which silences logger-emitted warnings — not the ones a phase prints itself, which are never hidden. |

**Phases resolve left to right.** `--phases` replaces the set, then each
`--X` / `--no-X` applies as a delta — so `--fast --enrich` means *no service
work except enrichment*. Naming a phase in `--phases` and removing it with
`--no-X` is refused: there is no reading of that which says what you meant.

A document that fails never stops the others: it is logged, reported
`FAILED`, and the run moves on. `--fail-fast` and `--max-failures` are the
only ways to change that, and both are off by default. When either stops a
run, the summary says how many volumes were never attempted.

A document that fails does not kill the run: the error is logged with its
traceback, the document is reported as `FAILED`, and processing continues. The
run ends with a summary and exits **non-zero** if anything failed — so it can be
trusted in a script. Entity CSVs written before a failure are cleaned up, so
nothing references a TEI that was never produced.

**Exit codes.** `0` everything asked for succeeded · `1` some volumes
failed · `2` usage error · `3` misconfiguration and nothing ran · `4`
everything that ran failed · `5` the quality gate was not met. The table
under [Troubleshooting](#exit-codes) says what each one asks you to do.

**What a run leaves behind.** Beside the TEI, each run keeps its own
record:

```
tei_output/.teille-douce/runs/20260903-180824-0031415/
    run.json         what was asked (argv, every setting and where it came
                     from) and what happened to each volume
    incidents.jsonl  one incident per line, appended as it happens
    pipeline.log     this run's log, moved in at the end
```

The directory is named for when the run started and the process that ran
it: two volumes launched in parallel start inside the same second, and one
would otherwise overwrite the other's record. The log is the transcript and
`incidents.jsonl` is its index; no fact is
stored twice in two forms that could diverge. The JSONL is written line by
line so that a Ctrl-C in the fourth hour keeps everything before it. The
last ten runs are kept, and a directory is pruned whole — an index must not
outlive its transcript.

`--retry-failed` reads the last `run.json` and converts only what that run
reported `FAILED`.

**Logs.** Each run writes its own file at DEBUG level, named
`pipeline_YYYYmmdd_HHMMSS_PID.log` while it runs and moved into the run
directory as `pipeline.log` at the end. The process id is in the working
name so that volumes launched in parallel, which start inside the same
second, do not truncate each other's log. A `--log-file` you name is an
instruction: it stays where you put it. The console shows warnings and errors only, unless you set
`-v` (`-vv` for debug). Move or disable the run log with `--log-file` / `--no-log-file`.

**Cost.** With every annotation phase enabled, a full volume takes tens of
minutes — most of it waiting on the two HTTP services. Page parsing itself is
parallel, capped at `min(cpu_count(), 8)` workers.

### A fast run

To convert without the three annotation phases — no services, no models:

```bash
teille-douce run --fast
```

You get a complete base TEI: header, `sourceDoc`, text structure, notes,
figures, language detection.

It used to be the way to find out whether the input was shaped correctly, at
the cost of converting the corpus to learn it.
[`teille-douce check`](#before-you-run-teille-douce-check) answers that
without writing anything.

## Configuration

Two mechanisms, with different purposes.

**`teille_douce/config.py`** holds settings that describe *your project*: paths, catalogue
column expectations, software versions written into `<appInfo>`, the
responsibility statement, the languages to detect, the confidence thresholds. It
is meant to be edited.

**Command-line flags** win over everything, per setting: a flag you did not
pass does not shadow what the environment supplied, so `-o /tmp/out` leaves
`TDOUCE_OCR_DIR` in charge of the input. The chain is

```
flag  >  environment (TDOUCE_*)  >  config file  >  config.py default
```

The config file is a `teille-douce.toml` found by walking up from the
working directory, or the one `--config PATH` names; `--no-config` skips
discovery. It may set only the runtime settings — a key it does not know is
an error naming the nearest valid one, because silently ignoring it is how
you spend an afternoon.

```toml
[paths]
input = "OCR"
output = "tei_output"

[phases]
ner = false            # no GPU on this machine

[services]
pyhellen = "http://pyhellen.labo:9000"

[limits]
jobs = 4
```

The keys are the ones in the table below, second column: each setting has
one, and it is not derivable from the variable name — `TDOUCE_OCR_DIR` is
`paths.input`.

**A relative path in the config file is read next to that file**, not next
to the working directory: the file is found by walking up, so one line has
to mean the same thing from every subdirectory it was written to serve.
`output = "tei"` in `/corpus/teille-douce.toml` is `/corpus/tei` wherever
you launch the run from. An empty value is refused like any other, and
`~` means your home directory — in the config file, in a `TDOUCE_*`
variable and in a quoted flag alike, since only a shell expands it for you.

**Environment variables** override what moves between machines and between runs
— paths, service URLs, timeouts, and the phase switches. The prefix is
`TDOUCE_`, from the pipeline's original name; it is kept so that existing
wrapper scripts and CI configurations keep working:

| Variable | Config key | Default | Meaning |
|---|---|---|---|
| `TDOUCE_OCR_DIR` | `paths.input` | `OCR` | Input directory |
| `TDOUCE_OUTPUT_DIR` | `paths.output` | `tei_output` | Output directory |
| `TDOUCE_ENRICHMENT` | `phases.enrich` | `1` | Linguistic enrichment on/off |
| `TDOUCE_MODERNIZE` | `phases.modernize` | `1` | Modernization on/off |
| `TDOUCE_NER` | `phases.ner` | `1` | Named-entity recognition on/off |
| `TDOUCE_PYHELLEN_URL` | `services.pyhellen` | `http://localhost:8000` | PyHellen base URL |
| `TDOUCE_PYHELLEN_TIMEOUT` | `services.pyhellen_timeout` | `120` | Seconds per PyHellen request |
| `TDOUCE_MODERNIZE_URL` | `services.modernize` | `http://localhost:8011` | VieuxParler base URL, for every language with no specific override |
| `TDOUCE_MODERNIZE_URL_<IDENT>` | — | — | Same, for one language only (`TDOUCE_MODERNIZE_URL_FRA`) |
| `TDOUCE_MODERNIZE_TIMEOUT` | `services.modernize_timeout` | `300` | Seconds per modernization batch |
| `TDOUCE_MODERNIZE_SIMILARITY_MIN` | `limits.similarity_min` | `0.8` | Below this similarity, a modernized line is rejected as a hallucination |
| `TDOUCE_HEALTH_TIMEOUT` | `services.health_timeout` | `30` | Seconds for the reachability probe both services answer before a run |
| `TDOUCE_TEI_RNG` | — | — | Path to a `tei_all.rng`, for the schema-validation tests |
| `TDOUCE_ENTITIES_DIR` | `paths.entities` | `entities` | Where the NER entity CSVs go |
| `TDOUCE_METADATA_CSV` | `paths.metadata` | `metadata_livre.csv` | Volume catalogue |
| `TDOUCE_PERSONS_CSV` | `paths.persons` | `metadata_personne.csv` | Person catalogue |
| `TDOUCE_SKIP_EXISTING` | `output.skip_existing` | `0` | Resume: skip volumes already converted |
| `TDOUCE_JOBS` | `limits.jobs` | `8` | Page-parsing workers |
| `TDOUCE_MODERNIZE_BATCH_SIZE` | `limits.batch_size` | `64` | Lines per modernization request |
| `TDOUCE_MODERNIZE_CONCURRENCY` | `limits.concurrency` | `8` | In-flight requests to VieuxParler |
| `TDOUCE_PYHELLEN_CONCURRENCY` | `limits.concurrency` | `8` | In-flight requests to PyHellen |
| `TDOUCE_PYHELLEN_MAX_CONSECUTIVE_FAILURES` | `limits.max_consecutive_failures` | `10` | Circuit breaker: stop calling after this many failures in a row |
| `TDOUCE_NER_CONFIDENCE` | `limits.ner_confidence` | `0.6` | Below this, an entity prediction is dropped |
| `TDOUCE_NER_DEVICE` | `models.device` | `auto` | Where the NER models run: `auto`, `cpu`, `cuda`, `cuda:1`, `mps` |
| `TDOUCE_FAIL_ON` | `quality.fail_on` | `never` | What counts as a failure: `never`, `incident`, `loss` |
| `TDOUCE_UI` | `output.ui` | `auto` | Which reporter to use: `auto`, `plain`, `dashboard` |
| `TDOUCE_MAX_PAGE_LOSS` | `quality.max_page_loss` | `100` | A volume losing more than this share of its pages is a failure |
| `TDOUCE_DEBUG` | `output.debug` | `0` | Verbose diagnostics, in the console and in the run log |
| `TDOUCE_LOG_LEVEL` | `output.log_level` | `WARNING` | Console level |
| `TDOUCE_LOG_FILE` | `output.log_file` | `pipeline.log` | Run log; each run writes its own timestamped file |

`--concurrency` and `limits.concurrency` set both services at once;
`TDOUCE_PYHELLEN_CONCURRENCY` and `TDOUCE_MODERNIZE_CONCURRENCY` set one
each. The table above is checked against the code by a test, so it cannot
drift.

Booleans accept `1`/`true`/`yes`/`on` and `0`/`false`/`no`/`off`,
case-insensitive.

**Every setting rejects a value it cannot use** — unreadable, empty, non-finite,
out of range — and keeps its default while saying so on stderr. A variable set
but empty counts as unusable: a wrapper doing
`export TDOUCE_MODERNIZE_URL="${MODERNIZE_URL}"` with the outer variable unset
would otherwise silently disable the service. If a value of yours was ignored,
there is a `RuntimeWarning` naming it and giving the reason.

### Where a value came from: `teille-douce info`

Four layers per setting is a trap rather than a feature without a way to ask
the chain where a value came from — and the config file is the sharpest edge
of it, because it is found by walking *up* from the working directory, so one
you have forgotten is the least debuggable thing in the design.

```bash
teille-douce info                    # every setting, and which layer set it
teille-douce info pyhellen_url       # one setting, layer by layer
teille-douce info TDOUCE_JOBS        # named by its variable, or by its TOML key
teille-douce info --json             # the same, for a wrapper
```

```
  pyhellen_url = http://pyhellen.labo:9000            (env TDOUCE_PYHELLEN_URL)

  flag     --pyhellen           (not given)
  env      TDOUCE_PYHELLEN_URL  http://pyhellen.labo:9000              <- used
  config   services.pyhellen    ./teille-douce.toml: http://localhost:8000
  default  config.py            http://localhost:8000
```

A layer that offered a value the converters refused says so on its own line —
which is the answer to "why is my variable ignored", printed where you are
looking rather than in a `RuntimeWarning` scrolled past four hours ago. With
no argument, every setting is listed, the ones nobody set marked apart from
the ones somebody did, the config file named whether or not anything in it
won, and the versions that land in `<appInfo>` at the end.

## The annotation services

Three phases are optional and enabled by default. Each is probed once before the
run and disabled for the whole run — with one warning line explaining the
missing elements — if it does not answer.

### PyHellen — linguistic enrichment

An HTTP service returning tokens, parts of speech and lemmas. Models are chosen
per detected language: `freem` for French, `lasla` for Latin, `grc` for Ancient
Greek. Produces `<s>`, `<w>` and `<pc>` inside `<ab>`, `<note>`, `<head>` and
`<titlePart>`.

Configure with `TDOUCE_PYHELLEN_URL`. Requests are capped at 8 concurrent, and
a circuit breaker stops calling after 10 consecutive failures — a frozen server
must not turn into hours of sequential timeouts.

### VieuxParler — modernization

An HTTP service rewriting early modern French into modern French, line by line
in batches of 64. Produces `<choice><orig>…</orig><reg>…</reg></choice>`.

Two guards make the result readable as evidence rather than as a claim. A line
whose modernized form falls below `TDOUCE_MODERNIZE_SIMILARITY_MIN`
(0.8 character-level similarity, after normalizing long-s, accents and case) is
**rejected** and left unmodified — over Greek OCR artifacts the service answers
with invented French. What survives carries a `@cert` grading how much it
changed: `high` above 0.95 (a spelling normalization), `medium` from 0.90, `low`
below (a heavy rewrite worth a reader's attention). Those boundaries were
measured on real output for this corpus, where modernized similarity runs
0.81–1.00 with a median of 0.96.

### NER — named entities

Runs locally, no service, but needs `pip install -e '.[ner]'`. Both models
load on a CUDA GPU when there is one and on the CPU otherwise; `--device`
(or `TDOUCE_NER_DEVICE`, or `models.device`) overrides that, and the run
prints the device it chose before downloading several gigabytes of model.
Apple silicon is not picked automatically — `--device mps` asks for it.
Left on `auto`, the Flair model keeps whatever device Flair itself
selected, so an existing `FLAIR_DEVICE` still applies.

Two models:
**CamemBERT** (`pjox/camembert-classical-fr-ner`) for persons, places and
organizations in French, reading the `<orig>` text; **GLiNER**
(`urchade/gliner_multi-v2.1`) for artworks, literary works, materials,
techniques, events and dates in any language, reading the `<reg>` text where one
exists. Predictions below 0.6 confidence are dropped, and three further
filtering layers remove the noise a model produces on OCR of this quality.

Entities are written twice: inline as `<persName>`/`<placeName>`/`<orgName>`/
`<rs>` carrying `@resp="#ner-auto"`, `@cert` and `@ref`, and as grouped entries
in `<standOff>`. They are also exported per document to
`entities/<document>/entities_*.csv`.

## Reading the output

A single file per volume, in four layers.

### `<teiHeader>` — what is claimed about the document

```xml
<extent>
  <measure unit="images" quantity="8">8 images</measure>
  <measure unit="words" quantity="183">183 words</measure>
</extent>
…
<langUsage>
  <language ident="fra" usage="88" n="161 words">French</language>
  <language ident="lat" usage="12" n="22 words">Latin</language>
</langUsage>
```

`@usage` is a percentage, `@n` the absolute count with its unit — both computed
from the text actually assembled, not from the catalogue.

`<encodingDesc>` carries three things worth knowing about:

- `<editorialDecl>` — prose stating how language detection, modernization and
  entity recognition were performed, including the numeric thresholds actually
  used. It is generated from `teille_douce/config.py`, so it cannot drift from the code.
- `<appInfo>` — the OCR/HTR software and versions from `APP_VERSIONS`.
- `<classDecl>` — the full SegmOnto taxonomy as `<catDesc>` entries with links
  to the SegmOnto guidelines, which is what `zone/@corresp` points at.

`<revisionDesc>` lists the phases that actually ran, so a file tells you whether
it was enriched, modernized and NER-annotated.

`<particDesc><listPerson>` holds the **curated** persons, from your CSV. Model
inferences never go here — they live in `<standOff>` — so that what an editor
asserts stays distinguishable from what a model guessed.

### `<sourceDoc>` — the layout, as segmented

One `<surface>` per page, its `<graphic>` pointing at the full IIIF image:

```xml
<surface xml:id="f1" n="1" ulx="0" uly="0" lrx="1749" lry="2481">
  <graphic url="https://gallica.bnf.fr/iiif/…/f1/full/full/0/native.jpg"/>
  <zone xml:id="zone_c038da…" type="RunningTitleZone" corresp="#RunningTitleZone"
        ulx="243" uly="84" lrx="1178" lry="175" points="243,84 1178,84 …"
        source="https://gallica.bnf.fr/iiif/…/f1/243,84,935,91/full/0/native.jpg">
    <zone xml:id="zoneLine_d36dde…" type="DefaultLine" …>
      <path xml:id="path_8c14ea…" points="257,150 616,147 1158,157"/>
      <line xml:id="line_1e47a7…" n="1">DE LAMOVR, LIVRE VII.</line>
    </zone>
  </zone>
</surface>
```

Three nested levels: region zone, line zone, and the `<line>` itself with its
baseline `<path>`. Bounding boxes as `@ulx`/`@uly`/`@lrx`/`@lry`, the full
polygon as `@points`, and `@source` giving the IIIF crop URL of that zone alone —
paste it in a browser and you see the region. `@corresp` on a region zone points
into the SegmOnto taxonomy in the header.

Identifiers are **deterministic**: built with `uuid5` from the document and the
element, so two runs over the same input produce byte-identical files. They are
prefixed by type — `zone_`, `zoneLine_`, `line_`, `path_` — so an id tells you
what it identifies.

### `<text>` — the reading text

```xml
<text>
  <front>
    <pb corresp="#f3" facs="#f3" n="3"/>
    <titlePage facs="#f3">
      <titlePart corresp="#zone_e3644c…" type="TitlePageZone" xml:lang="fra">…</titlePart>
    </titlePage>
  </front>
  <body>
    <div>
      <pb corresp="#f1" facs="#f1" n="1"/>
      <fw corresp="#zone_c038da…" type="RunningTitleZone" xml:lang="fra">…</fw>
      <ab corresp="#zone_46be43…" type="MainZone" xml:lang="fra">
        <lb corresp="#zoneLine_2b4f6d…" facs="#zoneLine_2b4f6d…"/>CHAPITRE PREMIER.
        <lb corresp="#zoneLine_b31701…" facs="#zoneLine_b31701…"/>Des raisons qui nous obligent…
      </ab>
      <note corresp="#zone_b9348f…" type="MarginTextZone" place="margin"
            xml:lang="lat" target="#zoneLine_69a574…">…</note>
    </div>
  </body>
</text>
```

How SegmOnto labels become TEI:

| Zone label | Becomes |
|---|---|
| `MainZone` (and any `Main…`) | `<ab>`, one per source zone — two columns of a page stay two `<ab>` |
| `MainZone` + a `HeadingLine` | `<head>` opening a new `<div>` |
| `MarginTextZone` | `<note place="margin">`, with `@target` on the line it faces |
| `NumberingZone`, `QuireMarksZone`, `RunningTitleZone` | `<fw>` |
| `GraphicZone` | `<figure>` with a `<graphic>` of the IIIF crop; text inside it stays inside the figure |
| `TitlePageZone` | `<front><titlePage><titlePart>` |
| anything else | `<ab type="…">` — no structure claimed, no text lost |

Every element carries `@corresp` back to its `<sourceDoc>` zone, and every
`<lb/>` back to its line — so any word in the reading text can be traced to a
rectangle on a page image. `@facs` is there too, because that is the attribute
TEI/IIIF viewers read.

Line-break hyphens (`¬` and `-`) are kept as the source prints them. Rejoining
words split across lines happens inside the annotation phases, on a copy, and
never edits the transcription.

`xml:lang` is set per container by [Lingua](https://github.com/pemistahl/lingua-py),
restricted to the languages in `SUPPORTED_LANGUAGES`. A container mixing
languages gets `<foreign xml:lang="…">` around the foreign stretches — validated
by rule-based heuristics, because a statistical detector on a short OCR-damaged
line is not trustworthy alone.

### The annotation layer

Present only if the corresponding phase ran.

```xml
<s xml:id="s_7cc0c50dd561_0" part="I" next="#s_7cc0c50dd561_1">
  <w lemma="raison" pos="NOMcom" msd="NOMB.=p|GENRE=f">raisons</w>
  <pc>,</pc>
</s>
```

Sentences crossing a page or zone boundary are split with `@part="I"`/`"F"` and
linked by `@next`/`@prev` rather than being silently merged. `@pos` and `@msd`
belong to the tagset declared in the header for that language.

```xml
<choice>
  <orig>nostre</orig>
  <reg type="modernized" resp="#modernize-auto" cert="high">notre</reg>
</choice>
```

The original reading is always preserved. `@resp` says a machine produced the
`<reg>`, `@cert` how far it moved.

```xml
<rs resp="#ner-auto" cert="high" type="event" ref="#event-06ef4779-…">…</rs>
```

Nine entity types, each with its own TEI treatment:

| Type | Inline element | Listed in `<standOff>` as | CSV |
|---|---|---|---|
| person | `<persName>` | `<listPerson><person>` | `entities_persons.csv` |
| place | `<placeName>` | `<listPlace><place>` | `entities_places.csv` |
| organization | `<orgName>` | `<listOrg><org>` | `entities_orgs.csv` |
| artwork | `<objectName>` | `<listObject><object><objectIdentifier>` | `entities_artworks.csv` |
| literary work | `<title>` | `<listBibl><bibl>` | `entities_works.csv` |
| event | `<rs type="event">` | `<listEvent><event>` | `entities_events.csv` |
| material | `<material>` | `<list type="materials"><item>` | `entities_materials.csv` |
| technique | `<rs type="technique">` | `<list type="techniques"><item>` | `entities_techniques.csv` |
| date | `<date when="…">` | — | — |

Dates get a machine-readable `@when` when the text states one clearly (`1659`,
`M.DC.LIX`); without it a `<date>` cannot be sorted, filtered or placed on a
timeline.

Materials and techniques are open vocabularies — the terms a model found in the
text, not a controlled nomenclature — and the header says so, so that
`<list type="materials">` is not mistaken for an authority file.

Every inline annotation carries `@ref` resolving to an `xml:id` inside the same
document; nothing dangles. Where a mention sits inside a `<choice>`, it is
anchored on both the `<orig>` and the `<reg>`.

## Validating the output

Two different questions, two validations.

```bash
# Is it a conformant output of THIS pipeline? (RELAX NG + Schematron + Python)
teille-douce validate                 # no argument: the output directory

# One file, or a directory somewhere else
teille-douce validate tei_test/LIV0044_reconciled.tei.xml

# Is it conformant TEI at all? (needs a tei_all.rng, ~1 MB, not versioned)
teille-douce validate --schema tei_all.rng tei_output

# One JSON object instead of a report, for a wrapper to read
teille-douce validate --json tei_output
```

| Option | Effect |
|---|---|
| `FILE\|DIR ...` | What to check. A directory becomes the `.xml` directly inside it; nothing at all means the configured output directory, which is what the end-of-run report offers as the next thing to type. |
| `--no-odd` | Do not validate against the project schema. Five invariants live only there and will go unchecked, which the command says rather than letting a partial check look like a complete one. |
| `--schema RNG` | A `tei_all.rng`, adding full TEI RELAX NG validation. |
| `-j, --jobs auto\|N` | Files are independent; `auto` is as many as there are, capped by the machine. |
| `--max-errors N` | How many errors to print per file, `0` for all. A display decision: nothing is dropped at collection. |
| `--json` | The same verdicts as one JSON object, under `files`, with `applied` naming the schemas this invocation actually used — the notes below are prose and `--json` suppresses them, so without it a wrapper could not tell a complete check from a narrowed one. |

`tei_all` establishes TEI conformance but detects neither a non-existent zone
type, nor a `<figure>` without an anchor, nor an automatic entity with no
certainty indication. `--odd` answers that complementary question, using the
project's own customization: a closed element inventory, closed value lists for
`zone/@type` and `rs/@type`, and eleven Schematron rules.

The command exits **1** when a file fails, so it drops straight into CI. Rules
the TEI marks `role="nonfatal"` are reported as warnings, not errors. Under
`--no-odd` five local invariants go unchecked, and it says so rather than
letting you believe the check was complete.

A refusal is not a failed file: **2** for a `--schema` or a `-j` this program
cannot use, **3** for nothing to check, a demanded `--odd` with no schema
installed, and a schema that parses and will not compile. One broken argument
must not be reported as a corpus that does not conform.

The project schema is applied without being asked for. It is versioned, so
applying it needs only `lxml` and `saxonche` — not the toolchain that
*compiles* it. An installation that does not carry it at all (a wheel
installed outside a checkout) narrows the check and says so; a `--odd` you
typed yourself is a demand, and fails.

Get `tei_all.rng` with:

```bash
curl -sSL -o tei_all.rng https://tei-c.org/release/xml/tei/custom/schema/relaxng/tei_all.rng
```

Validation costs roughly 0.7 s per MB; see [schema.md](schema.md) for the
breakdown and for what the eleven rules check.

## Going back to a run: `teille-douce report`

Every run leaves a directory under `tei_output/.teille-douce/runs/`: the
manifest, the incident index, and its own log. `report` reads it. It touches
no corpus, so it is instant and cannot damage anything.

```bash
teille-douce report                          # the last run
teille-douce report --runs                   # the runs kept here, newest first
teille-douce report --run 20260903-180824    # a precise one; the pid is optional
teille-douce report LIV0044_reconciled       # one volume
teille-douce report --code phase             # by loss code; a prefix is enough
teille-douce report --why I2 --context       # one incident, and the log around it
teille-douce report --limits                 # what could not be measured, and why
teille-douce report --json                   # the same, for a wrapper
```

The selectors compose: `report LIV0044_reconciled --code container` means what
it looks like it means.

```
  run 20260903-180824-0031415   2026-09-03 18:08      1 of 2 converted · exit 1

  2 incidents

  I1  LIV0044_reconciled     phase_lost                            148 of 148
  I2  LIV0326_v1_reconciled  document_failed                             1 of 1

  teille-douce report --why I1 for one of them, --limits for what is not here
```

Exit codes: **0** the third block is empty, **1** it is not — whatever the
selectors left on screen, because the question a wrapper asks this command is
"did that run need a human" — **2** for a value typed on the command line that
names nothing (`--why I9`, or `--why` under a `--block` that is not indexed),
and **3** when there is no record here at all.

**`--limits` is the one that matters in the long run.** It separates what this
pipeline *cannot* measure from what it *does not measure yet*, and gives the
second kind its price so that someone can decide to pay it. Entities dropped
below the NER threshold are the first kind: the threshold is applied inside
GLiNER's own inference, so what it cost has no denominator to be a fraction
of. Blocks 1 and 2 over a past run are the second: `incidents.jsonl` indexes
block 3 alone — an index of everything is an index of nothing — so the defects
of the source and what the guards withheld are in the end-of-run summary and
in the log, and `report --block source` says so rather than answering
"nothing", which would be read as "the run lost nothing that way".

## Shell completion

```bash
teille-douce completion bash > ~/.local/share/bash-completion/completions/teille-douce
teille-douce completion zsh  > ~/.zfunc/_teille-douce
teille-douce completion fish > ~/.config/fish/completions/teille-douce.fish
```

`teille-douce run --fail-on <TAB>` then offers `never incident loss`, because
the parser declares those three — not because a list of them was copied into a
shell script. Regenerate the file after an upgrade; a completion offering a
flag the program no longer has is worse than none.

## Troubleshooting

**`-i: OCR is not there.`** — the input directory does not exist. Create it,
or point `-i` / `TDOUCE_OCR_DIR` / `paths.input` elsewhere. The prefix names
the layer the value actually came from, so `TDOUCE_OCR_DIR: /srv/ocr is not
there.` means the variable, not the flag; `teille-douce info ocr_dir` prints
all four layers.

Every path this run will read is checked before anything is opened — the two
catalogues included, since a mistyped `--metadata` used to convert
twenty-seven volumes with placeholder headers and exit 0. A catalogue that is
simply absent at its default location is not an error: the guide says both are
optional, and the header says so with explicit placeholders.

### Exit codes

| | |
|---|---|
| **0** | everything asked for succeeded — losses do not change this, unless `--fail-on` says otherwise |
| **1** | partial failure: some volumes failed, others did not |
| **2** | usage error, on the command line: unknown flag, a value a flag will not take, two flags that contradict each other (`--force --skip-existing`, `--plain --dashboard`, `--no-probe --require-services`) |
| **3** | misconfiguration, nothing ran: input missing, empty corpus, a selector matching nothing, `--require-services` with a service down, a config file that is unreadable or names a key this pipeline does not have |
| **4** | total failure: everything that ran failed, including the case where nothing could even be opened. Distinct from 1 because the remedy differs — 1 is worth retrying volume by volume, 4 usually means the input or the setup is wrong. A run stopped early by `--fail-fast` is never 4: it did not prove the corpus unconvertible |
| **5** | the quality gate was not met: everything converted, and `--fail-on` or `--max-page-loss` is still not satisfied. Only when nothing else failed — a failed volume is the more concrete fact and takes the code |

"Everything converted" and "exit 5" only contradict each other if a written
file and a publishable file are the same thing, which is the distinction
that flag exists to make.

**`No ALTO documents found in OCR/.`** — `OCR/` has no subdirectory containing
`.xml` files. Loose XML files at the top level of `OCR/` are not picked up:
every volume needs its own directory.

**`N directories with no ALTO file, nothing to convert`** — those volumes
exist as directories but hold no `.xml` anywhere under them, usually an
archive packed without its ALTO subfolder. They are named in the warning, in
the log and in the summary line (`… (N more held no ALTO)`), and the run
continues on the rest: nothing was converted from them, and nothing failed
either, so they do not change the exit status.

The exception is a corpus where they are *all* there is, with nothing
already converted to resume from: that exits 3, because the exit codes
describe the run and not the volumes, and a run handed nothing it could
convert did not run. It is also, far more often than a corpus of empty
volumes, an `-i` pointing one directory too high — which is the reading
exit 3 is there to suggest.

**`N directories could not be read`** — a different diagnosis, and a
failure: the volume may well hold ALTO, this process is simply not allowed
to list the directory. It is named like a corrupt archive, counted in the
denominator, and the run exits 1. Check the mode of the directory rather
than repacking the volume. Under `--skip-existing` a volume whose TEI is
already there is skipped without being opened, mode and all, so a nightly
wrapper does not go red over one there is nothing left to convert.

**The header is full of `Information not available.`** — no catalogue row
matched. `teille-douce check` names every volume this applies to before the
run. Check that the `BDD` column of `metadata_livre.csv` contains exactly
the internal id parsed from the directory name (`LIV0002a_reconciled` →
`LIV0002`), and that the file really is semicolon-delimited.

**Persons appear as placeholders** — `metadata_personne.csv` was not loaded, or
the `PERSxxxx` keys in the book CSV have no matching row. The run prints how
many persons it loaded, and `teille-douce check` says whether the table loads
at all.

**No `<w>` or no `<choice>` in the output** — the corresponding service did not
answer its probe; `teille-douce check` asks them the same question in two
seconds. There is one warning line at the start of the run saying which
one. Check the URL, and raise `TDOUCE_HEALTH_TIMEOUT` if the server is remote
and still loading its model.

**No entities** — the NER extra is not installed, or the phase was off (`--no-ner`, `--fast`, or `TDOUCE_NER=0`).
The first NER run also downloads several GB of models.

**A setting seems ignored** — look for a `RuntimeWarning` naming the variable.
Empty, unreadable, non-finite and out-of-range values are refused on purpose,
and the default is kept.

**Pages come out in the wrong order** — ordering uses the first number in each
filename. `f10.xml` and `page_10.xml` are fine; `vol2_f10.xml` sorts by `2` —
and so does every other page of that volume, so they all share one number: all
are converted, in discovery order, and one warning names them, but their IIIF
`@source` and their `surface/@n` are then meaningless.

**`FAILED <document>: N page id(s) claimed by several ALTO files`** — two files
would produce the same `<surface>` `xml:id` (`1.xml` and `f1.xml` both give
`f1`, and so does the same filename in two subdirectories of one volume). The
TEI would carry a duplicate `xml:id` that no XML parser reads back, so nothing
is written. Rename one of the files the message names.

**`FAILED <document>: …`** — that volume raised; the others were still
converted. The full traceback is in the run's `pipeline_*.log`. Fix and relaunch
with `--skip-existing` to convert only what is missing.

**No images in a TEI viewer** — the volume has neither a `manifest_iiif` value
nor a mapping CSV, or the mapping matched under 30 % of the filenames and was
rejected. For a non-Gallica IIIF server, set `IIIF_URI["image_base"]` in
`teille_douce/config.py` — it describes the project, so it has no flag.
