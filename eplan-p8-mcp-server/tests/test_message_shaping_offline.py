"""Tests for _shape_messages: severity ranking, the cap, and honest truncation.

The old capture kept the first 20 messages in tree order and said nothing
about the rest. Measured over 1,463 logged actions on 2026-09-03: 206 entries
carried messages, **104 of them had exactly 20**, and nothing anywhere
exceeded 20 - so half of all captures were truncated by an unmeasured amount
with no field reporting it. In tree order the 20 that survive are whichever
EPLAN logged first, which for a chatty action means twenty
"Started opening database" lines while the real per-project error falls off
the end. That was observed on a live upgrade_projects call.

Ranking and capping happen in Python rather than in the generated C#
deliberately: this is the file where a typo mutes all ~180 tools, and policy
expressed here is testable without string-matching generated source.

Offline: pure data in, pure data out.
"""

import pytest

from eplan_connection import (
    MESSAGE_CAP,
    MESSAGE_LEVEL_RANK,
    MESSAGE_SCAN_CAP,
    EPLANConnectionManager,
)

shape = EPLANConnectionManager._shape_messages


def _raw(*pairs):
    return [{"text": t, "level": lvl} for t, lvl in pairs]


def _result(records, scanned=None):
    out = {"success": False, "eplanMessagesRaw": records}
    if scanned is not None:
        out["eplanMessagesScanned"] = scanned
    return out


# ---------------------------------------------------------------------------
# the defect this exists to fix
# ---------------------------------------------------------------------------

def test_an_error_behind_twenty_noise_lines_survives():
    """The measured upgrade_projects shape: chatter first, the real error last."""
    noise = [("Started opening database: %d" % i, "Message") for i in range(60)]
    records = _raw(*noise) + _raw(("Project X could not be upgraded", "Error"))

    out = shape(_result(records, scanned=61))

    assert out["eplanMessages"][0] == "Project X could not be upgraded"
    assert len(out["eplanMessages"]) == MESSAGE_CAP
    assert out["eplanMessagesTruncated"] is True
    assert out["eplanMessagesTotal"] == 61


def test_truncation_is_reported_with_the_real_total():
    records = _raw(*[("m%d" % i, "Message") for i in range(45)])
    out = shape(_result(records, scanned=45))
    assert out["eplanMessagesTruncated"] is True
    assert out["eplanMessagesTotal"] == 45
    assert len(out["eplanMessages"]) == MESSAGE_CAP


def test_no_truncation_flag_when_everything_fits():
    """A clean capture must not grow a field. The happy path pays nothing."""
    out = shape(_result(_raw(("only one", "Message")), scanned=1))
    assert out["eplanMessages"] == ["only one"]
    assert "eplanMessagesTruncated" not in out
    assert "eplanMessagesLevels" not in out, "all-Message needs no level list"


# ---------------------------------------------------------------------------
# ranking
# ---------------------------------------------------------------------------

def test_severity_order_is_fatal_error_warning_message():
    records = _raw(
        ("a message", "Message"),
        ("a warning", "Warning"),
        ("a fatal", "FatalError"),
        ("an error", "Error"),
    )
    out = shape(_result(records, scanned=4))
    assert out["eplanMessages"] == ["a fatal", "an error", "a warning", "a message"]


def test_assert_does_not_outrank_warning():
    """
    MessageLevel's numeric order is not a severity order.

    Trace=0, Message=1, Warning=2, Assert=3, Error=4, FatalError=5 - so sorting
    by enum value would put Assert, documented as "the lowest level of an error,
    which will not appear in GUI", above Warning. MESSAGE_LEVEL_RANK omits it,
    so it falls into the unranked bucket at the end.
    """
    records = _raw(("an assert", "Assert"), ("a warning", "Warning"))
    out = shape(_result(records, scanned=2))
    assert out["eplanMessages"] == ["a warning", "an assert"]
    assert "Assert" not in MESSAGE_LEVEL_RANK


def test_unknown_levels_sort_last_without_being_dropped():
    records = _raw(("weird", "SomethingNew"), ("plain", "Message"))
    out = shape(_result(records, scanned=2))
    assert out["eplanMessages"] == ["plain", "weird"]


def test_chronological_order_is_preserved_within_a_severity():
    """Stable sort: a reader still sees the sequence EPLAN produced."""
    records = _raw(*[("m%d" % i, "Message") for i in range(5)])
    out = shape(_result(records, scanned=5))
    assert out["eplanMessages"] == ["m0", "m1", "m2", "m3", "m4"]


