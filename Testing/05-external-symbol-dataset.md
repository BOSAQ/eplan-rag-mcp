# Cross-Referencing an External Symbol Dataset

**Reusable tool:** `Testing/tools/lookup_symbol_dataset.py` — downloads the
dataset once (cached, gitignored, never committed) and searches it by
`--short-name`, `--number` or `--contains` a description substring. See its
`--help` for the numeric-id caveat below (`number` is not globally unique).

## The dataset

`covaga/electrical-symbols-dataset` on Hugging Face — 33,502 rows, one
Parquet file (`data/train-00000-of-00001.parquet`, ~120MB), columns
`file_name` (PNG image), `short_name`, `number`, `description`,
`variant_id` (4 classes, A–D), `visual_description` (AI-generated text
description of the pictogram). Covers EPLAN IEC, NFPA and GB symbol families.

## Where it helped

It supplied the semantics that `live_symbol_catalog` cannot: EPLAN's live API
returns a symbol's *name*, pin count and geometry, never what the symbol
*means*. Cross-referencing `short_name` against this project's live
`IEC_symbol` catalog confirmed, independently, exactly what had already been
inferred from the German Schließer/Öffner naming convention:

| `short_name` | dataset `description` | live in this project? |
|---|---|---|
| `SL` | Power NO contact of a contactor | yes |
| `OL` | Power NC contact of a contactor | yes |
| `K` | Electromechanical operating device, general / relay coil, general | yes |
| `SSA` | Pushbutton, NO contact, general | yes |
| `SOA` | Pushbutton, NC contact, general | yes |
| `FT1` | Electromechanical device of a thermal relay, single-pole | yes |
| `SONOT1` | Emergency stop switch/pushbutton, NC contact | yes |
| `TST_2` | Starter, direct line, without reverse motion | not verified — possibly a pre-built macro |

`number` (a small integer per symbol, e.g. `SL: 0`, `K: 20`, `SSA: 159`)
turned out to be the exact numeric `SymbolId` that
`XEGActionInsertSymRef`/`eplan_insert_symbol_reference` requires (see
`04-interactive-vs-scripted-placement.md`) — a genuinely load-bearing find,
not just descriptive metadata.

## Where it did not, and should not, substitute for a live check

- **Pin geometry and variant count** are not in this dataset at all (it has
  `visual_description`, a *textual* description of the pictogram, not
  coordinates) — `live_symbol_catalog(library=..., symbol=...)` remains the
  only source for where a symbol's connection points actually are.
- **Existence in a specific project's libraries is not guaranteed.** The
  dataset spans IEC/NFPA/GB families generically; a name it lists is not
  proof that name resolves in *this* project's `.edb` master data. Every
  candidate name pulled from the dataset in this session (`K`, `SSA`, `SOA`,
  `FT1`, `SONOT1`) was still individually confirmed against the live project
  with `live_symbol_catalog` before being placed — treat the dataset as a
  strong prior for *which* names to try, never as a substitute for that
  confirmation call.
