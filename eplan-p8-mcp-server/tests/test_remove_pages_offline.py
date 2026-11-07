"""
live_remove_pages, asserted on its GENERATED C# with EPLAN closed.

A bulk page delete is the most destructive thing the live tools can do, so the
tests pin the guards: the expected-project check, dry run by default, the
max_pages cap, the everything-matches refusal, and the input validation that
keeps a too-broad prefix from ever reaching EPLAN.
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


def test_well_formed_and_scaffolded(capture):
    S.live_remove_pages(["=U1", "=U2"], ELK)
    cs = capture["cs"]
    assert _balanced(cs)
    assert "{{RESULT_PATH}}" in cs
    assert 'FindType("Eplan.EplApi.DataModel.LockingStep")' in cs
    assert "finally" in cs
    for line in cs.splitlines():
        if line.strip().startswith("using "):
            assert "Eplan.EplApi.DataModel" not in line


def test_dry_run_is_the_default(capture):
    S.live_remove_pages(["=U1"], ELK)
    cs = capture["cs"]
    assert 'results["dry_run"] = true;' in cs
    assert "if (!true)" in cs


def test_real_run_flips_the_flag(capture):
    S.live_remove_pages(["=U1"], ELK, dry_run=False)
    assert "if (!false)" in capture["cs"]


def test_expected_project_is_checked_in_script(capture):
    S.live_remove_pages(["=U1"], ELK)
    cs = capture["cs"]
    assert "ProjectLinkFilePath" in cs
    assert "REFUSING: the current project is" in cs
    # the path is escaped into a C# literal
    assert r"C:\\Projects\\BOSAQ\\ElectroClean\\ElectroClean MCP V0.1.elk" in cs


def test_prefixes_land_in_one_array(capture):
    S.live_remove_pages(["=U1", "=U2+PP1"], ELK)
    assert 'string[] prefixes = new string[] { "=U1", "=U2+PP1" };' in capture["cs"]


def test_single_string_prefix_is_accepted(capture):
    S.live_remove_pages("=U1", ELK)
    assert 'new string[] { "=U1" }' in capture["cs"]


def test_cap_and_everything_guard_present(capture):
    S.live_remove_pages(["=U1"], ELK, max_pages=50)
    cs = capture["cs"]
    assert "matched.Count > 50" in cs
    assert "matched.Count == total" in cs


def test_ordinal_prefix_match(capture):
    S.live_remove_pages(["=U1"], ELK)
    assert "StartsWith(pre, StringComparison.Ordinal)" in capture["cs"]


def test_default_timeout_allows_a_large_delete(capture):
    S.live_remove_pages(["=U1"], ELK)
    assert capture["timeout"] >= 300.0


@pytest.mark.parametrize("bad", ["=", "+", " = ", "U", ""])
def test_too_broad_or_empty_prefix_is_refused(capture, bad):
    result = S.live_remove_pages([bad], ELK)
    assert result["success"] is False
    assert "cs" not in capture


@pytest.mark.parametrize("bad", [[], None, 42])
def test_bad_prefix_container_is_refused(capture, bad):
    result = S.live_remove_pages(bad, ELK)
    assert result["success"] is False
    assert "cs" not in capture


@pytest.mark.parametrize("bad", [None, "", r"C:\Projects\x.edb", "x"])
def test_expected_project_must_be_an_elk_path(capture, bad):
    result = S.live_remove_pages(["=U1"], bad)
    assert result["success"] is False
    assert "cs" not in capture


@pytest.mark.parametrize("bad", [0, -3, "lots", None])
def test_max_pages_must_be_a_positive_integer(capture, bad):
    result = S.live_remove_pages(["=U1"], ELK, max_pages=bad)
    assert result["success"] is False
    assert "cs" not in capture


def test_injection_stays_in_literal(capture):
    S.live_remove_pages([INJECTION], ELK)
    cs = capture["cs"]
    assert _balanced(cs)
    assert '"' + INJECTION not in cs


def test_result_token_in_prefix_is_refused(capture):
    result = S.live_remove_pages(["={{RESULT_PATH}}"], ELK)
    assert result["success"] is False
    assert "cs" not in capture


def test_class_names_unique(capture):
    S.live_remove_pages(["=U1"], ELK)
    a = re.search(r"public class (\w+)", capture["cs"]).group(1)
    S.live_remove_pages(["=U1"], ELK)
    b = re.search(r"public class (\w+)", capture["cs"]).group(1)
    assert a != b
