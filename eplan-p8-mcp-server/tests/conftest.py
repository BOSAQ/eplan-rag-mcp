"""Shared test setup: make mcp_server importable from any test module."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_server"))
