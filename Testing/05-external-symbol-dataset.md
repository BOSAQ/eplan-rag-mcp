# Cross-Referencing an External Symbol Dataset

**Reusable tool:** `Testing/tools/lookup_symbol_dataset.py` — downloads the
dataset once (cached, gitignored, never committed) and searches it by
`--short-name`, `--number` or `--contains` a description substring. See its
`--help` for the numeric-id caveat below (`number` is not globally unique).

## The dataset

<https://huggingface.co/datasets/covaga/electrical-symbols-dataset> —
33,502 rows, one Parquet file (`data/train-00000-of-00001.parquet`, ~120MB),
columns `file_name` (PNG image), `short_name`, `number`, `description`,
`variant_id` (4 classes, A–D), `visual_description` (AI-generated text
description of the pictogram). Covers EPLAN IEC, NFPA and GB symbol
families. `lookup_symbol_dataset.py` (see above) downloads it from that
same URL automatically on first run — no separate manual download step.

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

## Correction: it covers connection/routing symbols too, not just devices

An earlier version of this note implied the dataset's value stopped at
device symbols. That was wrong, checked directly: every routing symbol
recorded in `02-routing-and-connections.md` (originally sourced from the
user's own EPLAN symbol-browser screenshot) is also in the dataset, with
matching numeric IDs:

| `short_name` | `number` | dataset `description` |
|---|---|---|
| `TLRU` | 64 | T-node left, right, down (LRD) |
| `TOUR` | 66 | T-node up, down, right (UDR) |
| `TOUL` | 67 | T-node up, down, left (UDL) |
| `BR` | 68 | Jumper |
| `CR` | 69 | Double junction |
| `co` | 70 | Angle |
| `CRL` | 71 | Diagonal connection |
| `TLRO` | 72 | T-node left, right, up (LRU) |
| `BP` | 8 | Interruption point |

`BP` was not in the user's screenshot but matches an `InterruptionPoint`
placement (tag `-0V60`) seen live on the project — independent confirmation
from a third direction.

It goes deeper than the eight/nine symbols above: a `short_name` starting
`CDP` (`ConnectionDefinitionPoint`, the type seen live next to `-K1`/`-Q2`
on the page) has **19 distinct variants** in the dataset — `CDP` (308),
`CDPU` (338), `CDPNG`/`CDPNG2` (no-graphic forms), `CDPCPLWL` (optical
fibre), a `CDPCP2F1`–`F5` family for piping (heated, insulated, cased), and
more — each with its own real numeric id.

**Casing caveat, found live:** the dataset stores this one as `"co"`
(lowercase), not `"CO"`. A case-sensitive exact-match lookup misses it
silently. `lookup_symbol_dataset.py`'s `--short-name` already compares
lowercased on both sides, so it is not affected — a hand-rolled
`df[df.short_name == "CO"]` one-liner would be.

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
