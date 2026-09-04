# Replicating a Real Production Page: What Broke

A test set by the user: given a photo of a real safety-circuit page (STO stop
chains for two sub-lines, three emergency-signal interconnection groups, a
20-terminal strip `-X4.21`, and a `-W5` UNITRONIC cable to three plug pins),
rebuild it on a fresh page with the `live_*` primitives.

The contact ladders were rebuilt and **electrically verified**. Four other
things broke, and each one is a finding worth more than the drawing was.

## 1. The active project can change under you mid-session

`eplan_live_query_pages` reported the scratch clone while
`eplan_live_symbol_catalog`, called minutes later, reported
`Macros_7C0x_v.7.13.01_WIP` — a 358-page production project. The user had
switched projects in EPLAN.

The write guards held (`allow_real_project` defaults to False, and the scratch
root check is structural), so nothing was written to production. But the
lesson generalises: **the project is session state, not a constant**, and two
tools reading it at different moments can legitimately disagree.
`eplan_get_current_project` plus one `live_query_pages` is a cheap
cross-check, and it is worth doing before any batch of writes rather than
trusting the project you started with.

## 2. `live_place_symbol` cannot place a terminal — and fails non-atomically

```
live_place_symbol(page, "IEC_symbol", "X", x=60, y=144, variant_nr=0)
  -> {"success": false,
      "error": "Function.Create: NotImplementedException: ..."}
```

`IEC_symbol/X` is the plain terminal symbol; `live_symbol_catalog` reports its
`symbolType` as `Function` and lists sane pin geometry (v0: `(0,+2)` and
`(0,-0.75)`). It still cannot be placed: EPLAN promotes the created object to
the `Terminal` subclass based on the symbol, and `Function.Create` cannot
finish that.

**The dangerous part is not the failure, it is what the failure left behind.**
The very next `live_read_page` showed `placementCount: 5` against four
Functions, and the fifth was:

```
{"clrType": "Terminal", "handle": "28/17/199031/0", "name": "=+++",
 "location": {"x": 0.0, "y": 0.0}, "symbol": {"library": "IEC_symbol",
 "name": "X", "variantNr": 0}, "pins": [... "frame": "absolute" ...]}
```

A real Terminal, stranded at the page origin, after a call that reported
`success: false`. The object was created and then the positioning threw. This
is the same "born at origin" hazard `live_place_connection_symbol`'s docstring
warns about — except there the transit completes inside one locking step,
and here it never completes, so the stray object persists. A later
`generate_connections` could wire anything near `(0,0)` to it.

**Rule:** after any `live_place_symbol` that returns `success: false`,
re-read the page and check `placementCount`. Do not assume a failed write
wrote nothing. The orphan removes cleanly with
`live_remove_placement(handle, expect_type="Terminal")`.

Consequence for the replica: the 20-terminal strip `-X4.21` could not be
built at all. It was stood in for with `SPECIAL/BPIN` variant 3 — a
single-pin interruption point facing Up, which terminates a downward run —
clearly marked as a substitution, not a terminal.

## 3. A run between two interruption points produces no `Connection`

The 0V rail refused to wire, three different ways:

| Attempt | Geometry | Result |
|---|---|---|
| `BP(40,280)` → `CO(84,280)` → `BPIN(84,144)` | rail above the 24V rail | no connection |
| `BP(40,264)` → `CO(84,264)` → `BPIN(84,144)` | rail below it | no connection |
| `BPOUT(84,272)` → `BPIN(84,144)` | one clean vertical, nothing between | **no connection** |

Two hypotheses died on the way: "`y=280` is outside the page's connectable
area" (killed by attempt 2) and "the horizontal crosses the two 24V verticals
at `(60,264)`/`(72,264)`" (killed by attempt 3, which has no horizontal at
all). What survives is the pattern shared by all six connections that *did*
form: **every one has at least one `Function` endpoint.** No connection formed
between two `InterruptionPoint`s, even with facing pins on a shared axis and
nothing in between.

## 4. An interruption point cannot be named

```
live_set_device_tag(page, handle_of_BP, "-P2_24V7")
  -> {"success": false, "error": "Only a Function can carry a device tag;
       handle resolves to a InterruptionPoint."}
```

