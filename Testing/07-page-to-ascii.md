# Reading a Page as a Map, Not as a List

**Tool:** `Testing/tools/page_to_ascii.py` — renders an `eplan_live_read_page`
result as a fixed-pitch glyph map with a key block. Standard library only.
**Fixture:** `Testing/tools/fixtures/mando-1.json` — a verbatim capture of
`+MANDO/1` from the scratch clone, kept because it contains one of every
placement family the renderer has to handle.

## The problem it solves

`live_read_page` returns the truth, but as a flat list of placements with
millimetre coordinates. Deciding *layout* questions from that list — is this
device on the same axis as that one, does the corner sit where the branch
needs it, did anything drift off-axis — means holding a dozen coordinate pairs
in your head and comparing them pairwise.

That is not hypothetical. `04-interactive-vs-scripted-placement.md` records a
thermal relay that silently moved from `(100, 252)` to `(114, 254)`; it was
caught only by diffing two JSON dumps by hand, and the reason it mattered is
that `generate_connections` had quietly re-wired *around* the misaligned device
without a single error message. On a map, a device sitting one column off the
run it is supposed to be in is visible immediately.

The four reference terminals placed at the page corners in
`03-coordinate-system-and-designations.md` make the same point from the other
direction: rendered, `-TOPL` is top-left and `-BOTL` is bottom-left, which is
what "+Y is up" *looks like* rather than something to be re-derived from a
table of Y values every time.

## Format

Modelled on Dungeon Crawl Stone Soup's level files
(<https://github.com/crawl/crawl>, `crawl-ref/source/dat/des/*.des`): a grid of
single-character glyphs plus a `KEY:` block giving each glyph's meaning. The
format was worth borrowing for the same reasons crawl uses it — a glyph grid is
diffable, greppable, and readable with no renderer, and pushing the names into
a key block keeps the grid itself dense. `--format des` emits an actual
`.des`-shaped block (`NAME:` / `MAP` … `ENDMAP` / `KEY:`); the default
`--format text` adds millimetre rulers on both axes instead.

```
      0        50        100       150       200       250       300       350       400
      |         |         |         |         |         |         |         |         |
300 | a                                                                                   d
290 | ····················································································
260 | ·                                      ┬ =                  ┐                      ·
240 | ·                                                           e                      ·
230 | ·                                                           ┌ ┘                    ·
170 | ·                                                           =                      ·
110 | ·                                                           f                      ·
 80 | ·                                                    » =    ┘                      ·
 20 | b···················································································c
```

Devices and terminals get a letter, assigned in read order and explained in the
key. Everything structural gets a glyph that says what it *is*:
`┘ └ ┌ ┐` corners, `┬ ┴ ├ ┤` T-nodes, `┼` cross, `»` interruption point,
`=` connection definition point, `·` the outline of a `MacroBox` /
`LocationBox`. `--charset ascii` collapses those to `+ > = .` for terminals
that cannot render box-drawing characters.

## The one design decision worth knowing

**Routing glyphs are derived from each symbol's own pin directions, never from
its name.** A corner and a T-node are both `clrType: "SymbolReference"`, and
this project's names for them (`CO`, `TLRU`, `TOUR`, …) are install-specific —
see `02-routing-and-connections.md`. But "which way do the legs point" is in
the data for every project, so `{Left, Up} → ┘` and `{Down, Left, Right} → ┬`
hold everywhere. A direction set the renderer does not recognise draws as `?`
*and* is listed under `UNRECOGNISED ROUTING SHAPES`, so an unhandled case shows
up rather than being quietly drawn as something plausible.

This is self-checking in a useful way: on the fixture, `CO` variant 2 (pins
Left+Up) rendered `┘` and variant 0 (Right+Down) rendered `┌` — matching the
variant→quadrant mapping that had been measured separately from
`live_place_corner`'s own behaviour.

## What it refuses to guess

The projection is lossy, and each place where it loses something says so:

- **Overdrawn cells.** A cell covers several millimetres, so two placements can
  land in one. The map shows one glyph and prints `OVERDRAWN:` underneath,
  naming what is hidden and where. Measured on the fixture at a coarse zoom:
  `cell near (305, 230): showing e =+++MANDO-K1.001 | hidden: + CO v0`.
- **Connections that are not straight.** `--connections` takes an
  `eplan_live_read_connections` result and overlays it, but only draws a run
  when the two endpoints share an x or a y. Anything routed through corners is
  listed as *not drawn* instead. A missing wire is recoverable; an invented one
  is not — and on the fixture that rule correctly drew `Q2 → K1` (both at
  x=302) while refusing to draw `-0V60 (266, 80) → Q2 (302, 106)`, which really
  does turn a corner at `(302, 80)`.
- **Endpoint precision.** `live_read_connections` reports each end's *device*
  location, not the pin's: `Q2`'s connection end reads `(302, 106)` while its
  pins sit at `(302, 110)` and `(302, 102)`. Under one cell of error at any
  sane zoom, but the header says so rather than implying millimetre accuracy.
- **Truncated reads.** If the underlying `live_read_page` was truncated, the
  header leads with `!! this read was TRUNCATED` — an incomplete map must not
  read as an empty region.

None of this proves electrical connectivity. Geometry is still not electricity
(`02-routing-and-connections.md`); the map shows what is *drawn*, and only
`--connections` fed from `live_read_connections` shows what is *wired*.

## Usage

```bash
python page_to_ascii.py fixtures/mando-1.json              # whole page
python page_to_ascii.py page.json --connections conns.json # overlay wiring
python page_to_ascii.py page.json --region 255,60,325,275 --cols 60
python page_to_ascii.py page.json --fit --charset ascii --format des
some-command | python page_to_ascii.py -
```

`--cols` is a *target* width; the real cell size is snapped to a round
millimetre value and the region expanded to whole cells, so every axis label is
a round number rather than `288.6`. The header always states the actual mm per
column and per row. `--graphics` also plots texts and polylines — off by
default because a production page is mostly graphics and they bury the devices
(`live_read_page`'s own docstring records a Circuit page whose first 40
placements were all `PolyLine`).

Any JSON containing a `live_read_page` payload works, nested or not — a write
tool's `page_after` block is found automatically, so a placement's own return
value can be rendered without extracting anything first.
