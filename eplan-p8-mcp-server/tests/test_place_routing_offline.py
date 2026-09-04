"""
Placing connection symbols: corners and T-nodes.

Three measured facts drive the design here.

1. A routing symbol is NOT a Function. `Function.Create` refuses one with
   `S511085Cannot create function`, which names no cause. The path that works is
   `SymbolVariant.Create(Page)`, returning a `SymbolReference`. So the two kinds
   of symbol need two tools, and each refuses the other's input by name.

2. `SymbolVariant.Create` takes no coordinate - the object is born at the page
   ORIGIN and must be moved. Leaving one there is the failure mode being
   guarded against, so the script verifies the move landed.

3. Variants of one symbol facing the same directions are NOT interchangeable.
   Measured on `SPECIAL_en_US/TLRU`, whose five `Down+Left+Right` variants put
   the pins in different PLACES: v8 has all three at the vertex, v0 pushes its
   Right pin one grid step out. So an unresolved variant is a refusal carrying
   the real geometry, not a silent pick of the lowest number.

Runs with EPLAN closed.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MCP = os.path.join(os.path.dirname(HERE), "mcp_server")
for p in (MCP, os.path.join(MCP, "api")):
    if p not in sys.path:
        sys.path.insert(0, p)

from api.actions import schematic as S  # noqa: E402


# One corner, one T-node type with two rival symbols, shaped like a real read.
CORNER = {
    "library": "SPECIAL_en_US", "symbol": "CO", "type": "Routing",
    "variants": [
        {"variantNr": 0, "directions": ["Right", "Down"],
         "pins": [{"direction": "Right", "offset": {"x": 0.0, "y": 0.0}},
                  {"direction": "Down", "offset": {"x": 0.0, "y": 0.0}}]},
        {"variantNr": 1, "directions": ["Right", "Up"],
         "pins": [{"direction": "Right", "offset": {"x": 0.0, "y": 0.0}},
                  {"direction": "Up", "offset": {"x": 0.0, "y": 0.0}}]},
    ],
}
TLRU = {
    "library": "SPECIAL_en_US", "symbol": "TLRU", "type": "TNodeDown",
    "variants": [
        {"variantNr": 0, "directions": ["Down", "Left", "Right"],
         "pins": [{"direction": "Down", "offset": {"x": 0.0, "y": 0.0}},
                  {"direction": "Left", "offset": {"x": 0.0, "y": 0.0}},
                  {"direction": "Right", "offset": {"x": 3.175, "y": 0.0}}]},
        {"variantNr": 8, "directions": ["Right", "Left", "Down"],
         "pins": [{"direction": "Right", "offset": {"x": 0.0, "y": 0.0}},
                  {"direction": "Left", "offset": {"x": 0.0, "y": 0.0}},
                  {"direction": "Down", "offset": {"x": 0.0, "y": 0.0}}]},
    ],
}
_UP_VARIANTS = [
    {"variantNr": 8, "directions": ["Right", "Left", "Up"],
     "pins": [{"direction": "Right", "offset": {"x": 0.0, "y": 0.0}},
              {"direction": "Left", "offset": {"x": 0.0, "y": 0.0}},
              {"direction": "Up", "offset": {"x": 0.0, "y": 0.0}}]},
]
TLRO = {"library": "SPECIAL_en_US", "symbol": "TLRO", "type": "TNodeUp",
        "variants": _UP_VARIANTS}
TLRO_1 = {"library": "SPECIAL_en_US", "symbol": "TLRO_1", "type": "TNodeUp",
          "variants": _UP_VARIANTS}


@pytest.fixture
def eplan(monkeypatch):
    """A fake EPLAN: catalog reads answer from `state`, writes are recorded."""
    state = {"symbols": [], "scripts": [], "placed": []}

    def fake(script, timeout=30.0):
        state["scripts"].append(script)
        if "wantTypes" in script:                       # a catalog read
            return {"success": True, "results": {
                "success": True, "libraries": ["SPECIAL_en_US"],
                "symbols": [dict(s) for s in state["symbols"]]}}
        state["placed"].append(script)                   # a placement
        return {"success": True, "results": {
            "success": True, "page": "+P/1", "handle": "h1",
            "symbolType": "Routing",
            "placed": {"location": {"x": 10.0, "y": 20.0},
                       "boundingBox": [{"x": 9.0, "y": 19.0},
                                       {"x": 11.0, "y": 21.0}],
                       "pins": [{"index": 0, "direction": "Right",
                                 "raw": {"x": 0.0, "y": 0.0}}]}}}

    monkeypatch.setattr(S, "_execute_script", fake)
    return state


# ---------------------------------------------------------------------------
# The two placement paths stay apart
# ---------------------------------------------------------------------------

def test_the_device_placer_names_the_real_problem_with_a_routing_symbol(eplan):
    """
    "S511085Cannot create function" says nothing a caller can act on. The
    preflight turns it into the tool they should have called.
    """
    eplan["symbols"] = [CORNER]
    S.live_place_symbol("+P/1", "SPECIAL_en_US", "CO", 10.0, 20.0)
    cs = eplan["scripts"][-1]
    assert "ROUTING_TYPES" in cs
    assert "live_place_connection_symbol" in cs


def test_the_connection_placer_refuses_a_device(eplan):
    S.live_place_connection_symbol("+P/1", "LIB", "SL", 10.0, 20.0)
    cs = eplan["scripts"][-1]
    assert "a device, not a" in cs
    assert "live_place_symbol" in cs


def test_the_connection_placer_uses_symbolvariant_create_not_function_create(eplan):
    S.live_place_connection_symbol("+P/1", "LIB", "CO", 10.0, 20.0)
    cs = eplan["scripts"][-1]
    assert 'MethodByShape(varType, "Create", new string[] { "Page" }' in cs
    assert "Eplan.EplApi.DataModel.Function" not in cs


# ---------------------------------------------------------------------------
# The page origin
# ---------------------------------------------------------------------------

def test_a_symbol_born_at_the_origin_is_moved_and_the_move_is_verified(eplan):
    """
    Create(Page) takes no coordinate. An unmoved connection symbol sits on the
    page frame and autoconnects to whatever else is near (0,0), so the script
    checks that it actually landed.
    """
    S.live_place_connection_symbol("+P/1", "LIB", "CO", 10.0, 20.0)
    cs = eplan["scripts"][-1]
    assert 'GetWritable(sref.GetType(), "Location")' in cs
    move = cs.index("locProp.SetValue(sref")
    check = cs.index("The symbol did not move")
    assert move < check
    assert "still near the page origin" in cs


def test_an_unmovable_symbol_is_a_hard_error(eplan):
    S.live_place_connection_symbol("+P/1", "LIB", "CO", 10.0, 20.0)
    assert "Refusing to leave it there" in eplan["scripts"][-1]


def test_the_placement_is_scratch_guarded(eplan):
    S.live_place_connection_symbol("+P/1", "LIB", "CO", 10.0, 20.0)
    assert "GuardScratch(project," in eplan["scripts"][-1]


def test_writing_to_a_real_project_needs_the_flag(eplan):
    S.live_place_connection_symbol("+P/1", "LIB", "CO", 10.0, 20.0,
                                   allow_real_project=True)
    assert "GuardScratch(project, true," in eplan["scripts"][-1]


# ---------------------------------------------------------------------------
# Corners
# ---------------------------------------------------------------------------

def test_a_corner_is_looked_up_not_hardcoded(eplan):
    eplan["symbols"] = [CORNER]
    out = S.live_place_corner("+P/1", 10.0, 20.0, ["Right", "Down"])
    assert out["success"]
    assert out["chosen"]["symbol"] == "CO"
    assert out["chosen"]["variantNr"] == 0


def test_the_variant_is_derived_from_the_directions(eplan):
    eplan["symbols"] = [CORNER]
    assert S.live_place_corner("+P/1", 10.0, 20.0,
                               ["Right", "Up"])["chosen"]["variantNr"] == 1


def test_corner_direction_order_is_irrelevant(eplan):
    eplan["symbols"] = [CORNER]
    a = S.live_place_corner("+P/1", 10.0, 20.0, ["Right", "Down"])
    b = S.live_place_corner("+P/1", 10.0, 20.0, ["Down", "Right"])
    assert a["chosen"]["variantNr"] == b["chosen"]["variantNr"]


def test_a_corner_needs_exactly_two_directions(eplan):
    eplan["symbols"] = [CORNER]
    out = S.live_place_corner("+P/1", 10.0, 20.0, ["Right"])
    assert out["success"] is False
    assert "live_place_tnode" in out["error"]
    assert not eplan["scripts"]


def test_two_identical_directions_are_refused_as_a_straight_run(eplan):
    """A straight run needs no symbol at all - EPLAN autoconnects it."""
    eplan["symbols"] = [CORNER]
    out = S.live_place_corner("+P/1", 10.0, 20.0, ["Right", "Right"])
    assert out["success"] is False
    assert "autoconnects" in out["error"]
    assert not eplan["scripts"]


def test_a_project_with_no_matching_corner_says_so(eplan):
    eplan["symbols"] = [CORNER]
    out = S.live_place_corner("+P/1", 10.0, 20.0, ["Left", "Down"])
    assert out["success"] is False
    assert "live_routing_catalog" in out["error"]


# ---------------------------------------------------------------------------
# T-nodes
# ---------------------------------------------------------------------------

def test_a_tnode_is_chosen_by_TYPE_because_direction_is_not_a_variant(eplan):
    """
    The asymmetry with corners is EPLAN's: TNodeUp and TNodeDown are separate
    Symbol.Types, where a corner's four rotations are variants of one symbol.
    """
    assert S.TNODE_TYPE_BY_DIRECTION["Up"] == "TNodeUp"
    assert S.TNODE_TYPE_BY_DIRECTION["Down"] == "TNodeDown"
    eplan["symbols"] = [TLRU]
    S.live_place_tnode("+P/1", 10.0, 20.0, "Down", variant_nr=8)
    assert '"TNodeDown"' in eplan["scripts"][0]


def test_the_branch_direction_implies_the_other_two_legs(eplan):
    eplan["symbols"] = [TLRU]
    out = S.live_place_tnode("+P/1", 10.0, 20.0, "Down", variant_nr=8)
    assert sorted(out["chosen"]["directions"]) == ["Down", "Left", "Right"]


def test_a_bad_branch_direction_is_refused(eplan):
    out = S.live_place_tnode("+P/1", 10.0, 20.0, "Sideways")
    assert out["success"] is False
    assert not eplan["scripts"]


def test_two_rival_symbols_of_one_type_are_never_chosen_between(eplan):
    """TLRO and TLRO_1 are both TNodeUp. Picking one would be a coin flip."""
    eplan["symbols"] = [TLRO, TLRO_1]
    out = S.live_place_tnode("+P/1", 10.0, 20.0, "Up")
    assert out["success"] is False
    assert out["ambiguous"] is True
    assert {c["symbol"] for c in out["candidates"]} == {"TLRO", "TLRO_1"}
    assert not eplan["placed"], "it wrote despite being unable to choose"


def test_naming_the_symbol_resolves_a_rivalry(eplan):
    eplan["symbols"] = [TLRO, TLRO_1]
    out = S.live_place_tnode("+P/1", 10.0, 20.0, "Up",
                             symbol="TLRO_1", variant_nr=8)
    assert out["success"]
    assert out["chosen"]["symbol"] == "TLRO_1"


# ---------------------------------------------------------------------------
# Variants of one symbol are not interchangeable
# ---------------------------------------------------------------------------

def test_rival_variants_are_refused_with_their_real_geometry(eplan):
    """
    The refusal has to carry the pin OFFSETS. Saying only "5 variants match"
    leaves the caller no way to choose, and an earlier version of this message
    claimed they differed in pin ORDER - which is false for TLRU, whose pins
    sit in different PLACES.
    """
    eplan["symbols"] = [TLRU]
    out = S.live_place_tnode("+P/1", 10.0, 20.0, "Down")
    assert out["success"] is False
    assert out["ambiguous"] is True
    assert "NOT" in out["error"] and "different places" in out["error"]
    assert "+3.17" in out["error"], "the offset that distinguishes v0 is missing"
    assert "ORDER" not in out["error"]
    assert not eplan["placed"]


def test_an_explicit_variant_resolves_the_rivalry(eplan):
    eplan["symbols"] = [TLRU]
    out = S.live_place_tnode("+P/1", 10.0, 20.0, "Down", variant_nr=8)
    assert out["success"]
    assert out["chosen"]["variantNr"] == 8


def test_a_variant_that_does_not_face_the_right_way_is_refused(eplan):
    eplan["symbols"] = [TLRU]
    out = S.live_place_tnode("+P/1", 10.0, 20.0, "Down", variant_nr=5)
    assert out["success"] is False
    assert "does not face" in out["error"]
    assert not eplan["placed"]


def test_the_reason_for_the_choice_is_reported(eplan):
    eplan["symbols"] = [CORNER]
    out = S.live_place_corner("+P/1", 10.0, 20.0, ["Right", "Down"])
    assert "only Routing symbol" in out["chosen"]["why"]


# ---------------------------------------------------------------------------
# The usual injection and validation boundary
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", ["{{RESULT_PATH}}", None, 5])
def test_a_hostile_page_never_reaches_a_script(bad, eplan):
    out = S.live_place_connection_symbol(bad, "LIB", "CO", 1.0, 2.0)
    assert out["success"] is False
    assert not eplan["scripts"]


def test_a_page_named_after_a_token_survives(eplan):
    """SNAP, VARNR and XVAL are token names; a page called one must not corrupt."""
    S.live_place_connection_symbol("+SNAP/1", "LIB", "CO", 1.0, 2.0)
    assert '"+SNAP/1"' in eplan["scripts"][-1]


def test_a_negative_variant_is_refused(eplan):
    out = S.live_place_connection_symbol("+P/1", "LIB", "CO", 1.0, 2.0,
                                         variant_nr=-1)
    assert out["success"] is False
    assert not eplan["scripts"]


def test_snapping_is_on_by_default_and_can_be_turned_off(eplan):
    S.live_place_connection_symbol("+P/1", "LIB", "CO", 1.0, 2.0)
    assert "bool SNAP" not in eplan["scripts"][-1]
    assert "if (true && grid > 0.0001)" in eplan["scripts"][-1]
    S.live_place_connection_symbol("+P/1", "LIB", "CO", 1.0, 2.0,
                                   snap_to_grid=False)
    assert "if (false && grid > 0.0001)" in eplan["scripts"][-1]


def test_a_successful_placement_offers_its_undo(eplan):
    out = S.live_place_connection_symbol("+P/1", "LIB", "CO", 1.0, 2.0)
    assert out["undo"]["tool"] == "eplan_live_remove_placement"
    assert out["undo"]["handle"] == "h1"


def test_a_failed_catalog_read_is_not_dressed_up_as_a_placement(monkeypatch):
    monkeypatch.setattr(S, "_execute_script", lambda script, timeout=30.0: {
        "success": False, "message": "no project open"})
    assert S.live_place_corner("+P/1", 1.0, 2.0, ["Right", "Down"])["success"] is False
