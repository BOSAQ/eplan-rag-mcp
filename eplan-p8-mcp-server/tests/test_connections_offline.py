"""
live_read_connections, and the connection-line geometry bug it exposed.

Two things are pinned here:

  1. The reader NEVER writes. A "read" tool that quietly mutates is exactly the
     surprise this layer exists to avoid, and generating connections modifies
     the project.

  2. A connection line is anchored, then drawn RELATIVE to that anchor.
     `SetGraphics` does not take absolute page coordinates - measured against
     real human-drawn lines, whose Location is the absolute anchor and whose
     graphics and connection points are relative to it. Passing absolute
     coordinates put one end of every wire at the PAGE ORIGIN: a line that
     visibly exists, reports success, and connects nothing.

Runs with EPLAN closed.
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


@pytest.fixture
def capture(monkeypatch):
    seen = {"scripts": []}

    def fake(script, timeout=30.0):
        seen["scripts"].append(script)
        seen["cs"] = script
        return {"success": True, "results": {"success": True, "total": 1,
                                             "matched": 1, "connections": []}}

    monkeypatch.setattr(S, "_execute_script", fake)
    return seen


# ---------------------------------------------------------------------------
# The reader must never write
# ---------------------------------------------------------------------------

def test_read_connections_carries_no_write_guard(capture):
    """A read needs no scratch guard, because it cannot damage anything."""
    S.live_read_connections()
    assert "GuardScratch(project," not in capture["cs"]


def test_read_connections_generates_no_mutating_call(capture):
    S.live_read_connections()
    run_body = capture["cs"].split("[Start]")[1]
    for mutator in ("SetGraphics", ".Remove(", "Create(page", "SetValue("):
        assert mutator not in run_body, (
            "the connection reader emits %s - a read tool must not write" % mutator
        )


def test_read_connections_does_not_generate_connections(capture):
    """
    Generating connections MUTATES the project. The tool names it as a next
    step rather than running it.

    Looks for an EXECUTED action, not for the word: the script's own error text
    legitimately says "having been generated", and matching prose would flag
    that - the same false-positive trap that bit the earlier PointD check.
    """
    S.live_read_connections()
    run_body = capture["cs"].split("[Start]")[1]
    for call in ("ExecuteAction", "ActionManager", "GenerateConnections",
                 "XEsGenerate"):
        assert call not in run_body, (
            "the connection reader invokes %s - it must read, not generate" % call
        )


# ---------------------------------------------------------------------------
# Shape of the read
# ---------------------------------------------------------------------------

def test_read_connections_binds_getconnections_by_shape(capture):
    S.live_read_connections()
    cs = capture["cs"]
    assert 'RequireMethod(finderType, "GetConnections"' in cs
    assert "ConnectionsFilter" in cs


def test_a_null_result_is_an_error_not_an_empty_project(capture):
    """
    Reporting "no connections" for a null return would be indistinguishable
    from connections simply not having been generated.
    """
    S.live_read_connections()
    assert "refusing to report" in capture["cs"]


def test_the_true_total_is_reported_alongside_the_filtered_count(capture):
    S.live_read_connections(page="+P/1")
    cs = capture["cs"]
    assert 'results["total"] = total;' in cs
    assert 'results["matched"] = matched;' in cs


def test_a_page_filter_is_applied_in_the_script(capture):
    S.live_read_connections(page="+P/1")
    assert 'cpgName != "+P/1"' in capture["cs"]


def test_no_page_filter_when_none_is_given(capture):
    S.live_read_connections()
    assert "cpgName" not in capture["cs"]


def test_a_hostile_page_name_survives(capture):
    """PAGENAME/LIMIT are token names; a page called one must not corrupt."""
    S.live_read_connections(page="+LIMIT/1")
    assert '"+LIMIT/1"' in capture["cs"]


def test_a_result_path_token_in_the_page_is_refused(capture):
    out = S.live_read_connections(page="{{RESULT_PATH}}")
    assert out["success"] is False
    assert not capture["scripts"]


@pytest.mark.parametrize("bad", ["x", -1, 0, 99999])
def test_limit_is_validated(bad, capture):
    out = S.live_read_connections(limit=bad)
    assert out["success"] is False
    assert not capture["scripts"]


# ---------------------------------------------------------------------------
# Staleness: an empty list must not read as "nothing is wired"
# ---------------------------------------------------------------------------

def _result(monkeypatch, payload):
    monkeypatch.setattr(S, "_execute_script",
                        lambda script, timeout=30.0: {"success": True,
                                                      "results": payload})


def test_zero_connections_is_reported_as_probably_ungenerated(monkeypatch):
    _result(monkeypatch, {"success": True, "total": 0, "matched": 0,
                          "connections": []})
    out = S.live_read_connections()
    assert out["stale"] is True
    assert "generate_connections" in out["nextStep"]


def test_a_page_with_none_but_a_project_with_some_is_not_stale(monkeypatch):
    """The project HAS connections, so generation has clearly run."""
    _result(monkeypatch, {"success": True, "total": 3085, "matched": 0,
                          "connections": []})
    out = S.live_read_connections(page="+P/1")
    assert out["stale"] is False
    assert "3085" in out["note"]


def test_a_populated_read_carries_no_staleness_warning(monkeypatch):
    _result(monkeypatch, {"success": True, "total": 12, "matched": 12,
                          "connections": [{"handle": "h"}]})
    out = S.live_read_connections()
    assert "stale" not in out and "nextStep" not in out


# ---------------------------------------------------------------------------
# The connection-line geometry fix
# ---------------------------------------------------------------------------

def _connect_script(monkeypatch):
    """Drive live_connect_pins past its pin probe and capture the draw script."""
    scripts = []

    probe = {
        "success": True,
        "page": "+P/1",
        "from": {"placement": {
            "location": {"x": 60.0, "y": 200.0},
            "boundingBox": [{"x": 58.0, "y": 192.0}, {"x": 63.0, "y": 208.0}],
            "pins": [{"index": 0, "raw": {"x": 0.0, "y": 6.0}}]}},
        "to": {"placement": {
            "location": {"x": 140.0, "y": 200.0},
            "boundingBox": [{"x": 138.0, "y": 192.0}, {"x": 143.0, "y": 208.0}],
            "pins": [{"index": 0, "raw": {"x": 0.0, "y": 6.0}}]}},
    }

    def fake(script, timeout=30.0):
        scripts.append(script)
        if len(scripts) == 1:          # the pin probe
            return {"success": True, "results": probe}
        return {"success": True, "results": {"success": True, "lineDrawn": True}}

    monkeypatch.setattr(S, "_execute_script", fake)
    S.live_connect_pins("+P/1", "hA", 0, "hB", 0)
    assert len(scripts) == 2, "the draw script never ran"
    return scripts[1]


def test_the_line_is_anchored_before_it_is_drawn(monkeypatch):
    """
    Without an anchor, SetGraphics places the segment relative to the page
    ORIGIN - which put one end of every wire at (0,0).
    """
    cs = _connect_script(monkeypatch)
    assert 'GetWritable(dclType, "Location")' in cs
    anchor = cs.index("locProp.SetValue(dcl")
    draw = cs.index("Call(setG, dcl")
    assert anchor < draw, "the line must be anchored BEFORE SetGraphics"


def test_setgraphics_receives_relative_coordinates(monkeypatch):
    cs = _connect_script(monkeypatch)
    assert "MakePoint(ptType, 0.0, 0.0)" in cs, (
        "the segment must start at the anchor, i.e. relative (0,0)"
    )
    assert re.search(r"MakePoint\(ptType,\s*[\d.+-]+\s*-\s*[\d.+-]+", cs), (
        "the far end must be a DIFFERENCE, not an absolute coordinate"
    )


def test_a_missing_writable_location_is_a_hard_error(monkeypatch):
    """Silently drawing from the origin is the failure being prevented."""
    cs = _connect_script(monkeypatch)
    assert "has no writable Location" in cs
    assert "page origin" in cs


def test_the_anchor_is_reported_back(monkeypatch):
    cs = _connect_script(monkeypatch)
    assert 'results["anchor"]' in cs
