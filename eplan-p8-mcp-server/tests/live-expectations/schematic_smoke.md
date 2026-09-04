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
