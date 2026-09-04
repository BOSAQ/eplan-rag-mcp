#!/usr/bin/env python3
"""
Render an EPLAN page as an ASCII/Unicode map, Dungeon Crawl style.

Why this exists
---------------
eplan_live_read_page returns the truth about a page, but it returns it as a
flat list of placements with millimetre coordinates. Judging *layout* from
that list - is this device on the same axis as that one, does this corner
actually sit where the branch needs it, did something drift 2mm off-axis -
means holding ~17 coordinate pairs in your head at once and comparing them
pairwise. That is exactly the kind of thing a map makes trivial and a list
makes error-prone. Testing/04-interactive-vs-scripted-placement.md documents a
real bug (a thermal relay silently 14mm off position) that took a manual diff
of two JSON dumps to notice; on a map it would have been obvious at a glance.

The format is modelled on Dungeon Crawl Stone Soup's level-design files
(https://github.com/crawl/crawl, crawl-ref/source/dat/des/*.des): a fixed-pitch
grid of single-character glyphs, plus a KEY block giving each glyph's meaning.
That format was chosen for the same reason crawl chose it - a grid of glyphs is
diffable, greppable, and readable without a renderer, and the KEY block keeps
the glyphs themselves short. --format des emits an actual .des-shaped block.

What it does NOT do
-------------------
This is a *projection*, and a lossy one. Every cell covers several millimetres
(the header states exactly how many), so two placements can share a cell; when
they do, the collision is reported below the map rather than silently hidden.
Nothing here is a substitute for reading the coordinates when precision
matters, and nothing here proves electrical connectivity - see
Testing/02-routing-and-connections.md. Only eplan_live_read_connections does
that, and --connections overlays exactly that data when you pass it.

Usage
-----
    # from a saved eplan_live_read_page result
    python page_to_ascii.py fixtures/mando-1.json

    # from a pipe
    some-command | python page_to_ascii.py -

    # overlay real logical connections (eplan_live_read_connections output)
    python page_to_ascii.py page.json --connections conns.json

    # zoom into one region, pure ASCII, crawl .des shape
    python page_to_ascii.py page.json --region 280,60,340,280 --cols 60 \\
        --charset ascii --format des

Accepts any JSON that contains a live_read_page payload, at the top level or
nested - so a write tool's "page_after" block works without extracting it
first. Standard library only; no dependencies.
"""

import argparse
import json
import math
import sys

# --------------------------------------------------------------------------
# Placement taxonomy.
#
# These are CLR type names as reported by live_read_page's "clrType", not
# guesses. A corner and a T-node are both "SymbolReference" - what separates
# them is their pin DIRECTIONS, which is why STRUCTURAL types are glyphed from
# pin data rather than from the symbol name (see glyph_for_directions).
# --------------------------------------------------------------------------

STRUCTURAL_TYPES = {"SymbolReference"}
CDP_TYPES = {"ConnectionDefinitionPoint", "ConnectionDefinition"}
IP_TYPES = {"InterruptionPoint", "PotentialDefinitionPoint"}
BOX_TYPES = {"MacroBox", "LocationBox", "Shielding", "UnitBox"}
GRAPHIC_TYPES = {
    "PathText", "Text", "SpecialText", "PropertyPlacement", "PolyLine",
    "Rectangle", "Circle", "Arc", "Line", "Ellipse", "Image", "Hatching",
}

# Draw order. A higher number wins the cell.
P_BOX, P_GRAPHIC, P_CONN, P_CDP, P_STRUCT, P_IP, P_LABEL = 0, 1, 2, 3, 4, 5, 6

CHARSETS = {
    "unicode": {
        "h": "─", "v": "│",
        "LU": "┘", "RU": "└", "RD": "┌", "LD": "┐",
        "DLR": "┬", "ULR": "┴", "UDR": "├", "UDL": "┤",
        "UDLR": "┼",
        "box": "·", "cdp": "=", "ip": "»", "graphic": "░",
        "unknown": "?",
    },
    "ascii": {
        "h": "-", "v": "|",
        "LU": "+", "RU": "+", "RD": "+", "LD": "+",
        "DLR": "+", "ULR": "+", "UDR": "+", "UDL": "+",
        "UDLR": "+",
        "box": ".", "cdp": "=", "ip": ">", "graphic": ":",
        "unknown": "?",
    },
}