def test_levels_are_reported_only_when_something_outranks_message():
    records = _raw(("boom", "Error"), ("chatter", "Message"))
    out = shape(_result(records, scanned=2))
    assert out["eplanMessagesLevels"] == ["Error", "Message"]


# ---------------------------------------------------------------------------
# shape of the contract
# ---------------------------------------------------------------------------

def test_eplan_messages_stays_a_list_of_strings():
    """Additive, not a contract break - existing callers keep working."""
    out = shape(_result(_raw(("x", "Error")), scanned=1))
    assert isinstance(out["eplanMessages"], list)
    assert all(isinstance(m, str) for m in out["eplanMessages"])


def test_the_raw_fields_are_consumed_not_leaked():
    out = shape(_result(_raw(("x", "Error")), scanned=1))
    assert "eplanMessagesRaw" not in out
    assert "eplanMessagesScanned" not in out


def test_a_result_without_raw_messages_is_untouched():
    original = {"success": True, "executor": "action", "parameters": {"A": "1"}}
    assert shape(dict(original)) == original


@pytest.mark.parametrize("bad", [None, "not a list", 42, {}])
def test_a_malformed_raw_field_is_left_alone(bad):
    """Never raise on the diagnostics path - that would mask the real failure."""
    out = shape({"success": False, "eplanMessagesRaw": bad})
    assert out["success"] is False


def test_non_dict_entries_are_skipped_not_fatal():
    records = [{"text": "good", "level": "Error"}, "junk", None]
    out = shape(_result(records, scanned=3))
    assert out["eplanMessages"] == ["good"]


def test_entries_without_text_are_dropped():
    records = _raw(("", "Error")) + _raw(("real", "Message"))
    out = shape(_result(records, scanned=2))
    assert out["eplanMessages"] == ["real"]


def test_all_empty_text_produces_no_messages_field():
    out = shape(_result(_raw(("", "Error"), ("", "Message")), scanned=2))
    assert "eplanMessages" not in out


def test_missing_scanned_count_falls_back_to_what_arrived():
    out = shape(_result(_raw(*[("m%d" % i, "Message") for i in range(3)])))
    assert out["eplanMessagesTotal"] == 3


def test_message_length_is_pinned_not_just_the_flag():
    """
    Pins the cap itself.

    The C# side collects up to MESSAGE_SCAN_CAP (500). If the Python shaping
    seam were ever bypassed, or the key contract drifted, the model would get a
    500-entry dump in the one channel it is obliged to read - a token blowout
    in exactly the wrong place.
    """
    records = _raw(*[("m%d" % i, "Message") for i in range(MESSAGE_SCAN_CAP)])
    out = shape(_result(records, scanned=MESSAGE_SCAN_CAP))
    assert len(out["eplanMessages"]) == MESSAGE_CAP
    assert MESSAGE_CAP < MESSAGE_SCAN_CAP


# ---------------------------------------------------------------------------
# the generated C# must keep emitting what this expects
# ---------------------------------------------------------------------------

def test_template_emits_the_raw_fields_and_the_safe_level_accessor():
    """
    Guards the C#/Python seam.

    Token asserts rather than a fixture comparison: this block is edited by
    other work, and a fixture that gets regenerated on every change stops
    being read.
    """
    import eplan_connection as ec

    mgr = ec.EPLANConnectionManager()
    mgr.connected = True
    captured = {}

    class _Client:
        SynchronousMode = True

        def ExecuteAction(self, action):
            path = action.split('"')[1]
            with open(path, encoding="utf-8") as f:
                captured["cs"] = f.read()
            raise RuntimeError("stop here - the script content is what matters")

    mgr.client = _Client()
    mgr.execute_action("someAction /A:1", quiet_mode=True)
    cs = captured["cs"]

    assert 'results["eplanMessagesRaw"]' in cs
    assert 'results["eplanMessagesScanned"]' in cs
    # MessageLevel, never .Level: the latter is CS1061 and breaks every action.
    assert "m.MessageLevel.ToString()" in cs
    import re
    assert re.search(r"(?<!Message)\.Level\b", cs) is None
    # ToString() rather than an enum member, so a renamed member cannot become
    # a CS0117 at generation time.
    assert "MessageLevel.FatalError" not in cs
    assert str(MESSAGE_SCAN_CAP) in cs
    # The cast that a previous CS1061 regression turned into a total outage.
    assert "it.Current as BaseException" in cs
