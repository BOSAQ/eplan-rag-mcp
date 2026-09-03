"""
The security guards, proven with EPLAN closed.

Each test here pins one boundary that was previously open. They are grouped by
the thing that goes wrong when the guard is missing, because that is what a
future reader needs in order to decide whether a change is safe.
"""

import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
MCP = os.path.join(os.path.dirname(HERE), "mcp_server")
for p in (MCP, os.path.join(MCP, "api")):
    if p not in sys.path:
        sys.path.insert(0, p)

from api.actions._base import _build_action  # noqa: E402
from api.actions import fixtures  # noqa: E402
from api.actions import scripts as scripts_mod  # noqa: E402
from api.actions import scripted  # noqa: E402


# ---------------------------------------------------------------------------
# Parameter-KEY injection into the action command line.
#
# The value side already refused a double quote, because a quote lets a value
# close its own token and append further /PARAM pairs. The key side was
# interpolated raw, and Python's **kwargs accepts any string - so the same
# attack worked by putting it in the key instead.
# ---------------------------------------------------------------------------

def test_build_action_accepts_ordinary_keys():
    assert _build_action("Act", PROJECTNAME="x") == "Act /PROJECTNAME:x"
    assert _build_action("Act", A1_b="v") == "Act /A1_b:v"


@pytest.mark.parametrize("bad_key", [
    "PROJECTNAME:C:/x.elk /ScriptFile",   # the reported injection
    "NAME /OTHER",                        # a bare second parameter
    "NA ME",                              # whitespace splits the token
    "NAME:sub",                           # colon ends the key early
    'NAME"',                              # quote
    "NAME/x",
    "",
])
def test_build_action_rejects_a_key_that_could_inject_a_second_parameter(bad_key):
    with pytest.raises(ValueError) as exc:
        _build_action("XPrjActionProjectOpen", **{bad_key: "value"})
    assert "parameter name" in str(exc.value)


def test_build_action_still_rejects_a_quote_in_a_value():
    """The pre-existing value guard must survive the key guard being added."""
    with pytest.raises(ValueError):
        _build_action("Act", EXPORTFILE='"x" /EXPORTFILE:C:/evil.pdf')


# ---------------------------------------------------------------------------
# Scratch-clone path traversal.
#
# `name` becomes a filename. os.path.join absorbs ".." and DISCARDS the base
# entirely for an absolute path, so an unchecked name put the clone outside the
# scratch root - where scratch_project_discard then refused to clean it up.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_name", [
    "../../../Users/someone/Documents/EPLAN/proj",
    "..\\..\\proj",
    "sub/dir",
    "sub\\dir",
    "C:/absolute/proj",
    "C:proj",
    "\\\\server\\share\\proj",
    "..",
    ".",
    "",
    "   ",
    " leading",
    "trailing ",
    "CON",
    "com1.elk",
])
def test_scratch_create_rejects_a_name_that_is_not_a_bare_name(bad_name):
    result = fixtures._reject_unsafe_name(bad_name)
    assert result is not None, f"{bad_name!r} should have been rejected"
    assert result["success"] is False


@pytest.mark.parametrize("ok_name", [
    "SCRATCH_01",
    "proj_scratch_20260903_141500",
    "a.b.c",
    "Energy-Test_2",
])
def test_scratch_create_accepts_an_ordinary_name(ok_name):
    assert fixtures._reject_unsafe_name(ok_name) is None


def test_scratch_create_refuses_a_traversal_name_end_to_end(tmp_path, monkeypatch):
    """
    The guard must fire before anything is copied, and the template must not
    even need to exist for a clearly-bad name to be refused... but the template
    check runs first, so give it a real one and assert nothing was written.
    """
    monkeypatch.setattr(fixtures, "SCRATCH_ROOT", str(tmp_path / "scratch"))
    template = tmp_path / "tpl.elk"
    template.write_text("stub", encoding="utf-8")
    (tmp_path / "tpl.edb").mkdir()

    outside = tmp_path / "outside"
    outside.mkdir()

    result = fixtures.scratch_project_create(
        str(template), name="../outside/pwned", open_after=False
    )
    assert result["success"] is False
    assert not list(outside.iterdir()), "clone escaped the scratch root"