STRUCT_KEY_TEXT = {
    "h": "straight run, horizontal",
    "v": "straight run, vertical",
    "LU": "corner, legs Left+Up", "RU": "corner, legs Right+Up",
    "RD": "corner, legs Right+Down", "LD": "corner, legs Left+Down",
    "DLR": "T-node, branch Down", "ULR": "T-node, branch Up",
    "UDR": "T-node, branch Right", "UDL": "T-node, branch Left",
    "UDLR": "four-way crossing",
    "box": "box outline (MacroBox / LocationBox)",
    "cdp": "connection definition point (wire number / colour)",
    "ip": "interruption point (cross-page jump)",
    "graphic": "graphic (text, polyline, ...) - only with --graphics",
    "unknown": "symbol whose pin directions did not match a known shape",
}

GLYPH_POOL = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

# Cell sizes we are willing to snap to, in mm. Snapping matters: it is what
# makes every row/column label a round millimetre value instead of 288.6.
NICE_MM = [0.25, 0.5, 1, 2, 2.5, 4, 5, 8, 10, 12.5, 16, 20, 25, 40, 50, 100]

EPS = 1e-6


# --------------------------------------------------------------------------
# Input handling
# --------------------------------------------------------------------------

def load_json(path):
    if path == "-":
        return json.load(sys.stdin)
    with open(path, "r", encoding="utf-8") as fh:
        return json.load(fh)


def find_page_payload(obj, _depth=0):
    """
    Locate the live_read_page payload inside whatever was handed to us.

    Every schematic write tool echoes the same structure back under
    "page_after", and the MCP layer sometimes nests it under "results", so
    accepting only a bare top-level payload would mean the caller has to dig
    it out by hand every time.
    """
    if _depth > 6 or not isinstance(obj, dict):
        return None
    if isinstance(obj.get("placements"), list):
        return obj
    for key in ("page_after", "results", "result", "data"):
        hit = find_page_payload(obj.get(key), _depth + 1)
        if hit:
            return hit
    for value in obj.values():
        if isinstance(value, dict):
            hit = find_page_payload(value, _depth + 1)
            if hit:
                return hit
    return None


def point(d):
    if not isinstance(d, dict):
        return None
    x, y = d.get("x"), d.get("y")
    if x is None or y is None:
        return None
    return float(x), float(y)


def placement_point(pl):
    """A placement's anchor: its Location, else its bounding-box centre."""
    p = point(pl.get("location"))
    if p:
        return p
    bb = pl.get("boundingBox") or []
    pts = [point(b) for b in bb]
    pts = [p for p in pts if p]
    if not pts:
        return None
    return (sum(p[0] for p in pts) / len(pts),
            sum(p[1] for p in pts) / len(pts))


def bbox(pl):
    pts = [point(b) for b in (pl.get("boundingBox") or [])]
    pts = [p for p in pts if p]
    if len(pts) < 2:
        return None
    xs = [p[0] for p in pts]
    ys = [p[1] for p in pts]
    return min(xs), min(ys), max(xs), max(ys)


def classify(pl):
    t = pl.get("clrType") or ""
    if t in BOX_TYPES:
        return "box"
    if t in CDP_TYPES:
        return "cdp"
    if t in IP_TYPES:
        return "ip"
    if t in STRUCTURAL_TYPES:
        return "struct"
    if t in GRAPHIC_TYPES:
        return "graphic"
    return "label"


