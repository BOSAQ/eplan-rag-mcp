"""
E3D / 3D layout space actions.

These reach the EPLAN DataModel.E3D object model through runtime reflection
because the script engine only compiles Base/ApplicationFramework (direct
references to DataModel/HEServices fail with CS0234). The techniques used here
were proven interactively (see the eplan-development skill reference
`e3d-installation-spaces.md`):

- create_installation_space: headless InstallationSpace.Create
- insert_3d_macro: headless Insert3D.WindowMacro (fileName overload)
"""

import uuid

from ._base import cs_escape
from .scripted import _execute_script


def create_installation_space(space_name: str) -> dict:
    """
    Create a 3D installation (layout) space in the current project, headless.

    Runs InstallationSpace.Create(project, name) inside a LockingStep via
    runtime reflection, so no EPLAN dialog is shown. If a space with the same
    VisibleName already exists it is left untouched.

    Args:
        space_name: VisibleName for the new space.

    Returns:
        dict with created flag, createdName and existing space list.
    """
    if not space_name:
        return {"success": False, "error": "space_name is required"}
    if '"' in space_name or "\r" in space_name or "\n" in space_name:
        return {"success": False, "error": "space_name must be a single line without double quotes"}

    body = r"""
using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Collections.Generic;
using Eplan.EplApi.ApplicationFramework;
using Eplan.EplApi.Base;
using Eplan.EplApi.Scripting;

public class CreateSpace_%EXECID%
{
    private static List<string> Chain(Exception ex)
    {
        var c = new List<string>();
        Exception cur = ex;
        while (cur != null) { c.Add(cur.GetType().Name + ": " + cur.Message); cur = cur.InnerException; }
        return c;
    }

    [Start]
    public void Run()
    {
        var results = new Dictionary<string, object>();
        try
        {
            var dm = Assembly.Load("Eplan.EplApi.DataModelu");
            var he = Assembly.Load("Eplan.EplApi.HEServicesu");

            var lsType = dm.GetType("Eplan.EplApi.DataModel.LockingStep");
            object lockStep = Activator.CreateInstance(lsType);
            try
            {
                var ssType = he.GetType("Eplan.EplApi.HEServices.SelectionSet");
                object ss = Activator.CreateInstance(ssType);
                var getCur = ssType.GetMethod("GetCurrentProject", new Type[] { typeof(bool) });
                object project = getCur.Invoke(ss, new object[] { false });
                if (project == null)
                {
                    results["success"] = false;
                    results["error"] = "no current project via SelectionSet (inside LockingStep)";
                }
                else
                {
                    results["project"] = (string)project.GetType().GetProperty("ProjectFullName").GetValue(project, null);

                    var existing = new List<string>();
                    var isProp = project.GetType().GetProperty("InstallationSpaces");
                    if (isProp != null)
                    {
                        var spaces = (System.Collections.IEnumerable)isProp.GetValue(project, null);
                        foreach (var s in spaces)
                        {
                            existing.Add((string)s.GetType().GetProperty("VisibleName").GetValue(s, null));
                        }
                    }
                    results["existing"] = existing;
                    results["existsAlready"] = existing.Contains("%NAME%");

                    if (existing.Contains("%NAME%"))
                    {
                        results["created"] = false;
                        results["note"] = "already exists";
                    }
                    else
                    {
                        var isType = dm.GetType("Eplan.EplApi.DataModel.E3D.InstallationSpace");
                        var staticCreate = isType.GetMethods(BindingFlags.Public | BindingFlags.Static)
                            .FirstOrDefault(m => m.Name == "Create" && m.GetParameters().Length == 3);
                        if (staticCreate != null)
                        {
                            try
                            {
                                object space = staticCreate.Invoke(null, new object[] { project, "%NAME%", null });
                                results["created"] = true;
                                results["createdName"] = (string)space.GetType().GetProperty("VisibleName").GetValue(space, null);
                                results["via"] = "InstallationSpace.Create static";
                            }
                            catch (Exception ex)
                            {
                                results["created"] = false;
                                results["createError"] = string.Join(" <- ", Chain(ex));
                            }
                        }
                        else
                        {
                            results["created"] = false;
                            results["createError"] = "no static Create(3) found";
                        }
                    }
                    results["success"] = true;
                }
            }
            catch (Exception ex)
            {
                results["success"] = false;
                results["error"] = string.Join(" <- ", Chain(ex));
                results["errorType"] = ex.GetType().FullName;
            }
            finally
            {
                try { lsType.GetMethod("Dispose").Invoke(lockStep, null); } catch { }
            }
        }
        catch (Exception ex)
        {
            results["success"] = false;
            results["error"] = string.Join(" <- ", Chain(ex));
            results["errorType"] = ex.GetType().FullName;
        }
        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results, Newtonsoft.Json.Formatting.Indented);
        File.WriteAllText(@"{{RESULT_PATH}}", json);
    }
}
"""
    body = body.replace("%EXECID%", uuid.uuid4().hex[:8])
    body = body.replace("%NAME%", cs_escape(space_name))

    return _execute_script(body)


