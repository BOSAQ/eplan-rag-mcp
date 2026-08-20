"""Offline tests for api/actions/live.py: the generated reflection C# must stay
well-formed, injection-free, and free of the traps that make it fail inside
EPLAN's script engine. _execute_script is replaced by a capture stub, so no
EPLAN is needed."""

import pytest

from api.actions import live


@pytest.fixture
def capture(monkeypatch):
    """Stub _execute_script; captures the generated C# instead of running it."""
    captured = {}

    def fake_execute(script, timeout=30.0):
        captured["script"] = script
        captured["timeout"] = timeout
        return {"success": True, "results": {"stubbed": True}}

    monkeypatch.setattr(live, "_execute_script", fake_execute)
    return captured


INJECTION = '"; System.Environment.Exit(0); string y = "'


def _string_literals_balanced(cs: str) -> bool:
    """After stripping escape sequences, every line must contain an even
    number of quotes - i.e. no value broke out of its string literal."""
    stripped = cs.replace("\\\\", "").replace('\\"', "")
    return all(line.count('"') % 2 == 0 for line in stripped.splitlines())


ALL_TOOLS = [
    (live.live_query_functions, {}),
    (live.live_query_pages, {}),
    (live.live_set_function_text, {"name": "+X-K1", "text": "hi"}),
]


# ---------------------------------------------------------------------------
# The CS0234 trap: DataModel/HEServices must never appear as `using` directives
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fn,kwargs", ALL_TOOLS)
def test_no_datamodel_using_directive(capture, fn, kwargs):
    fn(**kwargs)
    for line in capture["script"].splitlines():
        if line.strip().startswith("using "):
            assert "Eplan.EplApi.DataModel" not in line
            assert "Eplan.EplApi.HEServices" not in line


# ---------------------------------------------------------------------------
# Assembly resolution: never Assembly.Load the native twin as the primary route
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fn,kwargs", ALL_TOOLS)
def test_resolves_types_from_loaded_assemblies(capture, fn, kwargs):
    fn(**kwargs)
    cs = capture["script"]
    # Scanning the loaded set is the primary route.
    assert "AppDomain.CurrentDomain.GetAssemblies()" in cs
    # In the Assembly.Load FALLBACK list, the *Netu names must come first: on
    # 2027 the un-suffixed name is the mixed-mode native twin and loading it
    # throws BadImageFormatException. Scope the check to the candidates array,
    # since the surrounding comment mentions the un-suffixed name too.
    start = cs.index("string[] candidates")
    candidates = cs[start:cs.index("};", start)]
    assert candidates.index('"Eplan.EplApi.DataModelNetu"') < candidates.index('"Eplan.EplApi.DataModelu"')
    assert candidates.index('"Eplan.EplApi.HEServicesNetu"') < candidates.index('"Eplan.EplApi.HEServicesu"')


@pytest.mark.parametrize("fn,kwargs", ALL_TOOLS)
def test_locking_step_taken_and_disposed(capture, fn, kwargs):
    fn(**kwargs)
    cs = capture["script"]
    assert 'FindType("Eplan.EplApi.DataModel.LockingStep")' in cs
    assert "finally" in cs
    assert 'lsType.GetMethod("Dispose")' in cs


# ---------------------------------------------------------------------------
# Script-engine syntax limits
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fn,kwargs", ALL_TOOLS)
def test_no_dictionary_index_initializers(capture, fn, kwargs):
    # `new Dictionary<..> { ["k"] = v }` is CS1525 in EPLAN's script engine.
    fn(**kwargs)
    assert '{ ["' not in capture["script"]
    assert "{[" not in capture["script"].replace(" ", "")


@pytest.mark.parametrize("fn,kwargs", ALL_TOOLS)
def test_result_path_placeholder_present(capture, fn, kwargs):
    fn(**kwargs)
    assert "{{RESULT_PATH}}" in capture["script"]


# ---------------------------------------------------------------------------
# limit is interpolated outside a string literal -> must be a real integer
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("fn,kwargs", ALL_TOOLS)
def test_rejects_non_integer_limit(capture, fn, kwargs):
    result = fn(limit=INJECTION, **kwargs)
    assert result["success"] is False
    assert "limit" in result["error"].lower()
    assert "script" not in capture, "malicious limit must never reach the script"


def test_query_coerces_numeric_string_limit(capture):
    result = live.live_query_pages(limit="25")
    assert result["success"] is True
    assert "list.Count < 25" in capture["script"]


def test_query_default_limit_in_script(capture):
    live.live_query_functions()
    assert "list.Count < 100" in capture["script"]


def test_set_function_text_default_limit_is_one(capture):
    # A mistaken name must not mass-edit the project.
    live.live_set_function_text(name="+X-K1", text="hi")
    assert "details.Count >= 1" in capture["script"]


# ---------------------------------------------------------------------------
# String parameters must stay inside their literals
# ---------------------------------------------------------------------------

def test_contains_injection_stays_in_literal(capture):
    live.live_query_functions(contains=INJECTION)
    cs = capture["script"]
    # The payload survives as DATA inside the literal - that is correct. What
    # matters is that its quotes were escaped so it cannot close the literal and
    # become code: no bare `"` remains around the injected text.
    assert '\\"' in cs
    assert _string_literals_balanced(cs)
    # Every quote in the payload is escaped, so the only occurrences of a bare
    # quote are the literal's own delimiters.
    assert '"' + INJECTION not in cs


def test_name_and_text_injection_stay_in_literal(capture):
    live.live_set_function_text(name=INJECTION, text=INJECTION)
    cs = capture["script"]
    assert _string_literals_balanced(cs)


def test_backslash_path_is_escaped(capture):
    live.live_set_function_text(name=r"+X-K1", text=r"C:\Temp\A1")
    cs = capture["script"]
    # A raw C:\Temp\A1 would be CS1009 (unrecognized escape \T, \A).
    assert r"C:\\Temp\\A1" in cs
    assert _string_literals_balanced(cs)


def test_unicode_line_terminator_stays_in_literal(capture):
    ls = chr(0x2028)
    live.live_query_pages(contains=f"a{ls}b")
    assert ls not in capture["script"]


# ---------------------------------------------------------------------------
# Contract details
# ---------------------------------------------------------------------------

def test_set_function_text_requires_name(capture):
    result = live.live_set_function_text(name="", text="hi")
    assert result["success"] is False
    assert "script" not in capture


@pytest.mark.parametrize("fn,kwargs", ALL_TOOLS)
def test_timeout_is_forwarded(capture, fn, kwargs):
    fn(timeout_seconds=123.0, **kwargs)
    assert capture["timeout"] == 123.0


@pytest.mark.parametrize("fn,kwargs", ALL_TOOLS)
def test_class_names_are_unique_per_call(capture, fn, kwargs):
    fn(**kwargs)
    first = capture["script"]
    fn(**kwargs)
    second = capture["script"]
    # Same script twice would collide in EPLAN's script engine cache.
    assert first != second


def test_ambiguous_match_guard_present(capture):
    # Function.Properties and FUNC_TEXT[int] both make a plain GetProperty throw
    # AmbiguousMatchException; the generated code must use the guarded lookup.
    live.live_set_function_text(name="+X-K1", text="hi")
    cs = capture["script"]
    assert "DeclaredOnly" in cs
    assert "Type.EmptyTypes" in cs
    assert 'GetPropInfo(props.GetType(), "FUNC_TEXT")' in cs
