# Schema guide

The pipeline ships its own TEI customization. This page explains what it
guarantees, how to validate against it, and how to change it.
[`schema/README.md`](../schema/README.md) is the reference for the compilation
chain itself, including two traps that are not deducible from the TEI
documentation.

- [Two different questions](#two-different-questions)
- [The eleven Schematron rules](#the-eleven-schematron-rules)
- [Validating](#validating)
- [Changing the schema](#changing-the-schema)
- [What lives where](#what-lives-where)

---

## Two different questions

**"Is this valid TEI?"** is answered by `tei_all` — the whole Guidelines, about
six hundred elements, of which this pipeline emits 118. It is a real check, but
it detects neither a zone type that does not exist, nor a `<figure>` with no
anchor, nor a machine-generated entity carrying no indication of certainty.

**"Is this a conformant output of *this* pipeline?"** is answered by
`schema/alto2tei.odd`. It constrains on three levels:

1. **A closed element inventory.** `<moduleRef include="…">` imports only the
   elements the pipeline actually emits. Anything else is an error — either the
   pipeline gained an element and the ODD has not caught up, or it is a stray.
2. **Closed value lists.** `zone/@type` mirrors the SegmOnto taxonomy in
   `src/constants.py`; `rs/@type` mirrors the entity table in
   `src/enrichment/entity_schema.py`.
3. **Eleven Schematron rules** for what a content model cannot express.

Both validations remain available, and they answer different questions. In
practice: run `--odd` always, and `--schema tei_all.rng` when you want TEI
conformance stated independently.

## The eleven Schematron rules

| Rule | Context | Catches |
|---|---|---|
| `inventaire-ferme` | any element | An element outside the pipeline's declared inventory |
| `coordonnees-bien-formees` | `@points` | Coordinates that are not whitespace-separated `x,y` pairs |
| `langUsage-langues-seules` | `<langUsage>` | Prose mixed in with `<language>` elements — it belongs in `<encodingDesc>` |
| `language-quantite` | `<language usage="…">` | An empty `@ident`, or an `@n` that is not a count plus its unit (`1716 words`) |
| `choice-orig-reg` | `<choice>` | A modernization `<choice>` that does not hold exactly one `<orig>` and one `<reg>` |
| `pas-de-cesure-residuelle` | `<reg>` | A line-break hyphen (`¬`) surviving into a modernized form |
| `entite-automatique-datee` | `@resp="#ner-auto"` | An automatically detected entity with no `@cert` |
| `graphiczone-identifiee` | `zone[@type='GraphicZone']` | A graphic zone with no `xml:id` — its `<figure>` would have nothing to point at |
| `figure-ancree` | `<figure>` | A figure with no `@corresp` naming the zone it comes from |
| `idno-iiif-unique` | `idno[@type='iiif']` | Several manifests crammed into one element instead of being split |
| `pas-de-gabarit-publie` | `<ptr>`, `<idno>` | A placeholder ORCID (`0000-0000-0000-0000`) reaching the output |

Every one of these is **local**: it evaluates on an element and its immediate
neighbourhood. Invariants needing a full document traversal — resolving every
`@corresp`, checking that each `GraphicZone` has a `<figure>` — stay implemented
in Python in `scripts/validate_tei.py`, which does them in a single pass. In
Schematron they would be quadratic over documents of a hundred thousand
elements.

Five of the local rules used to be stated *twice*, once in the ODD and once in
Python, and the two versions had drifted: different severities, different
scopes, and the Python hyphenation check missed enriched `<reg>` elements whose
text lives inside `<w>`. They are now stated once, in the ODD. A run without
`--odd` therefore does not check them — and the script says so rather than
letting you believe the check was complete.

## Validating

```bash
# The project schema: RELAX NG, then Schematron, then the Python invariants
venv/bin/python scripts/validate_tei.py --odd tei_output/*.xml

# TEI conformance as well
venv/bin/python scripts/validate_tei.py --odd --schema tei_all.rng tei_output/*.xml

# A corpus, over 8 cores — documents are independent
venv/bin/python scripts/validate_tei.py --odd -j 8 tei_output/*.xml
```

Exit code 1 on any error. Rules the TEI itself marks `role="nonfatal"` are
reported as warnings.

The Schematron half needs `saxonche` (the TEI produces it in
`queryBinding="xslt2"`, which lxml cannot execute); RELAX NG validation against
`schema/alto2tei.rng` does not. When `saxonche` is missing, the Schematron step
skips with an explicit reason.

The end-to-end tests validate against `schema/alto2tei.rng` on every run. The
schema is versioned, so that check cannot be skipped.

**Cost:** roughly 0.7 s per MB — about thirty seconds for a 39 MB document of
135,000 elements. Measured breakdown: XML parsing 0.2 s, RELAX NG 16 s,
Schematron 8 s, Python checks 1 s. `-j N` spreads files over N cores; four
documents totalling 111 MB go from 72 s to 30 s on four cores, the floor being
the largest single file.

That it is not far worse is down to one decision. The TEI types `@points` as a
**list** of points, each validated against a pattern, which had libxml2 checking
539,000 coordinates per document and pushed RELAX NG alone to 56 s. The type is
narrowed to a string and the list shape is stated in Schematron instead
(`coordonnees-bien-formees`), where Saxon checks it in a second — the same
requirement, forty times cheaper.

## Changing the schema

`alto2tei.odd` is the source. `alto2tei.rng`, `alto2tei.sch` and
`alto2tei.svrl.xsl` are generated from it and versioned. **Never edit the
derivatives by hand.**

```bash
venv/bin/python scripts/build_odd.py            # after any change to the ODD
venv/bin/python scripts/build_odd.py --check    # do the derivatives match the source?
venv/bin/python scripts/build_odd.py --refresh  # re-download the toolchain
```

The procedure:

1. Edit `schema/alto2tei.odd`.
2. Recompile: `venv/bin/python scripts/build_odd.py`.
3. Verify: `venv/bin/python -m pytest tests/test_odd.py tests/test_e2e_pipeline.py`.
4. Commit the source **and the three derivatives in the same commit**.

`--check` recompiles into a temporary directory and compares against the
versioned files, ignoring only the generation date the TEI Stylesheets stamp
into their output. It is the fast way to find out whether someone edited a
derivative directly.

**Adding an element to the pipeline's output means adding it to the ODD.** The
inventory is closed, so `tests/test_odd.py` fails until you do — which is the
point: the schema cannot silently fall behind the code.

`build_odd.py` materializes TEI P5, the TEI Stylesheets and SchXslt into
`.odd-toolchain/` (gitignored) at versions pinned at the top of the script, and
drives them with SaxonC-HE. Raising those versions is a deliberate change:
recompile and read the diff of the generated schemas.

## What lives where

| File | Role | Applied by |
|---|---|---|
| `schema/alto2tei.odd` | Source. A TEI document describing the customization. | — |
| `schema/alto2tei.rng` | Content model (RELAX NG). | `lxml` |
| `schema/alto2tei.sch` | Schematron constraints, readable form. | — |
| `schema/alto2tei.svrl.xsl` | Schematron constraints, executable form. | `saxonche` |
| `scripts/build_odd.py` | Compiles the ODD into the three derivatives. | — |
| `scripts/rng_simplify.py` | RELAX NG §4.19/§4.20 reductions, needed before lxml reads the schema. | — |
| `scripts/validate_tei.py` | Runs everything, plus the document-wide Python invariants. | — |

Two implementation constraints shape all of this and are documented at length in
[`schema/README.md`](../schema/README.md), because each cost a full debugging
session:

- **libxml2 does not reduce impossible patterns.** A partial TEI import leaves
  empty model classes, which `odd2relax` renders as `<notAllowed/>`; the RELAX
  NG spec requires reducing those inside sequences, and libxml2 does not. The
  observed effects were a schema that would not compile after ten minutes, and —
  on a variant that did compile — a `<zone>` that no longer accepted a nested
  `<zone>`, rejecting conformant documents. `rng_simplify.py` performs the
  reductions first: 279 patterns eliminated, compilation down to 0.1 s. The
  `core` module remains incompilable after simplification and is imported whole;
  `inventaire-ferme` restores for it the inventory the content model no longer
  carries.
- **`extract-isosch` silently drops constraints by language.** It resolves a
  constraint's language from its nearest ancestor declaring `@xml:lang`,
  defaulting to `en`, and discards the rest. An `xml:lang="fr"` on the ODD root
  would therefore drop *every* project constraint from an `en` extraction — with
  no error: compilation succeeds, the `.sch` file is produced, and validation
  passes because the rules that could fail are absent. Two measures prevent it:
  French prose is marked on the `<div>` carrying it, never above `<schemaSpec>`;
  and `tests/test_odd.py` fails if a constraint declared in the ODD is missing
  from the generated Schematron.
