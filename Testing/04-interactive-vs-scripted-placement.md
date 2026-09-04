# Two Ways to Place a Symbol, and a Bug Caught Between Them

## Scripted (`live_place_symbol`): headless, reliable, and it does render

Calls `Function.Create(Page, SymbolVariant, PointD, PointD)` directly. No
cursor, no click, no GUI interaction of any kind — and, contrary to an
earlier hypothesis in this same session, **the placed symbol's pictogram
does render normally** in the graphical editor. What does *not* get filled in
by this path is the rest of the four-part device designation (see
`03-coordinate-system-and-designations.md`) unless the full string is passed
to `live_set_device_tag` explicitly.

## Native/interactive (`XEGActionInsertSymRef`, wrapped as
`eplan_insert_symbol_reference`): the real F3 path

This is the same action EPLAN's own F3 "insert symbol" dialog dispatches.
Its official parameter list (`SymbolLibName`, `SymbolId`, `VariantId`,
`FctDefTag`, `Placementmode`, `SymbolType`, `CustomSymbols`) has **no
coordinate parameter at all** — confirmed against the action's own published
parameter docs, not inferred. Calling it only *arms* a placement interaction
in the graphical editor; a human click is what actually places the symbol,
and there is no way to supply that click headlessly through this action.

`SymbolId` is a numeric master-data ID, not the symbol's short name — and the
`number` column of the external dataset in `05-external-symbol-dataset.md`
turned out to be exactly that ID (`SL` → `number: 0`, matching `SymbolId: 0`).

**Measured failure mode, live:** calling this action left the connected
EPLAN instance completely unresponsive — not just "an interaction is armed
and needs a click," but `eplan_ping` itself returned `alive: false`
afterward, and the very next `get_system_messages` call also timed out. A
plain reconnect (`eplan_connect`) recovered it, on a *different* remoting
port than before (49152 → 49153), implying EPLAN itself had restarted or the
remoting host had cycled. **Do not assume this action is safe to fire
unattended without a way to detect and recover from that outcome.**

## The bug this combination produced, and how it was caught

After the reconnect, `live_read_page` on the working page showed a
`FT1` (thermal relay) device at `(114, 254)` — not the `(100, 252)` this
session's own prior tool-call output had recorded when it was placed. No
tool reported an error; the discrepancy was only visible by treating the
earlier tool response as a fact to diff a fresh read against.

Effect: `generate_connections` did not fail or complain — it silently
reconnected the chain *around* the drifted device, producing `S0 -> S1`
directly (skipping the thermal relay entirely) instead of `S0 -> F1 -> S1`.
The circuit still "generated cleanly"; it was simply wrong, and stayed wrong
until someone thought to compare positions across two reads.

**Fix applied:** `live_remove_placement` on the drifted handle, re-place with
`live_place_symbol` at the intended coordinate, re-tag, `generate_connections`
with `rebuild_all=True`, then `live_read_connections` again to confirm the
missing device was back in the chain.

**Takeaway for future sessions:** after any interactive/GUI-adjacent action
(`XGedStartInteractionAction`, `XEGActionInsertSymRef`, `insert_device`, or
any reconnect following one of those), re-read the page and its connections
before trusting prior placement coordinates — do not assume state is stable
just because the last write call before the interruption reported success.
