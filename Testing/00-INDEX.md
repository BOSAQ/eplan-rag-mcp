# Live Testing Notes — Schematic Authoring (Session 2026-09-04)

Replaces the old `Audit/` folder (a Spanish-language, action-by-action coverage
log for the general `eplan_action_*` catalog). This folder is narrower and more
concrete: it records what was actually **measured live** while building a real
control-circuit schematic ("puesta en marcha" / motor start) end-to-end through
the six `live_*` schematic-authoring primitives in `schematic.py`, on EPLAN
2025/2027, project `EJEMPLO` (cloned to a scratch project throughout — the
original was never written to).

Every claim below was produced by an actual tool call in this session and can
be re-verified the same way: place something, `live_read_page`/
`live_read_connections` it back, compare.

## Files

| File | Covers |
|---|---|
| [01-schematic-authoring-primitives.md](01-schematic-authoring-primitives.md) | The six primitives (`live_symbol_catalog` → `live_place_symbol` → `live_connect_pins` → `live_read_page` → `live_remove_placement`), plus `live_place_connected` and `live_set_device_tag`. What each returns, what "connected" actually means. |
| [02-routing-and-connections.md](02-routing-and-connections.md) | Corners, T-nodes, crosses (`Symbol.Type` taxonomy). How `generate_connections` + `live_read_connections` prove a circuit is real, not just drawn. A worked example: a self-holding motor-start rung with a verified parallel branch. |
| [03-coordinate-system-and-designations.md](03-coordinate-system-and-designations.md) | Confirming `+Y = up` on the page from placed reference terminals. The full four-part device designation (`=function++installation+location-device`) vs. the bare device-tag suffix most calls default to. |
| [04-interactive-vs-scripted-placement.md](04-interactive-vs-scripted-placement.md) | Two different ways to put a symbol on a page — the scripted `Function.Create` path (`live_place_symbol`) vs. the native GUI action `XEGActionInsertSymRef` (F3-equivalent) — their trade-offs, and a real device-drift bug caught by re-reading the page after a hang. |
| [05-external-symbol-dataset.md](05-external-symbol-dataset.md) | Cross-referencing a public HuggingFace symbol-description dataset against this project's live `IEC_symbol` library — where it helped, where it didn't, and what it confirmed. |

## Ground rules this session actually followed

- **Writes stayed scratch-only.** Every placement in these notes happened in a
  disposable clone (`eplan_scratch_project_create`), never the user's real
  `EJEMPLO.elk`.
- **A write is not "connected" until proven.** `live_place_symbol` /
  `live_place_connected` report facing pins on a shared axis — that is
  geometry, not a logical connection. Only `generate_connections` +
  `live_read_connections` settle whether EPLAN actually wired two devices.
- **Nothing here was read from a screen.** Every fact — including catching a
  device that had drifted position — came from diffing two JSON reads
  (`live_read_page`/`live_read_connections`) against each other, never from
  pixels.
