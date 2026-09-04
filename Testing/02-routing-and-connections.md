# Routing Symbols, and Proving a Connection Is Real

## Nothing draws a wire

A straight run between two aligned, facing pins needs no object at all —
EPLAN autoconnects it. Every junction shape is a placed *symbol*, not a drawn
line:

| Job | `Symbol.Type` | This project's symbol(s) |
|---|---|---|
| Corner / turn | `Routing` | `CO` (4 variants = 4 quadrants) |
| Branch, third leg up | `TNodeUp` | `TLRO` **and** `TLRO_1` (same type, different pin order — see below) |
| Branch, third leg down | `TNodeDown` | `TLRU` |
| Branch, third leg left | `TNodeLeft` | `TOUL` |
| Branch, third leg right | `TNodeRight` | `TOUR` |
| Four-way crossing | `RoutingCross` | `CR` |
| Hop over, no connection | `RoutingBridge` | `BR` |
| Free/diagonal routing | `DynamicRouting` | `CRL` |
| Cross-page jump | `InterruptionPoint` | `BP` (seen live tagged `-0V60`) |
| Wire number / colour / gauge | `ConnectionDefinition` | `CDP` family (19 variants — see below) |

All confirmed live via `eplan_live_routing_catalog()` against this project —
matched exactly against a user-supplied screenshot of the same short-name /
number / description table from EPLAN's own symbol browser, which is strong
independent corroboration that these names are stable for this install.
`05-external-symbol-dataset.md` cross-checked the same names a *third* way,
against a public Hugging Face dataset, and the numeric ids agree exactly
(`TLRU`=64, `TOUR`=66, `TOUL`=67, `BR`=68, `CR`=69, `CO`=70, `CRL`=71,
`TLRO`=72, `BP`=8) — three independent sources landing on the same numbers is
about as confirmed as a symbol name gets without EPLAN's own docs saying so
directly. That same cross-check also surfaced 19 `CDP*` variants
(`ConnectionDefinitionPoint`) with real ids — `CDP` itself is 308, plus
no-graphic forms, optical-fibre, and a piping-specific `CDPCP2F1`–`F5`
family — none of which had been enumerated from this project directly.

**A corner's two connection points sit at the exact same coordinate as the
corner itself** — placing one *is* placing both pins; one `(x,y)` positions
the whole thing. `live_place_corner(directions=[...])` resolves which variant
to use from the direction pair, discovered from the project rather than
assumed (`v0=Right+Down, v1=Right+Up, v2=Left+Up, v3=Left+Down` on this
install's `CO`).

**A T-node's placement is chosen by `Symbol.Type`, not by variant** — `Up`
vs `Down` vs `Left` vs `Right` are different symbols entirely
(`live_place_tnode` takes `branch_direction`, not a direction list). Within
one type there can still be more than one symbol: this project's `TOUR`
(`TNodeRight`) alone has 5 variants, and variant **8** is the one whose three
legs *all* sit at offset `(0,0)` — the cleanest choice for sitting exactly on
an existing pin. `live_place_tnode` will refuse and list every candidate
rather than silently picking one when more than one variant matches the
requested directions.

## Geometry is not electricity

`live_place_symbol` / `live_place_connected` / `live_place_corner` /
`live_place_tnode` all report what was **drawn** — facing pins, coincident
points. None of that is a logical connection yet. `generate_connections`
(action `TYPE:CONNECTIONS`, `REBUILDALLCONNECTIONS:1` to force a full
rebuild) is what actually makes EPLAN compute `Connection` objects, and only
`live_read_connections` (read-only, will not trigger generation itself) can
confirm what got wired to what — by device tag, pin index and designation,
not by trusting the geometry.

## Worked example: a real self-holding start/stop rung

Built and verified live (arrangement, not full production circuit — see
`00-INDEX.md` for the scratch-only caveat):

```
        -S0 (SOA, stop, NC)
             |
        -F1 (FT1, thermal relay, NC)
             |
      ● ─────┴───────────────●   node X  (TOUR v8, TNodeRight)
      |                      |
  -S1 (SSA, start, NA)   -K1 (S, aux NA contact)   <- IN PARALLEL
      |                      |
      ● ─────┬───────────────●   node Y  (TOUR v8, TNodeRight)
             |
        -K1 (K, coil)
```

Column B (the auxiliary branch) reaches node X and node Y through two `CO`
corners (`Left+Down` at the top, `Up+Left` at the bottom) — a horizontal run
at each node's own y-coordinate, then a vertical run through the aux contact.

`generate_connections` + `live_read_connections` returned exactly 5
`Connection` objects, and the proof of the parallel branch is in the last
two: `F1[2] -> K1aux[1]` and `S1[2] -> K1aux[2]` — the auxiliary contact
touches the *same two electrical nodes* `S1` sits between. That is what makes
it a real parallel branch rather than a decorative second symbol near the
first.

**A caution found live, twice:** if any device in a chain drifts even 2mm off
the shared axis (see `04-interactive-vs-scripted-placement.md` for how that
happened), `generate_connections` does not error — it silently reconnects
around the misaligned device, connecting whatever *is* still aligned. A
thermal relay that drifted off-axis vanished from every connection without a
single error message; the fault was only visible by reading
`live_read_connections` and noticing the device's tag was absent from all 5
results.
