# Architecture

How a directory of ALTO files becomes one TEI document, and which module is
responsible for each part of it.

- [The shape of a run](#the-shape-of-a-run)
- [Per-document sequence](#per-document-sequence)
- [Module map](#module-map)
- [The four construction phases](#the-four-construction-phases)
- [The three annotation phases](#the-three-annotation-phases)
- [Cross-cutting concerns](#cross-cutting-concerns)
- [Using modules independently](#using-modules-independently)

---

## The shape of a run

```
config.py ──────────────► main.py
                             │
                    OCR/ ────┤ expand_archives()      ZIPs → directories
                             │ load_metadata()        the two CSVs, once
                             │ probe services         PyHellen, VieuxParler
                             │
                             ▼
                      for each document ──────────────► tei_output/<doc>.tei.xml
                                                        entities/<doc>/*.csv
```

Three things are done once per run, not per document: archives are expanded, the
metadata CSVs are loaded into memory, and each optional service is probed. A
service that does not answer is disabled **for the whole run**, with one warning
line — not retried per document, which would turn a dead server into hours of
timeouts.

Documents are then processed one at a time and **in isolation**: a document that
raises is logged with its traceback, reported as `FAILED`, and the run continues
with the next one. The exit code is non-zero if anything failed. Parallelism
happens *inside* a document, at page level.

## Per-document sequence

`main._process_document()`, in order:

| # | Call | Produces |
|---|---|---|
| 1 | `TEI(...)`, `build_tree()` | `<TEI xml:id="…">` root |
| 2 | `find_metadata_row()`, `build_metadata_dict()` | the document's catalogue row, resolved |
| 3 | `select_manifest()` | this document's IIIF image base |
| 4 | `build_header()` | complete `<teiHeader>` + the SegmOnto taxonomy tables |
| 5 | `build_sourcedoc()` | `<sourceDoc>`, pages processed in parallel |
| 6 | `build_body(detect_lang=True)` | `<text>` with `<front>`/`<body>`, `xml:lang`, `<foreign>` |
| 7 | `link_notes_to_lines()` | `@target` on margin notes |
| 8 | `extract_line_data()` | line inventory captured **before** enrichment reshapes the DOM |
| 9 | `enrich_body()` | `<s>`, `<w>`, `<pc>` — PyHellen |
| 10 | `modernize_body()` | `<choice><orig>/<reg></choice>` — VieuxParler |
| 11 | `run_ner()` | inline entities, `<standOff>` lists, `@ref`, entity CSVs |
| 12 | `override_teiheader_from_csv()` | local metadata wins over anything inferred |
| 13 | `finalize_langusage()` | `<langUsage>` from the statistics collected in step 6 |
| 14 | `finalize_extent()` | `<extent>` word and image counts |
| 15 | `write_xml()` | the file |

Steps 9, 10 and 11 are skipped when their `ALTO2TEI_*` flag is off or their
service is unreachable. Everything else always runs.

**The order is not arbitrary.** Four constraints fix it:

- **Step 8 before step 9.** Modernization works from the line inventory. Once
  enrichment has replaced line text with `<w>` elements, that inventory can no
  longer be derived — so it is captured first.
- **Step 9 before step 10.** Modernization splices `<choice>` around text that
  may already be tokenized; doing it the other way round would ask PyHellen to
  tag a document containing two competing readings of every line.
- **Step 11 last of the three.** NER reads `<orig>` for the French model and
  `<reg>` for the multilingual one, so both must already exist.
- **Step 12 after everything.** The CSV is the editor's assertion and overrides
  whatever the phases inferred; `finalize_langusage()` then runs *after* the
  override so a catalogue language list cannot silently erase measured counts.

## Module map

```
config.py                    Settings only: paths, versions, services, thresholds
main.py                      CLI, run orchestration, per-document isolation
scripts/
  validate_tei.py            Output validation (known failure modes + ODD)
  build_odd.py               Compiles schema/alto2tei.odd → rng, sch, svrl.xsl
  rng_simplify.py            RELAX NG §4.19/§4.20 reductions (see schema/README.md)
  build_test_fixture.py      Regenerates tests/fixtures/ from the private corpus
src/
  tei.py                     The TEI class — facade carrying document state
  constants.py               Namespaces, SegmOnto taxonomies, POS tagsets
  dates.py                   Reading the dates the corpus writes (CSV cells, text)
  volumetry.py               Words per language and per text, for <extent>/<langUsage>
  modernize.py               VieuxParler client
  teiheader/
    builder.py               Orchestration → build_header()
    default.py               DefaultTree: the skeleton, with placeholders
    prose.py                 Editorial declarations, generated from config
    full.py                  FullTree: metadata population + SegmOnto taxonomy
  sourcedoc/
    builder.py               Parallel page processing (multiprocessing)
    elements.py              SurfaceTree: surface / zone / line / path
    attributes.py            ALTO coords + TAGREFS → TEI attributes
  body/
    builder.py               Zone dispatch, <pb>, <hi>, modernization splicing
    text.py                  Text: sourceDoc → Line and Graphic namedtuples
    note_links.py            Links a margin <note> to the line it faces
  enrichment/
    pipeline.py              enrich_body(): orchestrates phases 1–6
    extractor.py             1. containers → text spans with offsets
    dehyphenation.py         2. rejoins words split across lines
    client.py                3. PyHellen HTTP client (tokens, POS, lemmas)
    aligner.py               4. NLP tokens → XML nodes
    segmenter.py             5. sentence segmentation
    reconstructor.py         6. rebuilds <s>/<w>/<pc>/<lb>/<hi>/<foreign>
    ner_pipeline.py          run_ner(): orchestrates phases 7–9
    entity_schema.py         Entity types → TEI elements, lists, vocabularies
    ner_models.py            7a. lazy loading of CamemBERT / GLiNER
    ner_detect.py            7b. entity inference over TEI blocks
    ner_filter.py            7c. three noise-filtering layers
    ner_align.py             8.  spans → XML nodes, dual <orig>/<reg> anchoring
    ner_resolve.py           9.  grouping, local linking, header injection, @ref
  lang/
    detector.py              LinguaDetector + historical-text cleanup
    heuristics.py            Keyword and character rules for 7 languages
    header.py                <langUsage> construction
  metadata/
    csv_book.py              metadata_livre.csv → header injection
    csv_person.py            metadata_personne.csv → PersonDatabase
    iiif.py                  ALTO file → IIIF URL mapping
  utils/
    files.py                 File ordering, document id parsing
    hyphen.py                What a line-break hyphen is — one definition, three users
    xml.py                   XML writing, content_root(), xml_id_safe()
```

Around 14,800 lines under `src/`, `scripts/`, `main.py` and `config.py`; 12,400
lines of tests.

Each module is self-contained enough to be imported on its own, which is what
the test suite does throughout.

## The four construction phases

### `teiheader/` — the header

Built in two passes, on purpose. `DefaultTree` lays down the **complete
skeleton** with explicit placeholders; `FullTree` then fills in what the
metadata actually provides. A field with no data keeps its placeholder rather
than vanishing, so the shape of the header does not depend on how complete a
catalogue row happens to be — and a consumer can rely on the element being
there.

`prose.py` generates the `<editorialDecl>` prose from `config.py`. The
modernization rejection threshold, for instance, is stated in the header by
reading the constant, not by restating the number — the two used to be separate
and were free to diverge.

`full.py` also emits the SegmOnto taxonomy into `<classDecl>` and returns the
zone and line tables, which `sourcedoc/` then uses to build `@corresp`.

### `sourcedoc/` — the layout

`builder.py` distributes pages over a `multiprocessing` pool capped at
`min(cpu_count(), 8)`. Results come back through `imap_unordered` and are
**re-sorted by page number**, so speed does not reorder the document. A page
whose ALTO is malformed is recorded in `skipped_pages` and the document
continues; a document where *every* page fails is a failure.

`elements.py` builds `<surface>` → `<zone>` (region) → `<zone>` (line) →
`<path>` + `<line>`. `attributes.py` converts ALTO geometry into TEI:
`<Polygon POINTS>` into `@points` and a derived bounding box, `<Baseline>` into
the `<path>`, and `TAGREFS` into `@type` plus `@subtype`/`@n` by parsing the
SegmOnto form `MainZone:column#1`.

Identifiers are `uuid5(namespace, document + element key)` — deterministic, so
two runs over identical input produce byte-identical output, which is what makes
the golden-file tests possible.

### `body/` — the reading text

`text.py` walks the finished `<sourceDoc>` and yields flat `Line` and `Graphic`
namedtuples: page id, zone id, zone type, line id, line type, text. The body
builder therefore never re-reads ALTO — `<sourceDoc>` is the single source of
truth for what is on the page.

`builder.py` dispatches each line on its zone type (the table is in the
[user guide](user-guide.md#text--the-reading-text)). Three rules run through the
dispatch:

- **One TEI element per source zone, not per line.** Consecutive lines of the
  same zone extend the same `<ab>`, `<note>`, `<fw>` or `<head>`. A running
  title set on two lines is one running title; two columns on a page are two
  `<ab>`.
- **Nothing is silently dropped.** A zone type with no dedicated branch, or a
  line label the pipeline does not know, still lands in an `<ab type="…">`.
- **Structure is only claimed where the segmentation supports it.** A
  `HeadingLine` in a `MainZone` opens a `<div>` with a `<head>`, because
  SegmOnto asserted it is a heading. Nothing infers a `<p>`, a title from a
  byline, or a chapter from a blank line.

`note_links.py` runs afterwards and gives each margin `<note>` a `@target`
listing the `MainZone` line zones its own zone vertically overlaps, on the same
surface. `target[0]` is the anchor: by default the highest overlapping line, but
if the note opens with a call mark (`*`, dagger) that also appears in one of the
candidate lines, that line is promoted — the page itself made the link, and the
pipeline only follows it. A promoted anchor is marked `subtype="gloss"
cert="medium"`; when the starred line was the default choice anyway, nothing is
asserted, since the asterisk may be a speck on the scan. Call marks are read
from the raw `<line>` text in `<sourceDoc>`, not from the body, because
tokenization does not preserve them.

Notes sharing an anchor are numbered `@n` in the vertical order of their margin
zones; a note with a unique anchor gets no `@n`.

### `lang/` — language detection

Lingua, restricted to `SUPPORTED_LANGUAGES`, at container level. Under the
confidence threshold, or on a text shorter than 10 characters, rule-based
heuristics take over — a statistical detector on a short OCR-damaged line is not
trustworthy alone.

Mixed containers go through Lingua's `detect_multiple_languages_of` and each
foreign stretch is then **validated by heuristics** before becoming a
`<foreign>`: rejected if the primary-language score reaches 2, or if the
target-language score is 0. Greek is reliable through Unicode ranges; Latin
relies on vocabulary it does not share with French.

Detection also accumulates per-language word counts, which `header.py` turns
into `<langUsage>` at the end of the run.

## The three annotation phases

All three start from `utils.xml.content_root()` — the `<text>` element, not
`<body>` — so front matter is annotated like the rest of the document.

### Enrichment (steps 1–6)

```
containers ─► extract_spans ─► dehyphenate ─► PyHellen ─► align_tokens
                                                              │
              rebuild_container ◄── segment_sentences ◄────────┘
```

The interesting part is that annotation runs on a **transformed copy** of the
text while the offsets remain traceable to the original. `extract_spans` records
character offsets; `dehyphenate` rejoins words split across lines and keeps an
offset map; `align_tokens` walks that map backwards to place each token on real
XML nodes. The transcription itself is never edited.

Three guards:

- A container where more than 20 % of tokens could not be anchored is
  **rejected** rather than annotated — past that ratio, annotations would attach
  to the wrong characters.
- Requests are capped at 8 concurrent, and a circuit breaker stops calling after
  10 consecutive failures.
- A sentence crossing a container or page boundary is split with
  `@part="I"`/`"F"` and chained by `@next`/`@prev`, rather than merged or
  truncated.

`reconstructor.py` rebuilds the container from tokens while preserving `<lb/>`,
`<hi>` and `<foreign>` — the elements the base build placed there.

### Modernization

`modernize.py` sends lines to VieuxParler in batches of 64, then
`body/builder.py` splices the result into the tree as `<choice>`. The grade is
computed **between what was sent and what came back**, at the client, not
re-derived later from the tree — recomputing it downstream scored dehyphenation
as if it were an editorial rewrite.

A line below the similarity floor is dropped as a hallucination and left
unmodified. Lost lines are counted and reported, so a document that came back
untouched does not look like a success.

### NER (steps 7–9)

```
blocks ─► CamemBERT (orig, fra)  ┐
      └─► GLiNER    (reg, all)   ├─► filter ─► align ─► resolve ─► @ref + standOff + CSV
                                 ┘
```

Two models with different reading strategies: CamemBERT on the `<orig>` text for
French persons, places and organizations; GLiNER on the `<reg>` text where one
exists, for artworks, works, materials, techniques, events and dates. Long
blocks are sliced into overlapping word windows, because GLiNER truncates inputs
beyond roughly 384 subword tokens.

`ner_filter.py` applies three layers at three different stages, and the staging
is the point:

1. **Span-level**, right after inference: minimum length, digits, punctuation,
   match against the volume's own title, and an all-caps heuristic against OCR
   garbage.
2. **POS-based**, after alignment: it reads the `@pos` attributes the enrichment
   phase already put on `<w>`. A person or place whose every word is a verb,
   determiner or pronoun is rejected. Language-agnostic — no stopword list.
3. **Entity-level**, after grouping: repairs duplicated canonical names, merges
   spelling variants by fuzzy matching, and prunes single-mention low-confidence
   entities.

`ner_align.py` anchors a mention on **both** `<orig>` and `<reg>` when it sits
inside a `<choice>` — a reader following either reading finds the annotation.

`ner_resolve.py` groups mentions into entities, assigns typed local identifiers,
writes the lists into `<standOff>`, and sets `@ref` on every mention. Two
decisions are structural:

- **Inferred lists live in `<standOff>`, never in `<profileDesc>`.**
  `<particDesc>` and `<settingDesc>` describe what the editor asserts; mixing a
  model's guesses in made the two indistinguishable — the header used to carry
  two `<listPerson>`, one curated and one guessed.
- **Entity types with no target list** (dates) get no `@ref`. A pointer to
  nothing is worse than no pointer.

Heavy imports (torch, transformers, gliner) sit inside `run_ner`, so a run with
NER disabled never pays for them.

## Cross-cutting concerns

**`src/tei.py`** is the facade. It carries the document state — root, filepaths,
metadata, `segmonto_zones`, `lang_stats`, `skipped_pages` — and exposes one
method per phase. `main.py` never reaches into a submodule.

**Identifiers.** `uuid5`, seeded from the document and the element, prefixed by
type (`zone_`, `zoneLine_`, `line_`, `path_`, `s_`, `pers-`, `place-`). Two
consequences: output is reproducible, and an id says what it identifies.

**Hyphenation** has exactly one definition, in `utils/hyphen.py`, used by the
body builder, the dehyphenator and the validator. It used to be restated in each
and they disagreed.

**Bare and namespaced tags coexist in the same tree**: elements built by
different phases do not all carry the TEI namespace prefix. Any code inspecting
tags must go through `local_tag()` / `content_root()` rather than comparing
`el.tag` to a string — including in tests, which use a local `qlocal()` helper.

**Missing metadata** uses `defaultdict(lambda: None)`, so an absent column is
`None` rather than a `KeyError`.

**Every phase reports what it lost.** Skipped pages, failed containers,
rejected modernizations, filtered entities — all counted and printed. A silent
zero is treated as a bug: a document whose enrichment server died mid-run must
not be indistinguishable from a document that simply had nothing to enrich.

## Using modules independently

```python
from src import TEI
from src.teiheader import build_header
from src.sourcedoc import build_sourcedoc
from src.body import build_body, Text, link_notes_to_lines
from src.metadata import load_metadata, load_person_database, IIIFMapping
from src.enrichment import enrich_body
from src.enrichment.ner_pipeline import run_ner
from src.lang import get_detector, build_langusage
from src.utils import Files, write_xml
```

`config.py` is imported by the modules that need settings, so overriding a
setting means setting the environment variable before the import — which is what
`tests/test_config_env.py` does.
