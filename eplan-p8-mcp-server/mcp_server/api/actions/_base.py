"""
Base utilities for EPLAN actions.
Shared functions used by all action modules.

Every action executes inside a C# script under QuietMode
(QuietModes.ShowNoDialogs) so no EPLAN dialog can block unattended runs.
"""

from typing import Optional
import sys
import os

# Add parent directory to path for imports
mcp_root = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if mcp_root not in sys.path:
    sys.path.insert(0, mcp_root)

# Also insert the current folder's parent so 'from .project import ...' style works if needed
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from eplan_connection import get_manager, cs_escape  # noqa: F401  (cs_escape re-exported)


class QuietManagerWrapper:
    """Wrapper that forces execute_action to run with quiet_mode=True (via script)."""
    def __init__(self, manager):
        self.manager = manager

    def __getattr__(self, name):
        if name == "execute_action":
            # Direct everything to execute_action with quiet_mode=True
            return lambda action, *args, **kwargs: self.manager.execute_action(action, quiet_mode=True)
        return getattr(self.manager, name)


def _get_connected_manager():
    """Get the connection manager, ensuring it's connected."""
    manager = get_manager()
    if not manager.connected:
        return None, {"success": False, "message": "Not connected to EPLAN. Call eplan_connect() first."}
    return QuietManagerWrapper(manager), None


def _build_action(action_name: str, **params) -> str:
    """
    Build an action string with parameters.

    Values are quoted when they contain whitespace. A value that already looks
    like a well-formed quoted token is left alone so callers can pre-quote.

    A double quote anywhere else in a value is REJECTED rather than passed
    through. EPLAN re-parses this command string with
    /([a-zA-Z0-9_]+):("([^"]*)"|([^\\s]*)), so a stray quote lets a value close
    its own token and inject further /PARAM pairs - e.g. a project_name of
    '"x" /EXPORTFILE:C:/evil.pdf' would smuggle in an EXPORTFILE parameter.
    That silently defeats the registry allowlist in catalog.action_run(), which
    is documented as the validated alternative to execute_raw_action. Since no
    EPLAN parameter value legitimately contains a double quote, refusing is
    both safe and lossless.

    Raises:
        ValueError: if a string value contains an unbalanced double quote.
            Callers reach this through the MCP tool wrapper, which turns it
            into {"success": False, "error": ...} rather than a traceback.
    """
    parts = [action_name]
    for key, value in params.items():
        if value is not None and value != "":
            if isinstance(value, bool):
                value = "1" if value else "0"
            if isinstance(value, str):
                quoted = (len(value) >= 2 and value.startswith('"')
                          and value.endswith('"') and '"' not in value[1:-1])
                if not quoted:
                    if '"' in value:
                        raise ValueError(
                            f'Refusing to build action {action_name!r}: value for '
                            f'/{key} contains a double quote, which would let it '
                            f'inject additional parameters. Value: {value!r}'
                        )
                    if any(c.isspace() for c in value):
                        value = f'"{value}"'
            parts.append(f"/{key}:{value}")
    return " ".join(parts)


def _execute_with_quiet_mode(action: str) -> dict:
    """Execute an action with QuietMode enabled to suppress dialogs."""
    manager, error = _get_connected_manager()
    if error:
        return error
    return manager.execute_action(action, quiet_mode=True)


# ---------------------------------------------------------------------------
# Reporting what an export actually wrote
# ---------------------------------------------------------------------------
#
# EPLAN's export actions take a filename but do not promise to use it: the
# active export scheme's own output settings decide the basename, and the
# action still returns success. A wrapper that echoes back the requested
# EXPORTFILE therefore reports a file that is not on disk, and a caller that
# reads it back gets FileNotFoundError with nothing pointing at the real name.
#
# So instead of trusting the request, snapshot the target directory and diff
# it. The snapshot carries mtime and size, not just names, because an export
# that OVERWRITES a file from an earlier run changes no name at all - a
# name-only diff would report that nothing was written.


def _snapshot_dir(directory: str) -> Optional[dict]:
    """
    {normcased name: (real name, mtime_ns, size)} for the files in *directory*.

    Returns None if the directory cannot be listed - it does not exist yet, it
    is on a share this process cannot read, or EPLAN is on another machine.
    That is reported to the caller as unavailable verification, never as
    "nothing was written".
    """
    snapshot = {}
    try:
        with os.scandir(directory) as entries:
            for entry in entries:
                try:
                    if not entry.is_file():
                        continue
                    stat = entry.stat()
                except OSError:
                    # Vanished or unreadable mid-scan; absent from the snapshot
                    # means a later scan can only report it as changed, which
                    # is the safe direction.
                    continue
                snapshot[os.path.normcase(entry.name)] = (
                    entry.name, stat.st_mtime_ns, stat.st_size
                )
    except OSError:
        return None
    return snapshot


def _report_written_files(result: dict, export_file: str,
                          directory: str, before: Optional[dict]) -> dict:
    """Add requestedFile/writtenFiles/requestedFileWritten to *result*."""
    result = dict(result)
    requested = os.path.abspath(export_file)
    result["requestedFile"] = requested

    after = _snapshot_dir(directory)
    if before is None or after is None:
        result["verification"] = (
            "unavailable: could not list %s, so what EPLAN wrote there is "
            "unknown. The export itself may well have succeeded - check the "
            "directory yourself rather than trusting requestedFile."
            % directory
        )
        return result

    changed = sorted(
        after[key][0] for key in after
        if before.get(key, (None, None, None))[1:] != after[key][1:]
    )
    result["writtenFiles"] = [os.path.join(directory, name) for name in changed]

    requested_name = os.path.normcase(os.path.basename(requested))
    result["requestedFileWritten"] = requested_name in after and (
        before.get(requested_name, (None, None, None))[1:]
        != after[requested_name][1:]
    )

    if not result["requestedFileWritten"]:
        if changed:
            # Deliberately does not tell the caller to pass export_scheme:
            # export_pxf_project has no such parameter, and naming a kwarg
            # that does not exist invites exactly the failed retry that #28
            # exists to prevent. The per-wrapper remedy is in each docstring.
            result["note"] = (
                "EPLAN did not write the requested basename. EPLAN decides "
                "the output filename itself - from the active export scheme "
                "for the PDF exports, from its own extension rule for PXF - "
                "not from export_file. writtenFiles is what actually changed "
                "in the target directory; see this wrapper's docstring for "
                "how to pin the name."
            )
        else:
            result["note"] = (
                "The action reported success but nothing in the target "
                "directory changed. Either the scheme wrote somewhere else "
                "entirely, or the export produced no file - do not treat "
                "requestedFile as existing."
            )
    return result


def _execute_and_report_written(action: str, export_file: str) -> dict:
    """
    Run *action* under QuietMode, then say which files it really wrote.

    Existing fields of the action result are passed through untouched, so
    anything matching on success/parameters keeps working. success is
    deliberately NOT flipped when the requested basename is missing: the
    export did happen, and calling it a failure would be its own lie. The
    truth is in requestedFileWritten and writtenFiles.
    """
    directory = os.path.dirname(os.path.abspath(export_file))
    before = _snapshot_dir(directory)
    result = _execute_with_quiet_mode(action)
    if not isinstance(result, dict) or not result.get("success"):
        return result
    return _report_written_files(result, export_file, directory, before)