# ---------------------------------------------------------------------------
# Code-loading tools: remote paths.
#
# _build_action only rejects double quotes, and a UNC path contains none. The
# file it names is fetched from another machine and can change between the
# check and the load.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad_path", [
    "\\\\attacker\\share\\evil.cs",
    "//attacker/share/evil.cs",
    "\\\\?\\UNC\\host\\share\\x.cs",
])
def test_script_tools_refuse_a_unc_path(bad_path):
    err = scripts_mod._reject_remote_path(bad_path)
    assert err is not None and err["success"] is False
    assert "UNC" in err["error"]


@pytest.mark.parametrize("ok_path", [
    r"C:\scripts\helper.cs",
    "C:/scripts/helper.cs",
    "helper.cs",
])
def test_script_tools_accept_a_local_path(ok_path):
    assert scripts_mod._reject_remote_path(ok_path) is None


def test_script_tools_reject_an_empty_path():
    assert scripts_mod._reject_remote_path("")["success"] is False


# ---------------------------------------------------------------------------
# execute_custom_script: pre-flight and audit trail.
# ---------------------------------------------------------------------------

def test_custom_script_without_the_placeholder_is_refused_not_timed_out():
    """
    A script with no {{RESULT_PATH}} can never write a result file, so it could
    only ever end in 'Timeout waiting for script results' after burning the
    whole timeout - and that message would send the caller looking for the
    wrong problem.
    """
    result = scripted.execute_custom_script("public class X { }")
    assert result["success"] is False
    assert "{{RESULT_PATH}}" in result["error"]


@pytest.mark.parametrize("empty", ["", "   ", None])
def test_custom_script_requires_a_script(empty):
    result = scripted.execute_custom_script(empty)
    assert result["success"] is False


def test_caller_supplied_script_is_archived_before_it_runs(tmp_path, monkeypatch):
    """
    The generated .cs is deleted in _execute_script's finally, and the action
    trace records only a path that no longer exists. For arbitrary caller code
    that left no evidence at all of what executed, so the source is archived
    first - and, critically, BEFORE execution, so a script that takes EPLAN down
    with it is still on disk afterwards.
    """
    monkeypatch.setattr(scripted, "AUDIT_SCRIPT_DIR", str(tmp_path / "audit"))
    name = scripted._archive_caller_script("public class Demo { /* marker */ }")
    assert name and name.endswith(".cs")
    written = (tmp_path / "audit" / name).read_text(encoding="utf-8")
    assert "marker" in written


def test_archiving_never_raises_even_when_the_directory_is_unusable(monkeypatch):
    """A failure to archive must not block the caller."""
    monkeypatch.setattr(scripted, "AUDIT_SCRIPT_DIR", "\x00invalid")
    assert scripted._archive_caller_script("x") is None


def test_identical_scripts_archive_under_the_same_digest(tmp_path, monkeypatch):
    monkeypatch.setattr(scripted, "AUDIT_SCRIPT_DIR", str(tmp_path / "audit"))
    a = scripted._archive_caller_script("same body")
    b = scripted._archive_caller_script("same body")
    c = scripted._archive_caller_script("different body")
    assert a.split("_")[-1] == b.split("_")[-1]
    assert a.split("_")[-1] != c.split("_")[-1]


# ---------------------------------------------------------------------------
# Dangerous tools must SAY they are dangerous.
#
# server.py passes __doc__ through unchanged, so the docstring is the whole of
# the model's safety context for these calls.
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("func", [
    scripted.execute_custom_script,
    scripts_mod.register_script,
    scripts_mod.execute_script,
])
def test_code_execution_tools_carry_explicit_danger_wording(func):
    doc = (func.__doc__ or "")
    assert "DANGEROUS" in doc, f"{func.__name__} does not warn that it is dangerous"
    assert "confirm with the user" in doc.lower(), (
        f"{func.__name__} does not tell the model to confirm first"
    )
    assert "never pass" in doc.lower(), (
        f"{func.__name__} does not warn against passing content it has read - "
        f"the prompt-injection path"
    )


def test_load_api_module_warns_about_persistence():
    from api.actions.addons import load_api_module
    doc = load_api_module.__doc__ or ""
    assert "DANGEROUS" in doc
    assert "PERSIST" in doc.upper(), (
        "registration survives EPLAN restarts; a model that does not know that "
        "will treat it as a one-off"
    )


def test_execute_raw_action_points_at_the_validated_alternative():
    from api.actions.addons import execute_raw_action
    doc = execute_raw_action.__doc__ or ""
    assert "action_run" in doc, (
        "execute_raw_action should steer callers to the validated dispatcher"
    )
