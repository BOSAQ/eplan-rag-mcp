"""
live_split_terminal_levels, live_remove_placements and live_place_cable,
asserted on their GENERATED C# with EPLAN closed.

These three rework an existing schematic (split a double-deck terminal, clear a
plug, redraw a cable), so the tests pin the guards they share with the other
real-project writers - expected project, scratch guard, all-or-nothing refusal
before any write - plus what each one must write.
"""

import os
import re
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MCP = os.path.join(os.path.dirname(HERE), "mcp_server")
for p in (MCP, os.path.join(MCP, "api")):
    if p not in sys.path:
        sys.path.insert(0, p)

from api.actions import schematic as S  # noqa: E402

ELK = r"C:\Projects\BOSAQ\ElectroClean\ElectroClean MCP V0.1.elk"
INJECTION = '"; System.Environment.Exit(0); string y = "'


@pytest.fixture
def capture(monkeypatch):
    seen = {}

    def fake(script, timeout=30.0):
        seen["cs"] = script
        seen["timeout"] = timeout
        return {"success": True, "results": {"success": True}}

    monkeypatch.setattr(S, "_execute_script", fake)
    return seen


def _balanced(cs):
    if cs.count("{") != cs.count("}"):
        return False
    stripped = cs.replace("\\\\", "").replace('\\"', "")
    return all(line.count('"') % 2 == 0 for line in stripped.splitlines())


CALLS = {
    "devices": lambda **kw: S.live_place_devices("=MCP/31", [{"x": 132, "y": 96, "tag": "=EC+LPA-X1:1"},
                                                             {"x": 140, "y": 96, "tag": "=EC+LPA-X1:2"}],
                                                 ELK, symbol="X4_B", **kw),
    "split": lambda **kw: S.live_split_terminal_levels(["=MCP+-X31:2", "=MCP+-X31:4"], ELK, **kw),
    "remove": lambda **kw: S.live_remove_placements("=MCP/31", ["798/17/35375/0", "798/17/35427/0"], ELK,
                                                    expect_types=["Plug", "PlugStrip"], **kw),
    "cable": lambda **kw: S.live_place_cable("=MCP/31", 128, 148, 172, 148, "=MCP+-W31.1", ELK,
                                             part_nr="LAPP.00101023",
                                             conductors=["BN", "BK", "GY", "BU", "GNYE"], **kw),
}


@pytest.mark.parametrize("name", sorted(CALLS))
def test_well_formed_and_guarded(capture, name):
    CALLS[name]()
    cs = capture["cs"]
    assert _balanced(cs)
    assert "{{RESULT_PATH}}" in cs
    assert "GuardScratch(project, false," in cs
    assert "REFUSING: the current project is" in cs
    assert r"C:\\Projects\\BOSAQ\\ElectroClean\\ElectroClean MCP V0.1.elk" in cs
    for line in cs.splitlines():
        if line.strip().startswith("using "):
            assert "Eplan.EplApi.DataModel" not in line


@pytest.mark.parametrize("name", sorted(CALLS))
def test_class_names_unique(capture, name):
    CALLS[name]()
    a = re.search(r"public class (\w+)", capture["cs"]).group(1)
    CALLS[name]()
    b = re.search(r"public class (\w+)", capture["cs"]).group(1)
    assert a != b


# --- split_terminal_levels ----------------------------------------------------

def test_split_is_dry_run_by_default_and_refuses_before_writing(capture):
    S.live_split_terminal_levels("=MCP+-X31:2", ELK)
    cs = capture["cs"]
    assert 'results["dry_run"] = true;' in cs
    assert "if (!true)" in cs
    assert cs.index("REFUSING, nothing changed") < cs.index('SetProp(props, "FUNC_TERMINALLEVEL", 1)')


def test_split_sets_main_level_and_a_new_position(capture):
    S.live_split_terminal_levels("=MCP+-X31:2", ELK, dry_run=False)
    cs = capture["cs"]
    assert '"IsMainTerminal"' in cs
    assert 'SetProp(props, "FUNC_TERMINALDEVICEPOSITION", (int)e["newDevicePosition"])' in cs
    assert "already a main terminal" in cs
    assert 'fn.GetType().Name != "Terminal"' in cs


@pytest.mark.parametrize("bad", [[], None, 5, ["X31"], ["=A+-X1:2", "=A+-X1:2"]])
def test_split_refuses_bad_input(capture, bad):
    assert S.live_split_terminal_levels(bad, ELK)["success"] is False
    assert "cs" not in capture


# --- remove_placements ----------------------------------------------------------

def test_remove_is_dry_run_by_default_and_type_checked(capture):
    CALLS["remove"]()
    cs = capture["cs"]
    assert "if (!true)" in cs
    assert 'string[] types = new string[] { "Plug", "PlugStrip" };' in cs
    assert cs.index("REFUSING, nothing removed") < cs.index('"Remove"')


@pytest.mark.parametrize("bad", [[], None, ["abc"], ["1/2; x"], ["1/2/3/0", "1/2/3/0"]])
def test_remove_refuses_bad_handles(capture, bad):
    assert S.live_remove_placements("=MCP/31", bad, ELK)["success"] is False
    assert "cs" not in capture