def glyph_for_directions(pins, chars):
    """
    Pick a box-drawing glyph from a routing symbol's own pin directions.

    This is deliberately derived rather than looked up by symbol name: the
    project's names (CO, TLRU, TOUR, ...) are install-specific - see
    Testing/02-routing-and-connections.md - but "which way do the legs point"
    is in the data for every project. A shape we do not recognise renders as
    '?' and is listed in the key, so an unhandled case is visible rather than
    quietly drawn as something else.
    """
    dirs = {(p.get("direction") or "")[:1].upper()
            for p in (pins or []) if p.get("direction")}
    dirs.discard("")
    key = "".join(c for c in "UDLR" if c in dirs)
    table = {
        "LU": "LU", "UL": "LU", "RU": "RU", "UR": "RU",
        "DR": "RD", "RD": "RD", "DL": "LD", "LD": "LD",
        "DLR": "DLR", "ULR": "ULR", "UDR": "UDR", "UDL": "UDL",
        "UDLR": "UDLR", "LR": "h", "UD": "v",
    }
    name = table.get(key)
    if name is None:
        # "UD"/"LR" orderings normalise above; anything else is genuinely new.
        canon = {"UL": "LU", "UR": "RU", "DL": "LD", "DR": "RD"}.get(key)
        name = canon or "unknown"
    return chars[name], name


# --------------------------------------------------------------------------
# Geometry: region and cell size
# --------------------------------------------------------------------------

def snap_cell(raw):
    for v in NICE_MM:
        if v >= raw - EPS:
            return float(v)
    return float(NICE_MM[-1])


def content_bounds(placements, include_graphics):
    xs, ys = [], []
    for pl in placements:
        if not include_graphics and classify(pl) == "graphic":
            continue
        b = bbox(pl)
        if b:
            xs += [b[0], b[2]]
            ys += [b[1], b[3]]
        else:
            p = placement_point(pl)
            if p:
                xs.append(p[0])
                ys.append(p[1])
    if not xs:
        return None
    return min(xs), min(ys), max(xs), max(ys)


def plan_grid(x0, y0, x1, y1, target_cols, aspect, cell_w, cell_h):
    """
    Choose cell size, then expand the region outward to whole cells.

    Expanding is what keeps every axis label a round number: if the region
    starts at 0 and each column is 5mm wide, every column boundary is a
    multiple of 5 by construction.
    """
    span_x = max(x1 - x0, EPS)
    if cell_w is None:
        cell_w = snap_cell(span_x / max(target_cols, 1))
    if cell_h is None:
        cell_h = snap_cell(cell_w * aspect)
    x0 = math.floor(x0 / cell_w) * cell_w
    x1 = math.ceil(x1 / cell_w) * cell_w
    y0 = math.floor(y0 / cell_h) * cell_h
    y1 = math.ceil(y1 / cell_h) * cell_h
    cols = max(1, int(round((x1 - x0) / cell_w)))
    rows = max(1, int(round((y1 - y0) / cell_h)))
    return x0, y0, x1, y1, cell_w, cell_h, cols, rows


def nice_tick(span, max_ticks):
    for step in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if span / step <= max_ticks:
            return step
    return 1000


# --------------------------------------------------------------------------
# The renderer
# --------------------------------------------------------------------------

class Grid(object):
    def __init__(self, rows, cols):
        self.rows, self.cols = rows, cols
        self.cell = [[(None, " ") for _ in range(cols)] for _ in range(rows)]

    def put(self, r, c, priority, ch):
        if not (0 <= r < self.rows and 0 <= c < self.cols):
            return False
        prev = self.cell[r][c][0]
        if prev is None or priority >= prev:
            self.cell[r][c] = (priority, ch)
            return True
        return False

    def free(self, r, c):
        if not (0 <= r < self.rows and 0 <= c < self.cols):
            return False
        return self.cell[r][c][0] is None

    def line(self, r):
        return "".join(ch for _, ch in self.cell[r]).rstrip()


