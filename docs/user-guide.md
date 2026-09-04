# User guide

Everything needed to run TEIlle-douce on your own documents and read what comes
out. For how the pipeline works internally, see
[architecture.md](architecture.md).

- [Installation](#installation)
- [Preparing your input](#preparing-your-input)
- [Running the pipeline](#running-the-pipeline)
- [Configuration](#configuration)
- [The annotation services](#the-annotation-services)
- [Reading the output](#reading-the-output)
- [Validating the output](#validating-the-output)
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
| `-x, --exclude PATTERN` | Drop volumes after selection. Repeatable. |
| `--limit N` | Convert at most N of those selected, in order. |
| `--skip-existing` / `--force` | Skip volumes already converted / convert them anyway. |
| `-i, --input` · `-o, --output` | Input and output directories. |
| `--entities` · `--metadata` · `--persons` | Entity CSVs, and the two catalogues. |
| `--fast` | No annotation phase at all: no service, no model. |
| `--phases LIST` | Set the phases outright: `enrich,modernize,ner`, `all` or `none`. |
| `--enrich`/`--no-enrich`, and likewise for `modernize` and `ner` | Add or remove one phase from the current set. |
| `--pyhellen URL` · `--vieuxparler URL` · `--health-timeout S` | The two services. |
| `-j, --jobs N` · `--batch-size N` · `--concurrency N` | Workers, lines per request, in-flight requests. |
| `-n, --dry-run` | Resolve everything, list the plan, write nothing. |
| `-v` / `-vv` / `-q` · `--log-level` · `--log-file` / `--no-log-file` | Console detail and the run log. `-q` leaves the failures and the summary; it does not hide what went wrong. |

**Phases resolve left to right.** `--phases` replaces the set, then each
`--X` / `--no-X` applies as a delta — so `--fast --enrich` means *no service
work except enrichment*. Naming a phase in `--phases` and removing it with
`--no-X` is refused: there is no reading of that which says what you meant.

**Exit codes.** `0` everything asked for succeeded · `1` some volumes failed
· `2` usage error · `3` misconfiguration, nothing ran (input directory
missing, no volumes found, a selector matched nothing).

A document that fails does not kill the run: the error is logged with its
traceback, the document is reported as `FAILED`, and processing continues. The
run ends with a summary and exits **non-zero** if anything failed — so it can be
trusted in a script. Entity CSVs written before a failure are cleaned up, so
nothing references a TEI that was never produced.

**Logs.** Each run writes its own timestamped file, `pipeline_YYYYmmdd_HHMMSS.log`,
at DEBUG level. The console shows warnings and errors only, unless you set
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
figures, language detection. This is also the mode to use when checking that
your input is shaped correctly, before committing to a long run.

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

**Environment variables** override what moves between machines and between runs
— paths, service URLs, timeouts, and the phase switches. The prefix is
`TDOUCE_`, from the pipeline's original name; it is kept so that existing
wrapper scripts and CI configurations keep working:

| Variable | Default | Meaning |
|---|---|---|
| `TDOUCE_OCR_DIR` | `OCR` | Input directory |
| `TDOUCE_OUTPUT_DIR` | `tei_output` | Output directory |
| `TDOUCE_ENRICHMENT` | `1` | Linguistic enrichment on/off |
| `TDOUCE_MODERNIZE` | `1` | Modernization on/off |
| `TDOUCE_NER` | `1` | Named-entity recognition on/off |
| `TDOUCE_PYHELLEN_URL` | `http://localhost:8000` | PyHellen base URL |
| `TDOUCE_PYHELLEN_TIMEOUT` | `120` | Seconds per PyHellen request |
| `TDOUCE_MODERNIZE_URL` | `http://localhost:8011` | VieuxParler base URL, for every language with no specific override |
| `TDOUCE_MODERNIZE_URL_<IDENT>` | — | Same, for one language only (`TDOUCE_MODERNIZE_URL_FRA`) |
| `TDOUCE_MODERNIZE_TIMEOUT` | `300` | Seconds per modernization batch |
| `TDOUCE_MODERNIZE_SIMILARITY_MIN` | `0.8` | Below this similarity, a modernized line is rejected as a hallucination |
| `TDOUCE_HEALTH_TIMEOUT` | `30` | Seconds for the reachability probe both services answer before a run |
| `TDOUCE_TEI_RNG` | — | Path to a `tei_all.rng`, for the schema-validation tests |
| `TDOUCE_ENTITIES_DIR` | `entities` | Where the NER entity CSVs go |
| `TDOUCE_METADATA_CSV` | `metadata_livre.csv` | Volume catalogue |
| `TDOUCE_PERSONS_CSV` | `metadata_personne.csv` | Person catalogue |
| `TDOUCE_SKIP_EXISTING` | `0` | Resume: skip volumes already converted |
| `TDOUCE_JOBS` | `8` | Page-parsing workers |
| `TDOUCE_MODERNIZE_BATCH_SIZE` | `64` | Lines per modernization request |
| `TDOUCE_MODERNIZE_CONCURRENCY` | `8` | In-flight requests to VieuxParler |
| `TDOUCE_PYHELLEN_CONCURRENCY` | `8` | In-flight requests to PyHellen |
| `TDOUCE_PYHELLEN_MAX_CONSECUTIVE_FAILURES` | `10` | Circuit breaker: stop calling after this many failures in a row |
| `TDOUCE_NER_CONFIDENCE` | `0.6` | Below this, an entity prediction is dropped |
| `TDOUCE_DEBUG` | `0` | Verbose diagnostics, in the console and in the run log |
| `TDOUCE_LOG_LEVEL` | `WARNING` | Console level |
| `TDOUCE_LOG_FILE` | `pipeline.log` | Run log; each run writes its own timestamped file |

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

Runs locally, no service, but needs `requirements-ner.txt`. Two models:
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
python3 scripts/validate_tei.py --odd tei_output/*.xml

# Is it conformant TEI at all? (needs a tei_all.rng, ~1 MB, not versioned)
python3 scripts/validate_tei.py --schema tei_all.rng tei_output/*.xml

# Both, on many files, spread over 8 cores
python3 scripts/validate_tei.py --odd --schema tei_all.rng -j 8 tei_output/*.xml
```

`tei_all` establishes TEI conformance but detects neither a non-existent zone
type, nor a `<figure>` without an anchor, nor an automatic entity with no
certainty indication. `--odd` answers that complementary question, using the
project's own customization: a closed element inventory, closed value lists for
`zone/@type` and `rs/@type`, and eleven Schematron rules.

The script exits **1** on any error, so it drops straight into CI. Rules the TEI
marks `role="nonfatal"` are reported as warnings, not errors. Without `--odd`,
five local invariants go unchecked and the script says so rather than letting
you believe the check was complete.

Get `tei_all.rng` with:

```bash
curl -sSL -o tei_all.rng https://tei-c.org/release/xml/tei/custom/schema/relaxng/tei_all.rng
```

Validation costs roughly 0.7 s per MB; see [schema.md](schema.md) for the
breakdown and for what the eleven rules check.

## Troubleshooting

**`Directory not found: OCR`** — `OCR/` does not exist. Create it, or point
`TDOUCE_OCR_DIR` elsewhere.

**`No ALTO documents found in OCR/.`** — `OCR/` has no subdirectory containing
`.xml` files. Loose XML files at the top level of `OCR/` are not picked up:
every volume needs its own directory.

**The header is full of `Information not available.`** — no catalogue row
matched. Check that the `BDD` column of `metadata_livre.csv` contains exactly
the internal id parsed from the directory name (`LIV0002a_reconciled` →
`LIV0002`), and that the file really is semicolon-delimited.

**Persons appear as placeholders** — `metadata_personne.csv` was not loaded, or
the `PERSxxxx` keys in the book CSV have no matching row. The run prints how
many persons it loaded.

**No `<w>` or no `<choice>` in the output** — the corresponding service did not
answer its probe. There is one warning line at the start of the run saying which
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
