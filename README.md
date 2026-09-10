# TEIlle-douce

[![CI](https://github.com/rayondemiel/TEIlle-douce/actions/workflows/ci.yml/badge.svg)](https://github.com/rayondemiel/TEIlle-douce/actions/workflows/ci.yml)
[![Python 3.12+](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/downloads/)
[![Code: AGPL-3.0](https://img.shields.io/badge/code-AGPL--3.0-orange.svg)](LICENSE)
[![Encoding: CC BY 4.0](https://img.shields.io/badge/TEI%20output-CC%20BY%204.0-green.svg)](https://creativecommons.org/licenses/by/4.0/)

*Taille-douce* is the copperplate engraving of the seventeenth century — the
technique Abraham Bosse set out in his *Traicté des manières de graver en taille
douce* (1645), and the one that illustrated most of the books this pipeline
reads.

TEIlle-douce turns the ALTO XML produced by OCR/HTR engines into TEI P5 editions
of early modern printed books — page images kept addressable through IIIF,
layout kept in `<sourceDoc>` under the
[SegmOnto](https://segmonto.github.io/) taxonomy, reading text assembled in
`<text>`, and — optionally — annotated with tokens, lemmas, modernized spellings
and named entities.

It exists for a specific corpus: 27 volumes — 19 distinct works — of French
art theory and art literature printed and manuscript from
the seventeenth century, digitized by Gallica and others providers, and
segmented with Kraken and YALTAi. Nothing in the pipeline is specific to those
books, but every design decision was made against them.

Part of the **[Projet Grand Siècle](https://github.com/Grand-Siecle)**
(Université de Lausanne — UNIL, Université de Genève — UNIGE).

> **Note** — this repository is moving to the `Grand-Siecle` organization.
> Until the transfer it lives at `rayondemiel/TEIlle-douce`, which is the URL
> the commands below use; GitHub will redirect the old one afterwards.

---

## What it produces

One TEI file per volume, with four layers a reader can use independently:

| Layer | Where | What is in it |
|---|---|---|
| **Metadata** | `<teiHeader>` | Catalogue record, persons, languages with word counts, SegmOnto taxonomy, editorial declarations, software versions |
| **Layout** | `<sourceDoc>` | Every `<surface>`, `<zone>`, `<line>` and baseline `<path>` of the ALTO, with its coordinates and a IIIF crop URL |
| **Text** | `<text>` | `<front>`/`<body>`, `<div>`/`<head>`, `<ab>`, `<note place="margin">`, `<fw>`, `<figure>`, `<pb>`, `<lb/>` — each `@corresp` pointing back into `<sourceDoc>` |
| **Annotation** *(optional)* | in `<text>` and `<standOff>` | `<s>`/`<w>`/`<pc>` with POS and lemmas, `<choice><orig>/<reg></choice>` for modernized spellings, `<persName>`/`<placeName>`/`<rs>` for entities resolved against `<standOff>` lists |

Every document is also validated against the project's own TEI customization
([`schema/teille-douce.odd`](schema/)), which states what a conformant output of
*this* pipeline is — a stricter question than TEI conformance.

## Quickstart

```bash
git clone https://github.com/rayondemiel/TEIlle-douce.git
cd TEIlle-douce
python3.12 -m venv venv && source venv/bin/activate
pip install -e .

# Put ALTO XML files (or ZIP archives of them) in OCR/, one directory per volume
cp -r /path/to/my-volume OCR/

# Convert, without the phases that need external services
teille-douce run --fast

# Check the result
teille-douce validate tei_output
```

TEI files land in `tei_output/`. The three annotation phases are enabled by
default and need two HTTP services ([PyHellen](https://github.com/Grand-Siecle/PyHellen), [VieuxParler](https://github.com/Grand-Siecle/VieuxParler-API)) plus several GB of
NER models; without them the pipeline still produces a complete base TEI, and
says which phases it skipped. See the [user guide](docs/user-guide.md).

A run draws a live panel in a terminal and writes one line per event
everywhere else, and ends on a report that separates what the source got
wrong from what a guard withheld on purpose and from what needs a human —
every figure against a denominator, and every loss with an address you can
open. Beside the TEI it leaves `tei_output/.teille-douce/runs/<when>/`: the
manifest of what was asked, an index of the incidents, and its own log.
`--fail-on incident` turns "everything converted, three volumes carry no
annotation" from an exit 0 into an exit 5.

## Documentation

| | |
|---|---|
| **[User guide](docs/user-guide.md)** | Install, prepare input, configure, run, read the output, validate, troubleshoot |
| **[Architecture](docs/architecture.md)** | The per-document sequence, the module map, and where each TEI element comes from |
| **[Developer guide](docs/developer-guide.md)** | Test suite, fixtures, conventions, and how to add a feature end to end |
| **[Schema guide](docs/schema.md)** | What the ODD guarantees, how to validate against it, how to change it |
| **[`schema/README.md`](schema/README.md)** | The ODD compilation chain in depth, and two traps that cost a debugging session each |
| **[Contributing](CONTRIBUTING.md)** | Workflow, conventions, pre-PR checklist |

## Requirements

Python 3.12 or later — `lingua-language-detector` 2.2 requires it and `pandas`
3.0 wants 3.11; CI pins 3.12 because language detection shifts between lingua
minor releases and the end-to-end test compares against a byte-exact reference
file.

Three dependency tiers, installed as needed:

```bash
pip install -e .          # core: lxml, pandas, rich, lingua, httpx
pip install -e '.[dev]'   # + pytest, coverage, saxonche (what CI installs)
pip install -e '.[ner]'   # + torch, transformers, gliner, flair (several GB)
```

## Project status

Working and in production on the Grand Siècle corpus. 682 tests, 89.5 % line
coverage enforced as a ratchet in CI, and outputs validated against both
`tei_all` and the project ODD on every run.

Known limits, deliberate rather than pending:

- **Zone types without a dedicated body element** (`StampZone`, `TableZone`,
  `CustomZone`) fall back to a plain `<ab type="…">`. No text is ever dropped,
  but no structure is claimed either.
- **`<titlePart>` is one per title zone.** The pipeline has no signal telling a
  title from a byline or an imprint, and inventing that distinction would be an
  editorial claim the OCR does not support.
- **Entity reconciliation against external authorities** (Wikidata, IdRef) is
  out of scope here. This pipeline guarantees typed local identifiers and
  resolvable `@ref`; reconciliation happens downstream.
- **Modernization is a machine reading.** Lines whose modernized form drifts too
  far from the original are rejected as hallucinations and left unmodified; what
  survives carries a `@cert` grading how much it changed.

## Licence and credits

Copyright © 2025–2026 the TEIlle-douce authors.

The **code** is distributed under the [GNU AGPL-3.0](LICENSE). The **TEI
encoding it produces** is published under
[CC BY 4.0](https://creativecommons.org/licenses/by/4.0/), as declared in each
output header. The **digitized images** the `@facs` and IIIF URLs point to
remain subject to the terms of their holding institution.

The `sourceDoc` and header construction descend from work by **Kelly
Christensen**, credited in the headers of the files concerned.

Please cite this pipeline using [`CITATION.cff`](CITATION.cff). Responsibility
for the encoding is stated in `teille_douce/config.py` and written into every output header:
Maxime Humeau, Jan Blanc, Antoine Gallay, Gabriel Batalla-Lagleyre, Pauline
Randonneix, Léonie Marquaille.