def render(payload, connections=None, target_cols=100, aspect=2.0,
           charset="unicode", region=None, include_graphics=False,
           auto_fit=False, cell_w=None, cell_h=None, fmt="text",
           label_every=1):
    chars = CHARSETS[charset]
    placements = payload.get("placements") or []

    page_size = point(payload.get("size")) or (0.0, 0.0)
    if region:
        x0, y0, x1, y1 = region
    elif auto_fit:
        b = content_bounds(placements, include_graphics)
        if not b:
            b = (0.0, 0.0, page_size[0] or 100.0, page_size[1] or 100.0)
        pad = 4.0
        x0, y0, x1, y1 = b[0] - pad, b[1] - pad, b[2] + pad, b[3] + pad
    else:
        x0, y0 = 0.0, 0.0
        x1 = page_size[0] or 420.0
        y1 = page_size[1] or 297.0
        b = content_bounds(placements, include_graphics)
        if b:  # a terminal can sit exactly on, or a hair past, the page edge
            x0, y0 = min(x0, b[0]), min(y0, b[1])
            x1, y1 = max(x1, b[2]), max(y1, b[3])

    x0, y0, x1, y1, cell_w, cell_h, cols, rows = plan_grid(
        x0, y0, x1, y1, target_cols, aspect, cell_w, cell_h)

    def to_cell(x, y):
        c = int((x - x0) / cell_w)
        r = int((y1 - y) / cell_h)
        return min(max(r, 0), rows - 1), min(max(c, 0), cols - 1)

    grid = Grid(rows, cols)
    legend = []          # (glyph, placement, point) for labelled items
    # Everything that competes for a cell, so an overdrawn item can be
    # reported instead of just vanishing. Boxes and graphics are excluded on
    # purpose - they are background and are *expected* to be drawn over.
    contents = {}        # (r, c) -> [(priority, description), ...]
    used_struct = {}     # key name -> glyph, for the key block
    unknown_shapes = []
    off_region = 0

    def in_region(x, y):
        return x0 - EPS <= x <= x1 + EPS and y0 - EPS <= y <= y1 + EPS

    # --- 1. boxes (drawn first, lowest priority: they are background) -----
    for pl in placements:
        if classify(pl) != "box":
            continue
        b = bbox(pl)
        if not b:
            continue
        r_top, c_left = to_cell(b[0], b[3])
        r_bot, c_right = to_cell(b[2], b[1])
        for c in range(c_left, c_right + 1):
            grid.put(r_top, c, P_BOX, chars["box"])
            grid.put(r_bot, c, P_BOX, chars["box"])
        for r in range(r_top, r_bot + 1):
            grid.put(r, c_left, P_BOX, chars["box"])
            grid.put(r, c_right, P_BOX, chars["box"])
        used_struct["box"] = chars["box"]

    # --- 2. graphics (opt-in; a real page is mostly these) ----------------
    if include_graphics:
        for pl in placements:
            if classify(pl) != "graphic":
                continue
            p = placement_point(pl)
            if p and in_region(*p):
                r, c = to_cell(*p)
                grid.put(r, c, P_GRAPHIC, chars["graphic"])
                used_struct["graphic"] = chars["graphic"]

    # --- 3. logical connections, when supplied ----------------------------
    #
    # Only axis-aligned runs are drawn. A connection whose two endpoints share
    # neither x nor y was routed through corners, and a straight line between
    # its endpoints would draw a wire that does not exist. Those are listed
    # under the map instead - an undrawn connection is recoverable, an
    # invented one is not.
    #
    # Note what the endpoints actually are: live_read_connections reports each
    # end's DEVICE location, not the pin's. Measured live, Q2's connection end
    # reads (302, 106) while its two pins sit at (302, 110) and (302, 102). At
    # any sane cell size that is under one cell of error, but it does mean a
    # drawn run shows which devices are wired, not the exact millimetres the
    # wire occupies.
    drawn_conns, skipped_conns = 0, []
    for conn in (connections or []):
        a = point((conn.get("from") or {}).get("location"))
        b = point((conn.get("to") or {}).get("location"))
        label = "%s:%s -> %s:%s" % (
            (conn.get("from") or {}).get("device", "?"),
            (conn.get("from") or {}).get("designation", "?"),
            (conn.get("to") or {}).get("device", "?"),
            (conn.get("to") or {}).get("designation", "?"))
        if not a or not b:
            skipped_conns.append((label, "no endpoint coordinates"))
            continue
        if abs(a[0] - b[0]) < EPS:
            r1, c = to_cell(a[0], a[1])
            r2, _ = to_cell(b[0], b[1])
            for r in range(min(r1, r2), max(r1, r2) + 1):
                if grid.free(r, c):
                    grid.put(r, c, P_CONN, chars["v"])
            used_struct["v"] = chars["v"]
            drawn_conns += 1
        elif abs(a[1] - b[1]) < EPS:
            r, c1 = to_cell(a[0], a[1])
            _, c2 = to_cell(b[0], b[1])
            for c in range(min(c1, c2), max(c1, c2) + 1):
                if grid.free(r, c):
                    grid.put(r, c, P_CONN, chars["h"])
            used_struct["h"] = chars["h"]
            drawn_conns += 1
        else:
            skipped_conns.append((label, "not axis-aligned (routed via corners)"))

    # --- 4. connection definition points, routing symbols, jumps ----------
    for pl in placements:
        kind = classify(pl)
        if kind not in ("cdp", "struct", "ip"):
            continue
        p = placement_point(pl)
        if not p:
            continue
        if not in_region(*p):
            off_region += 1
            continue
        r, c = to_cell(*p)
        sym = pl.get("symbol") or {}
        sym_name = sym.get("name") or pl.get("clrType") or "?"
        if kind == "cdp":
            grid.put(r, c, P_CDP, chars["cdp"])
            used_struct["cdp"] = chars["cdp"]
            contents.setdefault((r, c), []).append(
                (P_CDP, "%s %s" % (chars["cdp"], sym_name)))
        elif kind == "ip":
            grid.put(r, c, P_IP, chars["ip"])
            used_struct["ip"] = chars["ip"]
            contents.setdefault((r, c), []).append(
                (P_IP, "%s %s" % (chars["ip"], pl.get("name") or sym_name)))
        else:
            ch, name = glyph_for_directions(pl.get("pins"), chars)
            grid.put(r, c, P_STRUCT, ch)
            used_struct[name] = ch
            contents.setdefault((r, c), []).append(
                (P_STRUCT, "%s %s v%s (%s)" % (
                    ch, sym_name, sym.get("variantNr", "?"),
                    STRUCT_KEY_TEXT.get(name, name))))
            if name == "unknown":
                unknown_shapes.append("%s/%s v%s at (%g, %g)" % (
                    sym.get("library", "?"), sym_name,
                    sym.get("variantNr", "?"), p[0], p[1]))

    # --- 5. labelled placements (devices, terminals): highest priority ----
    for pl in placements:
        if classify(pl) != "label":
            continue
        p = placement_point(pl)
        if not p:
            continue
        if not in_region(*p):
            off_region += 1
            continue
        r, c = to_cell(*p)
        idx = len(legend)
        glyph = GLYPH_POOL[idx] if idx < len(GLYPH_POOL) else "#"
        legend.append((glyph, pl, p))
        contents.setdefault((r, c), []).append(
            (P_LABEL, "%s %s" % (glyph, pl.get("name") or "(unnamed)")))
        grid.put(r, c, P_LABEL, glyph)

    # A cell can only show one glyph. Whichever won it is the last-placed of
    # the highest priority - matching Grid.put's own ">=" rule. Say what got
    # covered, rather than letting the map imply it is not on the page.
    collisions = []
    for (r, c), items in sorted(contents.items()):
        if len(items) < 2:
            continue
        top = max(p for p, _ in items)
        shown = [d for p, d in items if p == top][-1]
        hidden = [d for p, d in items if not (p == top and d == shown)]
        if not hidden:
            continue
        cx = x0 + (c + 0.5) * cell_w
        cy = y1 - (r + 0.5) * cell_h
        collisions.append("cell near (%s, %s): showing %s | hidden: %s" % (
            _fmt_mm(cx), _fmt_mm(cy), shown, "; ".join(hidden)))

    if fmt == "des":
        return _emit_des(payload, grid, legend, used_struct, chars,
                         x0, y0, x1, y1, cell_w, cell_h, cols, rows,
                         collisions, skipped_conns, drawn_conns,
                         unknown_shapes, off_region)
    return _emit_text(payload, grid, legend, used_struct, chars,
                      x0, y0, x1, y1, cell_w, cell_h, cols, rows,
                      collisions, skipped_conns, drawn_conns,
                      unknown_shapes, off_region, label_every)