A clean refusal with the real reason — correct tool behaviour. But it means
the potentials `P2_0V7` / `P2_24V7` cannot be labelled through this toolset,
and it leaves finding 3 one step short: interruption points pair across pages
*by name*, so an unnamed pair may simply be incomplete rather than structurally
unable to connect. Separating "needs a Function endpoint" from "needs a name"
is not possible with the primitives available. Both readings are consistent
with everything measured; the note above states the measurement, not a cause.

## 5. `live_set_device_tag` under-reports a merge

A contactor's second pole is exactly the legitimate merge case, so:

```
live_set_device_tag(handle_2, "-K4.011", allow_merge=True)
  -> {"merged": false, "name": "=+++-K4.011"}
```

`merged: false` — while the page already held a function named
`=+++-K4.011`. The merge did happen where it counts: `live_read_connections`
afterwards reports both handles under the same device and links
`-K4.011[1]@(60,224)` to `-K4.011[1]@(72,224)`. Only the flag is wrong, and
the cause is visible in the tool's own note: EPLAN stores the tag as
`=+++-K4.011` (project structure settings reformat it) while the duplicate
check compares against the requested `-K4.011`. The check should compare
against the stored form.

This matters beyond cosmetics: the same comparison backs the *refusal* when
`allow_merge` is False, so a genuine accidental duplicate would not be caught
either.

## What did get built, and verified

```
       40   50   60   70   80   90
        |    |    |    |    |    |
272 |   »         ┬     ┐     »          <- 24V interruption point, T-node,
224 |             a─────b                   corner; 0V (unconnected)
220 |             │     │
188 |             c     d
144 |             »     »     »          <- stand-ins for -X4.21:1/:2/:3
```

`a`,`b` = `-K4.011` poles 23-24 and 33-34; `c`,`d` = `-K4.012` poles 23-24 and
33-34; all four `IEC_symbol/S` (NO contact) v0, the second pole of each relay
merged into the same device tag.

Six `Connection` objects, read back with `live_read_connections`:

1. `-K4.011[2]@(60,224)` → `-K4.012[1]@(60,188)` — column 1 in series
2. `-K4.011[2]@(72,224)` → `-K4.012[1]@(72,188)` — column 2 in series
3. `-K4.011[1]@(60,224)` → `-K4.011[1]@(72,224)` — the 24V rail joining both
4. `BP(40,272)` → `-K4.011[1]@(72,224)` — the potential entering
5. `-K4.012[2]@(60,188)` → stand-in `-X4.21:1`
6. `-K4.012[2]@(72,188)` → stand-in `-X4.21:2`

That is the STO chain of the original: both poles of `-K4.011` fed from one
potential, each in series with the matching pole of `-K4.012`, each landing on
its own terminal.

**Not replicated:** the terminal strip itself (finding 2), the 0V column
(finding 3), the potential names (finding 4), the `-W5` cable and its three
plug pins, the wire cross-section labels (`A₂ 1,5 mm²` — these are
`ConnectionDefinitionPoint` properties, and no primitive sets them), the
magenta path function texts, and the three right-hand interconnection groups,
which are structural repeats of the same pattern and would hit the same
terminal blocker.

**Assumption stated rather than read:** which rail feeds which column. The
source image resolves the two potentials entering top-left and three verticals
descending into the left group, but not reliably which vertical belongs to
which rail. The build feeds both contact columns from 24V and the third from
0V; that is a reading, not a measurement.

## Where the ASCII map earned itself

`07-page-to-ascii.md`'s renderer is what made finding 3 visible in one look.
The connection JSON says six connections exist; it does not say what is
*missing*. The map does — the 0V column rendered as three isolated glyphs with
no line between them, next to two fully-wired columns:

```
272 |   »         ┬     ┐     »       <- 0V symbols present
224 |             a─────b             <- 24V columns wired
144 |             »     »     »       <- third stand-in reached by nothing
```

Reading that from a 12-placement coordinate list would have meant checking
each pair by hand. This is precisely the failure mode
`02-routing-and-connections.md` warns about — `generate_connections` does not
report a wire it declined to create.
