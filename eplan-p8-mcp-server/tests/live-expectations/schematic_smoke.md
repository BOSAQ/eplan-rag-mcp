# Live expectations: schematic primitives

What the six schematic tools actually did on a real EPLAN, so a future change
that breaks one of them has something to be compared against. The offline suite
pins the generated C#; this pins the OBSERVED BEHAVIOUR, which no offline test
can reach.

**Measured:** 2026-09-03, EPLAN Electric P8 2027.0.1, remoting port 49152.
**Against:** a disposable clone in `%TEMP%\eplan_mcp_scratch`. No project or
master data outside that directory was written. Production master data on that
machine resolves to `P:\04\EPLAN\...`; the clone carries project-local copies of
all seven symbol libraries inside its own `.edb`, which is what makes the
scratch guard meaningful rather than decorative.

**Reproduce:** with a scratch project open in EPLAN, run the end-to-end script
described under "The sequence" below against the working tree (import
`api.actions.schematic` directly - a connected MCP server is running the
*installed* code, not your edit).

## The sequence, and what each step returned

| # | Call | Result |
|---|---|---|
| 1 | `live_symbol_catalog()` | 7 libraries: SPECIAL_en_US, Symbol Library - East River, Symbol Library - Special, GRAPHICS_en_US, Symbol Library - East River - Graphic, NFPA_symbol_en_US, Symbol Library - Graphics |
| 2 | `live_symbol_catalog(library="NFPA_symbol_en_US")` | 12 symbols; `SL`, `S`, `O`, `SSV`, `SWR`, `ONE` have 2 connection points, `Q1` has 6 |
| 3 | `live_symbol_catalog(library=..., symbol="SL")` | 9 variants, each with its pin list |
| 4 | `live_create_page(location="MCPTEST", counter=777)` | page **`+MCPTEST/777`**, `pageType=Circuit`, `gridSize=3.175` |
| 5 | `live_place_symbol(page, lib, "SL", 60.0, 200.0)` | handle `207/17/40965/0`, snapped to **(60.325, 200.025)** |
| 6 | `live_place_symbol(page, lib, "SL", 140.0, 200.0)` | handle `207/17/40966/0`, snapped to **(139.7, 200.025)** |
| 7 | `live_read_page(page)` | `placementCount=2`, both `Function` |
| 8 | `live_connect_pins(page, hA, 0, hB, 0)` | `lineDrawn=true`, from (60.325, 206.375) to (139.7, 206.375) |
| 9 | `live_read_page(page)` | `placementCount=3`: DynamicConnectionLine + 2 Function |
| 10 | `live_remove_placement(page, handle=hB, expect_type="Function")` | removed; count back to 2 |
| 11 | `live_remove_placement(page, handle=hA, expect_type="DynamicConnectionLine")` | **refused** - "handle resolves to a Function" |
| 12 | `live_remove_placement(page, remove_page=True)` | `pageRemoved=true`; a later read fails with the page list |

## Numbers a future change must not silently alter