# --------------------------------------------------------------------------
# Output formats
# --------------------------------------------------------------------------

def _fmt_mm(v):
    return ("%g" % round(v, 3))


def _header_lines(payload, x0, y0, x1, y1, cell_w, cell_h, cols, rows):
    size = point(payload.get("size"))
    out = ["%s   (%s)   project %s" % (
        payload.get("page", "?"), payload.get("pageType", "?"),
        payload.get("project", "?"))]
    bits = []
    if size:
        bits.append("page %s x %s mm" % (_fmt_mm(size[0]), _fmt_mm(size[1])))
    if payload.get("gridSize") is not None:
        bits.append("EPLAN grid %s mm" % _fmt_mm(payload["gridSize"]))
    bits.append("%d placements" % (payload.get("placementCount")
                                   or len(payload.get("placements") or [])))
    out.append("  ".join(bits))
    out.append("view x %s..%s  y %s..%s mm   %d cols x %d rows   "
               "1 col = %s mm, 1 row = %s mm   (+Y is up)" % (
                   _fmt_mm(x0), _fmt_mm(x1), _fmt_mm(y0), _fmt_mm(y1),
                   cols, rows, _fmt_mm(cell_w), _fmt_mm(cell_h)))
    if payload.get("truncated"):
        out.append("!! this read was TRUNCATED (returned %s of %s) - the map "
                   "is incomplete" % (payload.get("returned"),
                                      payload.get("placementCount")))
    return out


