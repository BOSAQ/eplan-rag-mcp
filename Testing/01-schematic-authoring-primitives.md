# The Schematic-Authoring Primitives

Measured against `schematic.py`'s six primitives (`live_symbol_catalog`,
`live_create_page`, `live_place_symbol`, `live_connect_pins`, `live_read_page`,
`live_remove_placement`), plus two write helpers that turned out to matter
more in practice than the six: `live_place_connected` and `live_set_device_tag`.

## `live_symbol_catalog` — three depths, no guessing

Symbol names are not guessable and are not universal across installations.
On this project's `IEC_symbol` library: `S`=NO contact, `O`=NC contact,
`SL`=power NO contact of a contactor, `K`=relay/contactor coil, general. These
are the German Schließer/Öffner convention (S/O), not something derivable from
English naming.

```
live_symbol_catalog()                                   -> project's libraries
live_symbol_catalog(library="IEC_symbol")                -> symbols + pin counts
live_symbol_catalog(library="IEC_symbol", symbol="SL")    -> variants + pin geometry
```

At depth 3, pin `"raw"` coordinates are relative to the symbol's own insertion
point — meaningless as page coordinates until actually placed. Depth-2
`connectionPoints >= 2` is the practical filter for "this can be wired."

**But depth 2 does not list everything, and claims it does.** The listing
stops after `SymbolId` 72 while reporting `truncated: false`; a symbol above
that id is invisible to it and to its `contains` filter, yet resolves and
places fine when named at depth 3. Never read "not in the depth-2 listing" as
"not in the project" — see `09-symbol-catalog-enumeration-gap.md`.

## `live_place_symbol` — the reliable, headless path

Calls `Function.Create(Page, SymbolVariant, PointD, PointD)` directly against
the object model. No GUI, no cursor, no click. Every placement in this
session's circuit went through this path and it was 100% reliable across ~20
calls.

A newly placed function's `name` is literally `"+"` — it is anonymous until
tagged (see `live_set_device_tag` below). That is expected, not a failure.

## `live_place_connected` — removes the coordinate arithmetic

`live_place_connected(page, to_handle, to_pin, library, symbol, distance,
variant_nr)` places a new symbol so its own facing pin lines up with an
existing placement's pin, `distance` millimetres away along the shared axis.
No coordinate is computed by the caller — the tool reads the anchor pin's
absolute position and does the arithmetic.

**Restriction found live:** this only works when the anchor is a `Function`
(built through the same `Function.Create` path). A routing symbol
(`SymbolReference`, built through `SymbolVariant.Create` — see
`02-routing-and-connections.md`) is not a valid anchor. Chaining through a
T-node or corner requires computing its exact pin coordinate — usually the
placement point itself, since junction symbols place every leg at offset
`(0,0)` — and placing the next device there directly with `live_place_symbol`.

## `live_read_page` — the one source of truth

Returns every placement's `clrType`, `handle`, `name`, `location`,
`boundingBox`, `symbol{library,name,variantNr}`, and `pins` (with `direction`
and both `raw` and absolute `point`). This is genuinely the only way this
session ever knew what was on a page — there is no visual channel. See
`04-interactive-vs-scripted-placement.md` for a case where this caught a real
bug (a device that had silently moved).

`types=["Function"]` matters in practice: a production page can be mostly
graphics/routing symbols, and the default `limit` will exhaust on those before
reaching a single device.

## `live_set_device_tag` — and the four-part designation gap

`Function.Name` is directly writable, but a call like `tag="-K1"` only fills
the device-tag suffix. See `03-coordinate-system-and-designations.md` for the
full designation format and how to fill the rest (`=function++installation+location`)
in the same call.

**Duplicate tags merge, deliberately.** Tagging a second placement with a tag
already used on that page does not error — it merges the new placement in as
a further sub-function of the same device (`allow_merge=True` required). This
is exactly right for a contactor's coil and its power-contacts, which really
are one physical device with several symbols on the page. Verified live:
merging three power-pole `SL` placements plus one auxiliary `S` contact all
under the same `-K1` tag produced one device with four sub-functions, and
`live_read_connections` correctly attributed all of them to `-K1`.

## `live_connect_pins` — usually the wrong tool

Two facing pins on a shared axis are *already* wired: EPLAN autoconnects them
and no line object exists on the page. `live_connect_pins` (and its routed
sibling) draw an *explicit* `DynamicConnectionLine` — the right tool only for
a run that truly cannot be expressed as aligned placements. Every connection
in this session's circuit was made by placement alignment, never by drawing a
line.
