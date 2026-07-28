"""Offline tests for api/actions/scripted.py: the generated C# must stay
well-formed and injection-free for every parameter. _execute_script is
replaced by a capture stub, so no EPLAN is needed."""

import pytest

from api.actions import scripted


@pytest.fixture
def capture(monkeypatch):
    """Stub _execute_script; captures the generated C# instead of running it."""
    captured = {}

    def fake_execute(script, timeout=30.0):
        captured["script"] = script
        return {"success": True, "results": {"stubbed": True}}

    monkeypatch.setattr(scripted, "_execute_script", fake_execute)
    return captured


INJECTION = '100).ToList(); System.Environment.Exit(0); var x = db.Parts.Take(1'


def _string_literals_balanced(cs: str) -> bool:
    """After stripping escape sequences, every line must contain an even
    number of quotes - i.e. no value broke out of its string literal."""
    stripped = cs.replace("\\\\", "").replace('\\"', "")
    return all(line.count('"') % 2 == 0 for line in stripped.splitlines())


# ---------------------------------------------------------------------------
# parts_db_query: limit must be an integer, never raw text in the script
# ---------------------------------------------------------------------------

def test_parts_db_query_rejects_non_integer_limit(capture):
    result = scripted.parts_db_query(limit=INJECTION)
    assert result["success"] is False
    assert "limit" in result["error"].lower()
    assert "script" not in capture, "malicious limit must never reach the script"


def test_parts_db_query_coerces_numeric_string_limit(capture):
    result = scripted.parts_db_query(limit="25")
    assert result["success"] is True
    assert ".Take(25)" in capture["script"]


def test_parts_db_query_default_limit_in_script(capture):
    scripted.parts_db_query()
    assert ".Take(100)" in capture["script"]


# ---------------------------------------------------------------------------
# filter_property / filter_value hardening
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("bad", [
    "PartNr; evil()",
    'PartNr")',
    "1Leading",
    "a-b",
    "a b",
])
def test_query_and_count_reject_non_identifier_filter_property(capture, bad):
    for func in (scripted.parts_db_query, scripted.parts_db_count):
        result = func(filter_property=bad, filter_value="x")
        assert result["success"] is False
        assert "filter_property" in result["error"]
    assert "script" not in capture


def test_filter_value_is_escaped_not_injected(capture):
    scripted.parts_db_query(filter_property="PartNr", filter_value='X"); evil(); ("')
    script = capture["script"]
    # The payload's quotes must arrive escaped, leaving every literal intact.
    assert 'X\\"); evil(); (\\"' in script
    assert _string_literals_balanced(script)


def test_return_properties_are_escaped(capture):
    scripted.parts_db_query(return_properties=['PartNr', 'bad"name'])
    script = capture["script"]
    assert 'bad\\"name' in script


# ---------------------------------------------------------------------------
# parts_db_get_part / create / update escaping
# ---------------------------------------------------------------------------

def test_get_part_escapes_part_number(capture):
    scripted.parts_db_get_part('P"); evil(); ("')
    script = capture["script"]
    assert 'P\\"); evil(); (\\"' in script
    assert _string_literals_balanced(script)


def test_create_with_no_properties_yields_empty_arrays(capture):
    result = scripted.parts_db_create("PN-1")
    assert result["success"] is True
    script = capture["script"]
    assert "string[] propNames = new string[] {  };" in script
    assert "string[] propValues = new string[] {  };" in script


def test_create_property_values_stringified_and_escaped(capture):
    scripted.parts_db_create("PN-2", {"ARTICLE_DESCR1": 'x"y', "ARTICLE_NOTE": 42})
    script = capture["script"]
    assert '"x\\"y"' in script
    assert '"42"' in script


def test_update_escapes_all_three_arguments(capture):
    scripted.parts_db_update('p"n', 'prop"name', 'va"lue')
    stripped = capture["script"].replace('\\"', "").replace("\\\\", "")
    for line in stripped.splitlines():
        assert line.count('"') % 2 == 0, f"unbalanced literal: {line!r}"


# ---------------------------------------------------------------------------
# settings / pathmap: numeric coercion and escaping
# ---------------------------------------------------------------------------

def test_settings_index_coercion_blocks_injection(capture):
    # A non-numeric index raises before any script is generated.
    with pytest.raises((TypeError, ValueError)):
        scripted.settings_get_string("USER.X", index="1); evil((")
    assert "script" not in capture


def test_settings_set_int_coerces_value(capture):
    result = scripted.settings_set_int("USER.X", "7")
    assert result["success"] is True
    assert '"USER.X", 7, 0' in capture["script"]


def test_settings_path_with_quotes_escaped(capture):
    scripted.settings_get_bool('USER."quoted"')
    assert 'USER.\\"quoted\\"' in capture["script"]


def test_pathmap_substitute_escapes_backslashes(capture):
    scripted.pathmap_substitute(r"C:\Users\test\$(DOC)")
    script = capture["script"]
    assert r"C:\\Users\\test" in script