def test_remove_refuses_bad_type_names(capture):
    r = S.live_remove_placements("=MCP/31", ["1/2/3/0"], ELK, expect_types=["Plug;"])
    assert r["success"] is False


# --- place_cable --------------------------------------------------------------------

def test_cable_uses_cable_create_and_cabdl(capture):
    CALLS["cable"]()
    cs = capture["cs"]
    assert 'FindType("Eplan.EplApi.DataModel.EObjects.Cable")' in cs
    assert '"CABDL"' in cs and '"SPECIAL"' in cs
    assert 'string partNr = "LAPP.00101023";' in cs
    assert 'string[] cond = new string[] { "BN", "BK", "GY", "BU", "GNYE" };' in cs
    assert 'SetProp(props, "CONNECTION_WIRENUMBER", cond[i])' in cs


def test_cable_only_numbers_when_the_count_matches(capture):
    CALLS["cable"]()
    cs = capture["cs"]
    assert "cond.Length > 0 && cond.Length != pts.Count" in cs
    assert "nothing was numbered" in cs


def test_cable_returns_an_undo(capture):
    CALLS["cable"]()
    assert 'undo["tool"] = "live_remove_placements";' in capture["cs"]


@pytest.mark.parametrize("coords", [(0, 0, 10, 10), (5, 5, 5, 5), (float("nan"), 0, 10, 0)])
def test_cable_refuses_bad_geometry(capture, coords):
    r = S.live_place_cable("=MCP/31", *coords, "=A+-W1", ELK)
    assert r["success"] is False
    assert "cs" not in capture


@pytest.mark.parametrize("bad", ["BN", [""], 5])
def test_cable_refuses_bad_conductors(capture, bad):
    r = S.live_place_cable("=MCP/31", 0, 10, 50, 10, "=A+-W1", ELK, conductors=bad)
    assert r["success"] is False


def test_cable_injection_stays_in_literal(capture):
    S.live_place_cable("=MCP/31", 0, 10, 50, 10, INJECTION, ELK, conductors=[INJECTION])
    cs = capture["cs"]
    assert _balanced(cs)
    assert '"' + INJECTION not in cs


# --- place_devices ------------------------------------------------------------------

def test_devices_refuse_existing_tags_before_placing(capture):
    CALLS["devices"]()
    cs = capture["cs"]
    assert cs.index("REFUSING, nothing placed") < cs.index("Call(create, fn")
    assert 'string[] tags = new string[] { "=EC+LPA-X1:1", "=EC+LPA-X1:2" };' in cs
    assert 'string[] syms = new string[] { "X4_B", "X4_B" };' in cs


@pytest.mark.parametrize("bad", [[], None, [{"x": 1, "y": 2}], [{"x": 1, "y": 2, "tag": "A"}, {"x": 3, "y": 4, "tag": "A"}],
                                 [{"x": "a", "y": 2, "tag": "A"}]])
def test_devices_refuse_bad_items(capture, bad):
    assert S.live_place_devices("=MCP/31", bad, ELK, symbol="X4_B")["success"] is False
    assert "cs" not in capture


def test_devices_need_a_symbol(capture):
    assert S.live_place_devices("=MCP/31", [{"x": 1, "y": 2, "tag": "A"}], ELK)["success"] is False


def test_devices_can_create_terminals(capture):
    S.live_place_devices("=MCP/31", [{"x": 1, "y": 2, "tag": "=A+-X1:1"}], ELK, symbol="X4_B",
                         object_type="Terminal")
    assert 'FindType("Eplan.EplApi.DataModel.EObjects.Terminal")' in capture["cs"]


def test_devices_default_to_function_and_refuse_other_types(capture):
    S.live_place_devices("=MCP/31", [{"x": 1, "y": 2, "tag": "=A+-K1"}], ELK, symbol="K")
    assert 'FindType("Eplan.EplApi.DataModel.Function")' in capture["cs"]
    r = S.live_place_devices("=MCP/31", [{"x": 1, "y": 2, "tag": "=A+-K2"}], ELK, symbol="K",
                             object_type="Cable")
    assert r["success"] is False


def test_terminals_never_try_the_four_argument_create(capture):
    """A failed 4-arg Create leaves an anonymous terminal at (0,0) - found live."""
    S.live_place_devices("=MCP/31", [{"x": 1, "y": 2, "tag": "=A+-X1:1"}], ELK, symbol="X2_NB",
                         object_type="Terminal")
    cs = capture["cs"]
    assert "if (true)" in cs
    assert "static void RemoveQuietly(object o)" in cs
    assert "catch { RemoveQuietly(fn); throw; }" in cs


def test_functions_use_the_four_argument_create_and_clean_up(capture):
    S.live_place_devices("=MCP/31", [{"x": 1, "y": 2, "tag": "=A+-K1"}], ELK, symbol="K")
    cs = capture["cs"]
    assert "if (false)" in cs
    assert "catch { RemoveQuietly(fn); throw; }" in cs


def test_cable_tags_every_conductor_point(capture):
    """New conductor points are anonymous ('=+') until tagged - found live."""
    CALLS["cable"]()
    cs = capture["cs"]
    assert "pn.SetValue(p, \"=MCP+-W31.1\", null);" in cs
