"""Shared test setup: make both import spellings work from any directory.

The suite uses two of them:

    from api.actions import scripted          # 10 modules - needs mcp_server/
    from mcp_server.api.actions import ...    #  2 modules - needs its parent

Only the first directory used to be added here. The second happened to work
anyway when `python -m pytest` was run from eplan-p8-mcp-server/, because
that puts the current directory on sys.path - so the suite passed from there
and failed collection with `ModuleNotFoundError: No module named
'mcp_server'` from the repo root, or under a bare `pytest`. Add both
explicitly so the working directory stops mattering.
"""

import os
import sys

_TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
_SERVER_ROOT = os.path.dirname(_TESTS_DIR)                    # eplan-p8-mcp-server/
_PACKAGE_DIR = os.path.join(_SERVER_ROOT, "mcp_server")       # its mcp_server/

for _path in (_PACKAGE_DIR, _SERVER_ROOT):
    if _path not in sys.path:
        sys.path.insert(0, _path)
