# Page Coordinates and the Full Device Designation

## `+Y` is up

Not documented anywhere consulted this session — confirmed by placing four
reference terminals (symbol `PLCCPNG`, `SPECIAL` library) by hand in the GUI
at the four corners of a drawing frame, then reading them back:

| Tag | Location |
|---|---|
| `-TOPL:1` | `(0, 292)` |
| `-BOTL:1` | `(0, 16)` |
| `-TOPR:2` | `(420, 292)` |
| `-BOTR:2` | `(420, 16)` |

`Y=292` is the visual top of the page, `Y=16` the visual bottom. So in the
`PointD` page coordinate system, **Y increases upward** — consistent with,
and now confirmed by, a placed symbol's `"Up"`-direction pin always carrying
a positive relative Y offset (e.g. `SL` variant 0: `Up` pin at `(0,+4)`,
`Down` at `(0,-4)`).

This matters because a person describing a page region in "reading order"
(top row first, Y increasing *downward*, as one would describe a print
layout or a screen) will produce coordinates that are the vertical mirror of
what `PointD` expects. **Placing four cheap reference terminals at the frame
corners and reading them back is the fast, unambiguous way to settle this for
any given project** — cheaper and more reliable than inferring it from a
single pin direction.

## Centering inside a known frame

Given the frame's real extent (from the terminals above: `x: 0–420`,
`y: 16–292`), centering an existing group of placements is arithmetic, not a
new primitive:

1. Read every placement's `location` and take the bounding box.
2. `dx = frame_center_x - group_center_x`, `dy = frame_center_y - group_center_y`,
   rounded to a multiple of the page's `gridSize`.
3. There is no "move" primitive. Each placement must be removed
   (`live_remove_placement`) and re-placed (`live_place_symbol` /
   `live_place_tnode` / `live_place_corner`) at `location + (dx, dy)`, then
   re-tagged and `generate_connections` re-run. Handles from before the
   removal are invalid afterward — re-collect them from the placement calls,
   not by memory.

## The full designation vs. the bare device tag

`live_set_device_tag`'s `tag` argument is written straight to `Function.Name`
and accepts EPLAN's complete structure-identifier syntax, not just a device
tag:

```
=<function designation>++<installation location>+<location designation>-<device tag>
e.g.  =CTRL++CAB1+MANDO-K1
```

Passing only `-K1` fills *just* the trailing device-tag segment; EPLAN then
reports the name back as `=+++-K1` (or `=+++<page-location>-K1` if the page's
own location designation gets inherited) — the leading `=`, `++...`, `+...`
segments are present but **empty**. That is easy to miss because the call
still succeeds and the device is still individually addressable by that
name — it only becomes visible as a gap when a person familiar with EPLAN's
convention looks at the page and asks where the function/installation/
location prefixes went.

Passing the full string (`=CTRL++CAB1+MANDO-K1`) was accepted verbatim and
stored exactly as given — confirmed live, no reformatting. There is no
special "designation" argument to discover; the fix is simply to build the
whole string when it matters.