- **Grid is 3.175 mm** (1/8"). `Page.GridSize` on this installation. Snapping
  60.0 -> 60.325 and 140.0 -> 139.7 follows from it; if those two numbers move,
  either the grid or the snap formula changed.
- **`SL` pin 0 reports raw `(0.0, 6.35)`** with the placement at
  `(60.325, 200.025)`, resolving to absolute `(60.325, 206.375)` with
  `frame="relative"`. This is the single most important observation in the file:
  the raw value is an OFFSET, and publishing it as a page coordinate would draw
  wires near the page origin that touch nothing while reporting success.
- **A newly placed Function's `Name` is `"+"`.** It has no device tag until one
  is assigned. Not a failure.
- **Page naming ignored the plant designation.** An earlier run set
  `DESIGNATION_PLANT` alongside location and counter and got `+SPIKE5/950` - the
  plant part did not appear. Hence `live_create_page` reads the name back rather
  than predicting it.

## The scratch guard, verified in both directions

With `SCRATCH_ROOT` pointed away from the open project (the same condition as
"a real project is focused"):

- `live_create_page(...)` with the default `allow_real_project=False` was
  **refused**: `REFUSING TO WRITE: the open project is '...SCHEM_SPIKE_01.edb',
  which is outside the scratch root '...'`.
- The same call with `allow_real_project=True` **succeeded** and created
  `+OVERRIDE/998`, which was then removed.

Both halves matter: a guard that never fires is not protecting anything, and one
that cannot be overridden makes the tool unusable for its real purpose.

## Failures found by running this, not by reading it

Recorded because each one cost real debugging time and none was visible from
Python:

1. `SetLiveProp` was called before it was defined. EPLAN reported
   `CS0103 (Row:712)`; the tool returned only "Timeout waiting for script
   results". **A compile error is indistinguishable from a timeout** unless you
   read `eplan_get_system_messages` - which is why every timeout from these
   tools now carries a hint saying so.
2. `Page.Properties` (a `PagePropertyList`) has **no page-description member**.
   The `description` argument was removed rather than shipped as a parameter
   that silently does nothing. Setting one needs the generic
   `Property[AnyPropertyId]` indexer and a decision about which property id
   represents "page description" - see the roadmap issue.

## Scope limit

Step 8 draws a graphical connection line between two pin coordinates. Whether
EPLAN has also created a LOGICAL `Connection` between the two functions is NOT
asserted here - that needs `generate_connections` + `export_connections` to
settle. The tool's own result says the same thing in its `scopeNote`, so a
caller cannot mistake "a line was drawn" for "the devices are wired".

---

# Connections: graphical vs logical

**Measured** 2026-09-03, EPLAN 2027.0.1, against a clone of a production project.

## `live_read_connections` reads real wiring

The production go-by reports **3085** logical connections, e.g.

```
+P03-CA[2] -> +P03-E-A[1]    kindOfWire=IndividualConnection
```

so the reader works against real data, not just synthetic pages.

## The connection-line geometry bug this exposed

`SetGraphics(p1, p2)` takes coordinates **RELATIVE to the line's `Location`**, not
absolute page coordinates. Confirmed by reading real, human-drawn lines:

| | Value |
|---|---|
| `Location` | (326.39, 346.71) — the absolute anchor |
| `GetGraphics()` | a `Line` from (0,0) to (-1.27, 2.54) — relative |
| `GraphicalConnectionPoints` | also relative |

The first version of `live_connect_pins` passed **absolute** coordinates with
`Location` left at its default, which put one end of every wire at the **page
origin**:

```
DynamicConnectionLine  loc=(0,0)  pins=[(0,0), (139.7, 206.375)]
```

A line that visibly exists, reports success, and connects nothing — exactly the
failure the pin-frame handling was written to prevent, arriving by another route.

**Fixed**: anchor `Location` at the first pin, then draw the segment relative.
After the fix:

```
DynamicConnectionLine  loc=(60.325, 206.375)
                       pins=[(60.325, 206.375), (139.7, 206.375)]
Function +-WA1         pin 0 = (60.325, 206.375)     <- exact match
Function +-WA2         pin 0 = (139.7, 206.375)      <- exact match
```

## UNRESOLVED: a drawn line did not become a logical Connection

With the geometry correct, both devices tagged (`+-WA1`, `+-WA2`) and
`eplan_generate_connections` run successfully, the page still reported **0**
logical connections.

What was ruled out:
- **Untagged devices** — tagging both made no difference.
- **Wrong geometry** — the line's connection points now coincide exactly with
  the device pins.
- **Generation not running** — `generate_connections` returned success, and the
  project's 3085 existing connections were unaffected.

Still open: whether the `SL` symbol from `NFPA_symbol_en_US` carries real
electrical connection points, whether generation is scheme- or scope-driven, or
whether programmatically created lines need something further.

**This is why `live_connect_pins` reports `lineDrawn`, never `wired`** — and why
`live_read_connections` exists. The gap is real, and it is now measurable rather
than assumed.

## RESOLVED (2026-09-03): generation works, but it ignores the drawn line

Two findings, and the second is the important one.

**1. Connections must be generated, and `generate /TYPE:CONNECTIONS` does it.**
The earlier "0 connections" reading was taken before generation had produced
anything for that page. On a page with four devices, generation moved the
project from 3085 to 3087 connections.

**2. EPLAN connected the devices it thought were adjacent, NOT the ones the
drawn lines joined.**

Placed and tagged:

| Device | Position | Pins |
|---|---|---|
| `-K1` | (60.3, 241.3) | 0 above at y=247.65, 1 below at y=234.95 |
| `-K2` | (139.7, 241.3) | same shape |
| `-K3` | (60.3, 181.0) | pin 0 at y=187.325 |
| `-K4` | (139.7, 139.7) | pin 0 at y=146.05 |

Lines drawn: `-K1 → -K2` (horizontal, same Y) and `-K3 → -K4` (routed, via a
corner). **Three** DynamicConnectionLines on the page.

What generation produced:

```
+-K1[2] (60.325, 241.3)  ->  +-K3[1] (60.325, 180.975)
+-K2[2] (139.7, 241.3)   ->  +-K4[1] (139.7, 139.7)
```

Those are the **vertically aligned** pairs — same X, bottom pin facing top pin.
Not one of the three drawn lines produced a connection.

### What this means for the wiring primitives

EPLAN's auto-connect works on **device alignment**, not on graphical lines added
through the API. The EPLAN-native way to wire two devices is to POSITION them so
their connection points face each other and let generation create the
connection; a `DynamicConnectionLine` placed programmatically is decoration
unless it participates in that logic.

So `live_connect_pins` and `live_connect_pins_routed` draw something visible and
geometrically correct that does **not** wire anything. Their geometry fixes
(anchoring, corners) remain valid and worth keeping, but the primitive a caller
actually needs is placement-driven: put the devices where their pins meet.

Recorded rather than patched, because the fix is a design change to the wiring
layer and not a bug in these two tools.


---

# How EPLAN actually wires a schematic

**Measured** 2026-09-03 across 30 pages of a production project. This supersedes
every earlier guess in this file about connections, including two of mine.

## Nothing draws a wire. Everything is a placed symbol.

| Need | Mechanism | Object | Seen in 30 pages |
|---|---|---|---|
| Straight run between aligned pins | autoconnecting line - implicit | **none** | - |
| Corner / turn | place a `CO` symbol (v0-v3 = 4 orientations) | SymbolReference | 2241 total |
| T-node / branch | place `TLRO` / `TLRU` | SymbolReference | (included above) |
| Wire number, colour, cross-section | `CDPNG` / `CDPNG2` on the connection | ConnectionDefinitionPoint | 1033 |
| Cross-page jump | `BP`, paired by name (A, B, C...) | InterruptionPoint | 156 |
| Diagonal / free routing | `DynamicConnectionLine` | rare | **18, all on ONE page** |

All from `SPECIAL_en_US` / `Symbol Library - Special`.

## The autoconnecting line is not an object

A page built with four devices and no lines at all:

```
placements: 4 (all Function)   line objects: 0
rendered:   two vertical lines between the vertically-aligned pairs
connections after generation:
    +-K1[2] (60.325, 241.3) -> +-K3[1] (60.325, 180.975)
    +-K2[2] (139.7, 241.3)  -> +-K4[1] (139.7, 139.7)
```

EPLAN renders the line between connection points that FACE EACH OTHER on a
shared axis, and generation turns it into an `IndividualConnection`. There is
nothing on the page to create, address or delete.

## A corner is a symbol, not a drawn elbow

`CO` has connection points on two perpendicular sides, so it autoconnects to
whatever is aligned above/below and left/right of it. Turning a corner means
PLACING `CO` at the turn - not drawing two segments. Its four variants are the
four orientations.

## Why the first attempt failed

`live_connect_pins` drew a `DynamicConnectionLine` between two pins for an
ordinary straight run. That primitive is real but rare - 18 instances in 30
pages, all on one detail page, used for genuine diagonals. Using it for a
straight run produced a line that was geometrically exact, visible on the page,
and connected to nothing, because the connection comes from ALIGNMENT and not
from the line.

The correct primitive set is placement-driven:

  place a device so its pin faces an existing pin  -> straight run
  place `CO` at a turn                             -> corner
  place `TLRO`/`TLRU`                              -> branch
  place `CDPNG` on a connection                    -> wire properties
  place `BP` pairs                                 -> cross-page
  `DynamicConnectionLine`                          -> diagonals only

## Note for the convention profile

Learning from a go-by ranked `Symbol Library - Special / CO` top with 175
observations. That is not a device - it is the corner symbol, and it is the most
common thing on a schematic page. A profile that treats it as device vocabulary
is mis-reading the page; connection symbols should be classified separately.
