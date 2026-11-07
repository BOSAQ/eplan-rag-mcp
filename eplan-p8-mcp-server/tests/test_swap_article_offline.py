"""
live_swap_article, asserted on its GENERATED C# with EPLAN closed.

An article swap rewrites what gets ordered, so the tests pin the guards: the
expected-project check, dry run by default, the all-or-nothing plan (nothing is
written until every device checks out), main-function-only matching, the
read-back, and input validation that keeps bad values out of the script.
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
OLD = "SE.A9F87416"
NEW = "SE.A9F89420"
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
    S.live_swap_article(["=MCP+-31F1", "=MCP+-32F1"], OLD, NEW, ELK)
    cs = capture["cs"]
    assert _balanced(cs)
    assert "{{RESULT_PATH}}" in cs
    assert 'FindType("Eplan.EplApi.DataModel.LockingStep")' in cs
    for line in cs.splitlines():
        if line.strip().startswith("using "):
            assert "Eplan.EplApi.DataModel" not in line


def test_dry_run_is_the_default(capture):
    S.live_swap_article("=MCP+-31F1", OLD, NEW, ELK)
    cs = capture["cs"]
    assert 'results["dry_run"] = true;' in cs
    assert "if (!true)" in cs


def test_real_run_flips_the_flag(capture):
    S.live_swap_article("=MCP+-31F1", OLD, NEW, ELK, dry_run=False)
    assert "if (!false)" in capture["cs"]


def test_expected_project_is_checked(capture):
    S.live_swap_article("=MCP+-31F1", OLD, NEW, ELK)
    cs = capture["cs"]
    assert "ProjectLinkFilePath" in cs
    assert "REFUSING: the current project is" in cs
    assert r"C:\\Projects\\BOSAQ\\ElectroClean\\ElectroClean MCP V0.1.elk" in cs


def test_values_land_in_the_script(capture):
    S.live_swap_article(["=MCP+-31F1", "=MCP+-32F1"], OLD, NEW, ELK)
    cs = capture["cs"]
    assert 'string[] devs = new string[] { "=MCP+-31F1", "=MCP+-32F1" };' in cs
    assert 'string oldPart = "SE.A9F87416";' in cs
    assert 'string newPart = "SE.A9F89420";' in cs


def test_plan_is_all_or_nothing(capture):
    """The refusal must come before the write block."""
    S.live_swap_article("=MCP+-31F1", OLD, NEW, ELK, dry_run=False)
    cs = capture["cs"]
    refuse = cs.index("REFUSING, nothing changed")
    write = cs.index("StoreToObject")
    assert refuse < write
    for msg in ("no such device", "does not carry", "times"):
        assert msg in cs


def test_device_with_several_functions_is_not_refused(capture):
    """A breaker and its aux contact share a name - found live on =MCP+-31F1.
    Uniqueness is judged on the article, never on the function count."""
    S.live_swap_article("=MCP+-31F1", OLD, NEW, ELK)
    cs = capture["cs"]
    assert "main functions with this name" not in cs
    assert "IsMainFunction" not in cs
    assert "mainFn[n] = fn;   // the function that carries the article" in cs


def test_exact_part_match_and_other_articles_untouched(capture):
    S.live_swap_article("=MCP+-31F1", OLD, NEW, ELK)
    cs = capture["cs"]
    assert "if (!addMode && pnr == oldPart)" in cs
    assert 'results["untouched"]' in cs


def test_real_run_reads_back(capture):
    S.live_swap_article("=MCP+-31F1", OLD, NEW, ELK, dry_run=False)
    cs = capture["cs"]
    assert 'results["verified"]' in cs
    assert "does not show the change" in cs


def test_reports_whether_new_part_is_a_project_article(capture):
    S.live_swap_article("=MCP+-31F1", OLD, NEW, ELK)
    assert 'results["new_part_in_project"]' in capture["cs"]


def test_guard_defaults_to_refuse_and_override_works(capture):
    S.live_swap_article("=MCP+-31F1", OLD, NEW, ELK, dry_run=False)
    assert "GuardScratch(project, false," in capture["cs"]
    S.live_swap_article("=MCP+-31F1", OLD, NEW, ELK, dry_run=False, allow_real_project=True)
    assert "GuardScratch(project, true," in capture["cs"]


@pytest.mark.parametrize("bad", [[], None, 42, [""], ["=MCP+-31F1", "=MCP+-31F1"]])
def test_bad_devices_are_refused(capture, bad):
    result = S.live_swap_article(bad, OLD, NEW, ELK)
    assert result["success"] is False
    assert "cs" not in capture


@pytest.mark.parametrize("old,new", [(OLD, ""), (OLD, None), (OLD, OLD), (None, "")])
def test_bad_part_numbers_are_refused(capture, old, new):
    result = S.live_swap_article("=MCP+-31F1", old, new, ELK)
    assert result["success"] is False
    assert "cs" not in capture


@pytest.mark.parametrize("bad", [None, "", r"C:\Projects\x.edb", "x"])
def test_expected_project_must_be_an_elk_path(capture, bad):
    result = S.live_swap_article("=MCP+-31F1", OLD, NEW, bad)
    assert result["success"] is False
    assert "cs" not in capture


def test_too_many_devices_refused(capture):
    result = S.live_swap_article(["=A+-K%d" % i for i in range(201)], OLD, NEW, ELK)
    assert result["success"] is False


def test_injection_stays_in_literal(capture):
    S.live_swap_article([INJECTION], INJECTION, NEW, ELK)
    cs = capture["cs"]
    assert _balanced(cs)
    assert '"' + INJECTION not in cs


def test_result_token_in_value_is_refused(capture):
    result = S.live_swap_article("={{RESULT_PATH}}", OLD, NEW, ELK)
    assert result["success"] is False
    assert "cs" not in capture


def test_class_names_unique(capture):
    S.live_swap_article("=MCP+-31F1", OLD, NEW, ELK)
    a = re.search(r"public class (\w+)", capture["cs"]).group(1)
    S.live_swap_article("=MCP+-31F1", OLD, NEW, ELK)
    b = re.search(r"public class (\w+)", capture["cs"]).group(1)
    assert a != b


# ---------------------------------------------------------------------------
# Add mode: an article for a device that has none (e.g. a split-off terminal level)
# ---------------------------------------------------------------------------

def test_add_mode_is_selected_by_empty_old_part(capture):
    S.live_swap_article("=MCP+-X31:2", None, "WAGO.2006-1201", ELK, function_type="Terminal")
    cs = capture["cs"]
    assert 'string oldPart = "";' in cs
    assert 'bool addMode = oldPart.Length == 0;' in cs
    assert 'string ftype = "Terminal";' in cs


def test_add_mode_refuses_ambiguous_or_already_equipped_devices(capture):
    S.live_swap_article("=MCP+-X31:2", None, "WAGO.2006-1201", ELK, dry_run=False)
    cs = capture["cs"]
    assert "narrow it with function_type" in cs
    assert "already has an article" in cs
    assert cs.index("REFUSING, nothing changed") < cs.index("AddArticleReference")


def test_add_mode_uses_add_article_reference(capture):
    S.live_swap_article("=MCP+-X31:2", "", "WAGO.2006-1201", ELK, dry_run=False)
    assert '"AddArticleReference"' in capture["cs"]
    assert 'new object[] { newPart, "1", (uint)1 }' in capture["cs"]


@pytest.mark.parametrize("bad", ["Ter minal", "1Terminal", "x;y"])
def test_function_type_must_be_a_type_name(capture, bad):
    result = S.live_swap_article("=MCP+-X31:2", None, "WAGO.2006-1201", ELK, function_type=bad)
    assert result["success"] is False
    assert "cs" not in capture
