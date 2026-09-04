# `MacroBox` by Raw Reflection — Outside the Standard Primitives

The six `live_*` primitives (`01-schematic-authoring-primitives.md`) cover
`Function` placements and, via `live_place_corner`/`live_place_tnode`, the
`SymbolReference` routing types (`02-routing-and-connections.md`). Neither
path knows about `MacroBox` — a third placement kind that appeared on the
page after converting a placed selection into a reusable macro through the
GUI. This file documents what `execute_custom_script` (arbitrary reflection,
confirmed with the user before each call per that tool's own contract)
revealed about it.

## `live_read_page` cannot see a MacroBox's name

`DumpPlacement`, the shared serializer behind every read, tries `Name` on
every placement (see `01-schematic-authoring-primitives.md`). `MacroBox` has
no `Name` property at all — it shows up as `"absentMembers": ["Name"]`,
which is correct behaviour (an absence is reported, never silently dropped)
but leaves a real gap: the one thing you'd want to know about a macro
placement — which macro file it is — is invisible through the normal read
path.

## The real property is `MacroBox.MacroName`

Found by dumping the type's full property list via reflection
(`Eplan.EplApi.DataModel.MacroBox`), then testing plausible candidates
(`Name`, `Path`, `MacroFileName`, `FileName`, `Caption`, `Text`,
`Description`, …) against a live placement. Only one hit:

```
MacroBox.MacroName : System.String = "ProductoSTD\ELEC\1PGxSTL_v.1.xx.xx\Galibo.ema"
```

Everything else on that candidate list is genuinely absent on this type — not
a naming guess that happened to miss, confirmed by reading the type's full
member list rather than trial and error alone.

## It is a plain writable string, and the write was verified

`GetProperty("MacroName")` reports `PropertyType.FullName == "System.String"`.
Unlike `Function`-side properties that route through
`PropertyValue.op_Implicit` (see `MakeValue`/`SetProp` in `live.py`, and the
`PropertyValue` note in `schematic.py`'s module docstring), a direct
`PropertyInfo.SetValue(box, "...", null)` worked with no conversion step.

Test performed: replaced `"Galibo"` with `"hello_world"` inside the path
string (`.../1PGxSTL_v.1.xx.xx/hello_world.ema`), then immediately re-read
`MacroName` on the same object rather than trusting the absence of an
exception:

```json
{
  "macroNameBefore": "ProductoSTD\\ELEC\\1PGxSTL_v.1.xx.xx\\Galibo.ema",
  "macroNamePropertyType": "System.String",
  "writeRoute": "direct SetValue(string)",
  "macroNameAfter": "ProductoSTD\\ELEC\\1PGxSTL_v.1.xx.xx\\hello_world.ema",
  "writeConfirmed": true,
  "isValid": "True",
  "isPlaced": "True",
  "referencedPlacementsCount": 0
}
```

## Second run: a populated macro box, `Variant`, and where `Description` isn't

Repeated against a *different* macro box the user placed afterward — this one
genuinely populated (`ReferencedPlacements` = 12, not 0: 5 `SymbolReference`,
2 `Function`, 2 `ConnectionDefinitionPoint`, 1 `PathText`, 1
`InterruptionPoint` — matching exactly what `live_read_page` separately
reported as new top-level placements on the page). This answers the open
question from the first run:

**The rename did not touch the content.** `MacroName` went from
`ProductoSTD\ELEC\7APx_v.7.xx.xx\AP\300.11_TT=ROLLERS.ema` to
`...\hello_there.ema` (only the filename segment replaced, directory and
`.ema` extension preserved by parsing rather than hardcoding "Galibo" —
the first test's fixed string obviously would not have generalised).
`ReferencedPlacements` still reported 12 objects immediately afterward,
`IsValid`/`IsPlaced` still `True`. So for at least this case, `MacroName` is
purely a label — changing it does not re-resolve or discard the already-
referenced content. Still not proof of the *positive* case (pointing
`MacroName` at a genuinely different macro file and expecting new content to
load) — only that renaming in place is non-destructive.

**`Variant` is real and simple.** A separate property from `MacroName`:
`MacroBox.Variant = "0"` — the macro's variant index, a plain integer-as-
string. Directly readable, no ambiguity.

**`Description` is not reachable through the object model, at any of the
three places it could plausibly be:**

1. `MacroBox` itself — confirmed absent already (see above).
2. `MacroBox.SymbolVariant` (`Eplan.EplApi.DataModel.MasterData.SymbolVariant`)
   — dumped its full property list and tried 9 description-shaped candidate
   names (`Description`, `Description0`, `LongDescription`, `Comment`,
   `Caption`, …). None exist; its real properties are geometry/identity only
   (`ConnectionPoints`, `SymbolLibraryName`, `SymbolName`, `VariantNr`,
   `SubPlacements`, …).
3. `MacroBox.Properties` (`Eplan.EplApi.DataModel.MacroBoxPropertyList`) — the
   type exists and is readable, but enumerating it returned zero entries.

**Working conclusion:** a macro's description, if the `.ema` file has one, is
metadata carried inside that file on disk — not something EPLAN loads onto
the in-memory placement object once a macro box references it. Reading it
would mean parsing the `.ema` file directly, or reading it through the GUI's
own macro-properties dialog; neither was attempted here.

## What these two tests do, and do not, prove

- `IsValid` and `IsPlaced` stayed `True` after both writes — neither a
  string mutation on an empty box nor one on a populated box (12 referenced
  placements) left the object in a broken state.
- **Resolved by the second run:** renaming a *populated* box's `MacroName`
  does not touch its `ReferencedPlacements` — same 12 objects, same types,
  read back immediately after the write. So `MacroName` behaves as a pure
  label on this install; it does not trigger EPLAN to re-resolve or reload
  content from whatever file the new name points to.
- **Still untested, and this is the actual open question now:** pointing
  `MacroName` at a *genuinely different, real* macro file (not just a cosmetic
  rename of the same one) and checking whether EPLAN ever picks that up —
  on next page open, on a manual refresh, on some explicit call. Nothing in
  this session forced a re-resolve, so silence here is not evidence either
  way. `MacroBox` also exposes `InsertMacroBox`, `SwitchLocalPropertyPlacements`,
  `LogicalAreaSegments` and `GetLogicalArea()` — one of those, not a bare
  property write, is the more likely correct route to actually *swap* which
  macro a box's content comes from.
- The action interaction layer (`04-interactive-vs-scripted-placement.md`)
  lists `XMIaSwapMacro` as an interaction name observed in EPLAN's own GUI
  action map but never verified in this project — worth trying first, next
  time a populated macro box genuinely needs to reference a different file.