def _x_ruler(x0, cell_w, cols, gutter):
    """
    Millimetre ruler above the map.

    The tick step is chosen so that every tick's LABEL fits: a tick mark with
    no number over it is worse than a coarser ruler, because it invites
    counting ticks and getting the wrong millimetre.
    """
    span = cols * cell_w
    step = None
    for candidate in (1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if span / candidate > 12:
            continue
        values = []
        v = math.ceil(x0 / candidate) * candidate
        while v <= x0 + span + EPS:
            values.append(v)
            v += candidate
        widest = max((len(_fmt_mm(t)) for t in values), default=1)
        if candidate / cell_w >= widest + 1:
            step = candidate
            break
    if step is None:
        step = nice_tick(span, 6)

    nums = [" "] * (cols + 12)
    marks = [" "] * (cols + 1)
    v = math.ceil(x0 / step) * step
    while v <= x0 + span + EPS:
        col = int(round((v - x0) / cell_w))
        if 0 <= col <= cols:
            text = _fmt_mm(v)
            start = max(0, col - len(text) // 2)
            nums[start:start + len(text)] = list(text)
            marks[col] = "|"
        v += step
    pad = " " * gutter
    return [pad + "".join(nums).rstrip(), pad + "".join(marks).rstrip()]


def _legend_lines(legend, used_struct, chars):
    out = []
    if legend:
        out.append("")
        out.append("KEY - placements")
        width = max(len((pl.get("name") or "(unnamed)")) for _, pl, _ in legend)
        width = min(max(width, 8), 34)
        for glyph, pl, p in legend:
            sym = pl.get("symbol") or {}
            sym_txt = ("%s/%s v%s" % (sym.get("library"), sym.get("name"),
                                      sym.get("variantNr"))
                       if sym.get("name") else "-")
            out.append("  %s  %-*s  %-26s %-20s @ (%s, %s)" % (
                glyph, width, (pl.get("name") or "(unnamed)")[:width],
                pl.get("clrType", "?"), sym_txt, _fmt_mm(p[0]), _fmt_mm(p[1])))
    pairs = _struct_key_pairs(used_struct)
    if pairs:
        out.append("")
        out.append("KEY - structure")
        for glyph, text in pairs:
            out.append("  %s  %s" % (glyph, text))
    return out


def _struct_key_pairs(used_struct):
    """
    One line per GLYPH, not per shape.

    The ascii charset draws every corner and T-node as '+', so keying by shape
    name would print '+' four times with four different meanings - which is
    worse than useless. Shapes sharing a glyph are merged into one entry.
    """
    by_glyph = {}
    for name, glyph in used_struct.items():
        by_glyph.setdefault(glyph, []).append(STRUCT_KEY_TEXT.get(name, name))
    return [(g, " / ".join(sorted(v))) for g, v in sorted(by_glyph.items())]


def _notes_lines(collisions, skipped_conns, drawn_conns, unknown_shapes,
                 off_region):
    out = []
    if drawn_conns or skipped_conns:
        out.append("")
        out.append("CONNECTIONS: %d drawn as straight runs, %d not drawn "
                   "(endpoints are DEVICE locations, not pin locations)"
                   % (drawn_conns, len(skipped_conns)))
        for label, why in skipped_conns:
            out.append("  - %s  (%s)" % (label, why))
    if collisions:
        out.append("")
        out.append("OVERDRAWN: %d cell(s) hold more than one placement, so "
                   "something on the page is not visible on the map."
                   % len(collisions))
        out.append("  Re-run with a larger --cols, or --region to zoom in, to "
                   "separate them.")
        for c in collisions:
            out.append("  - %s" % c)
    if unknown_shapes:
        out.append("")
        out.append("UNRECOGNISED ROUTING SHAPES (drawn as '?'):")
        for s in unknown_shapes:
            out.append("  - %s" % s)
    if off_region:
        out.append("")
        out.append("NOTE: %d placement(s) fall outside the rendered region."
                   % off_region)
    return out


def _emit_text(payload, grid, legend, used_struct, chars, x0, y0, x1, y1,
               cell_w, cell_h, cols, rows, collisions, skipped_conns,
               drawn_conns, unknown_shapes, off_region, label_every):
    labels = [_fmt_mm(y1 - r * cell_h) for r in range(rows)]
    lw = max(len(s) for s in labels) if labels else 3
    gutter = lw + 2

    out = _header_lines(payload, x0, y0, x1, y1, cell_w, cell_h, cols, rows)
    out.append("")
    out += _x_ruler(x0, cell_w, cols, gutter)
    for r in range(rows):
        tag = labels[r] if (label_every <= 1 or r % label_every == 0) else ""
        out.append("%*s |%s" % (lw, tag, grid.line(r)))
    out.append("%*s |" % (lw, _fmt_mm(y0)))
    out.append("row label = the UPPER edge of that row's %s mm band"
               % _fmt_mm(cell_h))
    out += _legend_lines(legend, used_struct, chars)
    out += _notes_lines(collisions, skipped_conns, drawn_conns,
                        unknown_shapes, off_region)
    return "\n".join(out)


def _emit_des(payload, grid, legend, used_struct, chars, x0, y0, x1, y1,
              cell_w, cell_h, cols, rows, collisions, skipped_conns,
              drawn_conns, unknown_shapes, off_region):
    """Crawl .des shape: NAME / MAP..ENDMAP / KEY lines, comments with ':'."""
    name = (payload.get("page") or "page").lower()
    for ch in "+/\\ .":
        name = name.replace(ch, "_")
    out = ["NAME:   eplan_%s" % name.strip("_")]
    for line in _header_lines(payload, x0, y0, x1, y1, cell_w, cell_h,
                              cols, rows):
        out.append(": %s" % line)
    out.append("MAP")
    for r in range(rows):
        out.append(grid.line(r))
    out.append("ENDMAP")
    for glyph, pl, p in legend:
        sym = pl.get("symbol") or {}
        out.append("KEY:    %s = %s | %s | %s/%s v%s | %s,%s" % (
            glyph, pl.get("name") or "(unnamed)", pl.get("clrType", "?"),
            sym.get("library", "-"), sym.get("name", "-"),
            sym.get("variantNr", "-"), _fmt_mm(p[0]), _fmt_mm(p[1])))
    for glyph, text in _struct_key_pairs(used_struct):
        out.append("KEY:    %s = %s" % (glyph, text))
    for line in _notes_lines(collisions, skipped_conns, drawn_conns,
                             unknown_shapes, off_region):
        out.append((": %s" % line) if line else ":")
    return "\n".join(out)


# --------------------------------------------------------------------------

def parse_region(text):
    parts = [p.strip() for p in text.split(",")]
    if len(parts) != 4:
        raise argparse.ArgumentTypeError("--region wants x0,y0,x1,y1 in mm")
    try:
        x0, y0, x1, y1 = (float(p) for p in parts)
    except ValueError:
        raise argparse.ArgumentTypeError("--region values must be numbers")
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("page_json",
                    help="File holding an eplan_live_read_page result, or '-' "
                         "for stdin. A nested 'page_after' block is found "
                         "automatically.")
    ap.add_argument("--connections", metavar="FILE",
                    help="eplan_live_read_connections output. Only "
                         "axis-aligned runs are drawn; anything routed through "
                         "corners is listed rather than guessed at.")
    ap.add_argument("--cols", type=int, default=100,
                    help="Target map width in characters (default 100). The "
                         "real width is derived from the snapped cell size and "
                         "is stated in the header.")
    ap.add_argument("--aspect", type=float, default=2.0,
                    help="mm per row / mm per col (default 2.0, which matches "
                         "a terminal character's own ~1:2 shape so the drawing "
                         "keeps its proportions)")
    ap.add_argument("--cell-w", type=float, help="Force mm per column")
    ap.add_argument("--cell-h", type=float, help="Force mm per row")
    ap.add_argument("--region", type=parse_region, metavar="X0,Y0,X1,Y1",
                    help="Render only this mm rectangle")
    ap.add_argument("--fit", action="store_true",
                    help="Crop to the content's bounding box instead of the "
                         "whole page")
    ap.add_argument("--graphics", action="store_true",
                    help="Also plot texts/polylines. Off by default: a "
                         "production page is mostly graphics and they bury the "
                         "devices.")
    ap.add_argument("--charset", choices=sorted(CHARSETS), default="unicode",
                    help="unicode box-drawing (default) or plain ascii")
    ap.add_argument("--format", dest="fmt", choices=("text", "des"),
                    default="text",
                    help="text = rulers + key blocks (default); des = crawl "
                         "level-file shape (MAP/ENDMAP/KEY)")
    ap.add_argument("--y-every", type=int, default=1, metavar="N",
                    help="Label every Nth row (default 1 = every row)")
    args = ap.parse_args()

    raw = load_json(args.page_json)
    payload = find_page_payload(raw)
    if payload is None:
        sys.exit("No live_read_page payload found in %s (no 'placements' list)."
                 % args.page_json)

    conns = None
    if args.connections:
        craw = load_json(args.connections)
        conns = craw.get("connections") if isinstance(craw, dict) else None
        if conns is None:
            found = find_page_payload(craw)
            conns = (found or {}).get("connections") or []

    text = render(payload, connections=conns, target_cols=args.cols,
                  aspect=args.aspect, charset=args.charset, region=args.region,
                  include_graphics=args.graphics, auto_fit=args.fit,
                  cell_w=args.cell_w, cell_h=args.cell_h, fmt=args.fmt,
                  label_every=args.y_every)

    # Windows consoles default to a codepage that cannot encode box-drawing
    # glyphs; without this the unicode charset dies on a UnicodeEncodeError.
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    print(text)


if __name__ == "__main__":
    main()