def insert_3d_macro(space_name: str, macro_path: str, variant: int = 0) -> dict:
    """
    Insert a 3D window macro into an installation space, headless.

    Uses Insert3D.WindowMacro(fileName, variant, parentSpace, identityMatrix,
    MoveKind=0, NumerationMode=0) via runtime reflection. The parent space is
    located by VisibleName inside the current project; nothing is shown.

    Args:
        space_name: VisibleName of the target installation space.
        macro_path: Absolute path to the .ema window macro.
        variant: Macro variant to insert (default 0).

    Returns:
        dict with spaceFound, placedVia and pluralCount.
    """
    if not space_name:
        return {"success": False, "error": "space_name is required"}
    if not macro_path:
        return {"success": False, "error": "macro_path is required"}

    body = r"""
using System;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Collections.Generic;
using Eplan.EplApi.ApplicationFramework;
using Eplan.EplApi.Base;
using Eplan.EplApi.Scripting;

public class InsertMacro_%EXECID%
{
    private static List<string> Chain(Exception ex)
    {
        var c = new List<string>();
        Exception cur = ex;
        while (cur != null) { c.Add(cur.GetType().Name + ": " + cur.Message); cur = cur.InnerException; }
        return c;
    }

    [Start]
    public void Run()
    {
        var results = new Dictionary<string, object>();
        try
        {
            var dm = Assembly.Load("Eplan.EplApi.DataModelu");
            var he = Assembly.Load("Eplan.EplApi.HEServicesu");

            var lsType = dm.GetType("Eplan.EplApi.DataModel.LockingStep");
            object lockStep = Activator.CreateInstance(lsType);
            try
            {
                var ssType = he.GetType("Eplan.EplApi.HEServices.SelectionSet");
                object ss = Activator.CreateInstance(ssType);
                object project = ssType.GetMethod("GetCurrentProject", new Type[] { typeof(bool) })
                    .Invoke(ss, new object[] { false });
                if (project == null) { results["success"] = false; results["error"] = "no project"; return; }
                results["project"] = (string)project.GetType().GetProperty("ProjectFullName").GetValue(project, null);

                Type isType = dm.GetType("Eplan.EplApi.DataModel.E3D.InstallationSpace");
                object space = null;
                var rawSpaces = project.GetType().GetProperty("InstallationSpaces").GetValue(project, null);
                foreach (var s in (System.Collections.IEnumerable)rawSpaces)
                {
                    if ((string)s.GetType().GetProperty("VisibleName").GetValue(s, null) == "%SPACE%")
                    { space = s; break; }
                }
                if (space == null) { results["success"] = false; results["error"] = "space not found: %SPACE%"; return; }
                results["spaceFound"] = true;

                Type insertType = he.GetType("Eplan.EplApi.HEServices.Insert3D");
                object insert = Activator.CreateInstance(insertType);
                var candidates = insertType.GetMethods(BindingFlags.Public | BindingFlags.Instance)
                    .Where(m => m.Name == "WindowMacro" && m.GetParameters().Length == 6)
                    .ToList();
                if (candidates.Count == 0) { results["success"] = false; results["error"] = "no 6-param WindowMacro"; return; }

                MethodInfo wanted = null;
                foreach (var m in candidates)
                {
                    var p0 = m.GetParameters()[0];
                    if (p0.ParameterType == typeof(string)) { wanted = m; break; }
                }
                if (wanted == null) { results["success"] = false; results["error"] = "no fileName overload"; return; }

                var ps = wanted.GetParameters();
                var args = new object[ps.Length];
                args[0] = "%MACRO%";
                foreach (var p in ps)
                {
                    if (p.Name == "oParent") { args[p.Position] = space; }
                    else if (p.Name == "oMatrix")
                    {
                        args[p.Position] = Activator.CreateInstance(p.ParameterType);
                    }
                    else if (p.Name == "nMoveCondition" || p.Name == "nNumerationMode")
                    {
                        args[p.Position] = Enum.ToObject(p.ParameterType, 0);
                    }
                    else if (p.Name == "strFileName") { args[p.Position] = "%MACRO%"; }
                    else if (p.Name == "nVariant") { args[p.Position] = %VARIANT%; }
                    else if (p.Name == "dRotationAngle" || p.Name == "dX" || p.Name == "dY" || p.Name == "dZ")
                    {
                        if (args[p.Position] == null) args[p.Position] = 0.0;
                    }
                }

                results["withArgs"] = string.Join(", ", ps.Select(p => p.Name + ":" + p.ParameterType.Name));
                try
                {
                    object placed = wanted.Invoke(insert, args);
                    results["success"] = true;
                    results["placedVia"] = "Insert3D.WindowMacro(fileName,...)";
                    if (placed is System.Collections.IEnumerable)
                    {
                        int n = 0;
                        foreach (var o in (System.Collections.IEnumerable)placed) n++;
                        results["pluralCount"] = n;
                    }
                }
                catch (Exception ex)
                {
                    results["success"] = false;
                    results["insertError"] = string.Join(" <- ", Chain(ex));
                }
            }
            finally
            {
                try { lsType.GetMethod("Dispose").Invoke(lockStep, null); } catch { }
            }
        }
        catch (Exception ex)
        {
            results["success"] = false;
            results["error"] = string.Join(" <- ", Chain(ex));
            results["errorType"] = ex.GetType().FullName;
        }
        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results, Newtonsoft.Json.Formatting.Indented);
        File.WriteAllText(@"{{RESULT_PATH}}", json);
    }
}
"""
    body = body.replace("%EXECID%", uuid.uuid4().hex[:8])
    body = body.replace("%SPACE%", cs_escape(space_name))
    body = body.replace("%MACRO%", cs_escape(macro_path))
    body = body.replace("%VARIANT%", str(int(variant)))

    return _execute_script(body)
