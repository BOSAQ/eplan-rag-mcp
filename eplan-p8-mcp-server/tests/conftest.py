"""Shared test setup: import path, and keeping the suite out of the real log."""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_server"))


@pytest.fixture(autouse=True)
def _isolate_action_log(tmp_path, monkeypatch):
    """Point actions.jsonl at a tmp_path for every test in the suite.

    Without this the suite appends to mcp_server/logs/actions.jsonl - the same
    file the action audit reasons from. That had already happened at scale: a
    census on 2026-09-03 found 871 of its 1,463 entries were test fixtures from
    59 separate runs, spread through the file rather than clustered at the head,
    including entries whose action strings are injection-probe payloads. Any
    statistic drawn from that file was measuring the test suite as much as
    EPLAN.

    Autouse and unconditional on purpose. An opt-in fixture only protects the
    tests that remember to ask for it, and the polluting writes come from
    _log_action deep inside execute_action, where a test author has no reason
    to be thinking about logging at all.
    """
    monkeypatch.setenv("EPLAN_MCP_LOG_DIR", str(tmp_path / "logs"))
