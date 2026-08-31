"""
Scratch project fixtures - disposable copies of a template project for
unattended test runs.

Pattern: an agent testing an add-in or automation never mutates a real
project. Instead it clones a designated template project into a scratch
area, opens the clone, runs actions against it, verifies the outcome, and
discards the clone. Deletion is restricted to the scratch root so these
tools can never remove a real project.

An EPLAN project on disk is `<name>.elk` (link file) plus a sibling
`<name>.edb\\` directory holding the actual data; both are copied. The
template must not be open in EPLAN during cloning (EPLAN locks files inside
the .edb directory).
"""

import os
import shutil
import tempfile
import time

from ._base import _get_connected_manager

# Scratch root: everything created here is fair game for deletion.
SCRATCH_ROOT = os.environ.get(
    "EPLAN_MCP_SCRATCH",
    os.path.join(tempfile.gettempdir(), "eplan_mcp_scratch"),
)


def _resolve_project_parts(elk_path: str):
    """Return (elk_path, edb_path, base_name) for a project .elk file."""
    elk_path = os.path.abspath(elk_path)
    base, ext = os.path.splitext(elk_path)
    return elk_path, base + ".edb", os.path.basename(base)


def _inside_scratch(path: str) -> bool:
    try:
        return os.path.commonpath(
            [os.path.abspath(path), os.path.abspath(SCRATCH_ROOT)]
        ) == os.path.abspath(SCRATCH_ROOT)
    except ValueError:  # different drives
        return False


def scratch_project_create(template_project: str, name: str = None,
                           open_after: bool = True) -> dict:
    """
    Clone a template project into the scratch area for a disposable test run.

    Copies the template's .elk file and .edb directory to the scratch root
    (EPLAN_MCP_SCRATCH env var, default %TEMP%/eplan_mcp_scratch) under a
    unique name, then optionally opens the clone in EPLAN.

    The template must be CLOSED in EPLAN while cloning - EPLAN locks files
    inside an open project's .edb directory and the copy would fail or be
    inconsistent.

    Args:
        template_project: Full path to the template project's .elk file.
        name: Base name for the clone. Default: "<template>_scratch_<timestamp>".
        open_after: Open the clone in EPLAN after copying (default True;
            requires an active EPLAN connection).
    """
    src_elk, src_edb, src_base = _resolve_project_parts(template_project)
    if not os.path.exists(src_elk):
        return {"success": False, "error": f"Template .elk not found: {src_elk}"}
    if not os.path.isdir(src_edb):
        return {"success": False,
                "error": f"Template .edb directory not found next to the .elk: {src_edb}"}

    clone_base = name or f"{src_base}_scratch_{time.strftime('%Y%m%d_%H%M%S')}"
    # Uniquify if the name is taken.
    dst_dir = SCRATCH_ROOT
    os.makedirs(dst_dir, exist_ok=True)
    candidate = clone_base
    counter = 1
    while os.path.exists(os.path.join(dst_dir, candidate + ".elk")):
        candidate = f"{clone_base}_{counter}"
        counter += 1
    dst_elk = os.path.join(dst_dir, candidate + ".elk")
    dst_edb = os.path.join(dst_dir, candidate + ".edb")

    try:
        shutil.copy2(src_elk, dst_elk)
        shutil.copytree(src_edb, dst_edb)
    except Exception as e:
        # Clean up a half-copied clone.
        for p in (dst_elk,):
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass
        if os.path.isdir(dst_edb):
            shutil.rmtree(dst_edb, ignore_errors=True)
        return {"success": False,
                "error": f"Copy failed (is the template open in EPLAN?): {e}"}

    result = {
        "success": True,
        "project": dst_elk,
        "template": src_elk,
        "note": "Disposable clone - discard with scratch_project_discard when done.",
    }
    if open_after:
        from .project import open_project
        result["open"] = open_project(dst_elk)
        result["success"] = result["open"].get("success", False)
    return result


def scratch_project_discard(project_path: str, close_first: bool = True) -> dict:
    """
    Delete a scratch project created by scratch_project_create.

    Refuses to touch anything outside the scratch root, so this can never
    delete a real project. Closes the focused project first if it is the one
    being discarded (best effort - if a different project has focus in EPLAN,
    close it manually before discarding).

    Args:
        project_path: Path to the scratch project's .elk file.
        close_first: Close the project in EPLAN before deleting (default True).
    """
    elk, edb, _ = _resolve_project_parts(project_path)
    if not _inside_scratch(elk):
        return {"success": False,
                "error": f"Refusing to delete outside the scratch root "
                         f"({SCRATCH_ROOT}): {elk}"}

    closed = None
    if close_first:
        manager, error = _get_connected_manager()
        if not error:
            from .project import get_current_project, close_project
            cur = get_current_project()
            focused = (cur.get("parameters") or {}).get("PROJECT", "")
            if focused and os.path.normcase(os.path.abspath(focused)) == os.path.normcase(elk):
                closed = close_project()

    errors = []
    for path, remover in ((elk, os.remove), (edb, lambda p: shutil.rmtree(p))):
        if os.path.exists(path):
            try:
                remover(path)
            except Exception as e:
                errors.append(f"{path}: {e}")

    result = {"success": not errors, "deleted": elk, "closed_in_eplan": closed}
    if errors:
        result["error"] = ("Delete failed (project still open/locked in EPLAN?): "
                           + "; ".join(errors))
    return result


def scratch_project_list() -> dict:
    """
    List scratch projects currently in the scratch area.

    Useful for cleaning up leftovers from crashed or interrupted test runs
    (discard them with scratch_project_discard).
    """
    if not os.path.isdir(SCRATCH_ROOT):
        return {"success": True, "scratch_root": SCRATCH_ROOT, "projects": []}
    projects = []
    for entry in sorted(os.listdir(SCRATCH_ROOT)):
        if entry.lower().endswith(".elk"):
            full = os.path.join(SCRATCH_ROOT, entry)
            projects.append({
                "project": full,
                "modified": time.strftime("%Y-%m-%d %H:%M:%S",
                                          time.localtime(os.path.getmtime(full))),
            })
    return {"success": True, "scratch_root": SCRATCH_ROOT, "projects": projects}
