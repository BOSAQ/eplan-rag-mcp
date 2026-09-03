"""
Scripted actions - Uses dynamically generated C# scripts for advanced EPLAN APIs.

These actions access internal EPLAN APIs that aren't available via standard actions:
- MDPartsManagement: Direct parts database access
- Settings: Typed settings (string, bool, int) with direct API
- PathMap: Variable substitution

EVERY generated script here must be valid **C# 5**. On EPLAN 2026 the
script engine compiles with a pre-C# 6 compiler, so all of these are
syntax errors (2027's engine is newer - it accepts at least the
dictionary index initializer - but these tools target both, so write
to the older floor):
    ?.  ?[]   null-conditional         -> use an explicit null check
    $"..."      string interpolation   -> use string.Format / concatenation
    { ["k"] = v }  dictionary index initializer -> assign after construction
    nameof(x), expression-bodied members, auto-property initializers
A compile error is invisible from here: ExecuteScript still reports success,
the script never runs, and the only symptom is that the result file never
appears - i.e. it looks exactly like a hung EPLAN. `_execute_script` now
reads EPLAN's message tree on timeout and reports the real CS#### error;
see `_compile_errors_for`.
"""

import os
import re
import json
import time
import uuid
from typing import List
from ._base import _get_connected_manager, cs_escape

# A value used as a C# member/identifier (not inside a string literal) cannot
# be escaped safely - it must be a real identifier. Reject anything else to
# close the injection surface for filter_property etc.
_CS_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _identifier_error(value: str, what: str) -> dict:
    return {
        "success": False,
        "error": f"Invalid {what}: {value!r}. Must be a valid identifier "
                 f"(letters, digits, underscore; not starting with a digit).",
    }

# Locate the mcp_server root (the directory containing eplan_connection.py) so
# that generated scripts and results share a single location with
# eplan_connection.py's QuietMode wrapper, instead of a per-API-version folder.
_MCP_ROOT = os.path.dirname(os.path.abspath(__file__))
while not os.path.exists(os.path.join(_MCP_ROOT, "eplan_connection.py")):
    _parent = os.path.dirname(_MCP_ROOT)
    if _parent == _MCP_ROOT:
        break
    _MCP_ROOT = _parent

# Directory for generated scripts and results (shared, under mcp_server/scripts)
SCRIPT_DIR = os.path.join(_MCP_ROOT, "scripts", "generated")
RESULTS_DIR = os.path.join(_MCP_ROOT, "scripts", "results")


def _ensure_dirs():
    """Ensure script and results directories exist."""
    os.makedirs(SCRIPT_DIR, exist_ok=True)
    os.makedirs(RESULTS_DIR, exist_ok=True)


# Friendly names callers reasonably reach for, mapped to the real parts-DB
# property. Anything not listed is passed through untouched, so raw
# ARTICLE_* names and MDPart members ("PartNr") keep working.
_PARTS_PROP_ALIASES = {
    "Description1": "ARTICLE_DESCR1",
    "Description2": "ARTICLE_DESCR2",
    "Description3": "ARTICLE_DESCR3",
    "Descr1": "ARTICLE_DESCR1",
    "Manufacturer": "ARTICLE_MANUFACTURER",
    "ManufacturerName": "ARTICLE_MANUFACTURER_NAME",
    "Supplier": "ARTICLE_SUPPLIER",
    "OrderNr": "ARTICLE_ORDERNR",
    "PartNumber": "ARTICLE_PARTNR",
    "ERPNr": "ARTICLE_ERPNR",
}


def _resolve_prop_name(name: str) -> str:
    """Map a friendly property name to its parts-DB name; pass others through."""
    return _PARTS_PROP_ALIASES.get(name, name)


# C# 5 helpers for reading/writing parts-database properties by NAME, shared
# by every parts_db_* script below. Interpolated into f-string templates, so
# the braces here are single (interpolated values are not re-scanned).
_PARTS_PROP_HELPERS_CS = """
    // Each ARTICLE_* member is declared TWICE on
    // MDPartsDatabaseItemPropertyList - once parameterless, once taking an
    // int index (for multi-value properties like ARTICLE_CUSTOM_DATA_VALUE).
    // A plain GetProperty(name) therefore throws AmbiguousMatchException for
    // every property, so ask for the non-indexed overload explicitly.
    static System.Reflection.PropertyInfo FindProp(object propList, string name)
    {
        return propList.GetType().GetProperty(
            name,
            System.Reflection.BindingFlags.Public | System.Reflection.BindingFlags.Instance,
            null, null, Type.EmptyTypes, null);
    }

    static bool ReadProp(object propList, string name, out string value)
    {
        value = "";
        var pi = FindProp(propList, name);
        if (pi == null) return false;
        var v = pi.GetValue(propList, null);
        value = v == null ? "" : v.ToString();
        return true;
    }

    // Returns null on success, else why it failed. The setter takes an
    // MDPropertyValue and MDPropertyValue has only a default constructor -
    // the string -> MDPropertyValue conversion that works in source is
    // compile-time only, invisible to SetValue, which would throw
    // ArgumentException on a bare string.
    static string WriteProp(object propList, string name, string value)
    {
        var pi = FindProp(propList, name);
        if (pi == null) return "property not found";
        var pv = new MDPropertyValue();
        pv.Set(value);
        pi.SetValue(propList, pv, null);
        return null;
    }
"""


# Reading EPLAN's message tree to explain a timeout itself runs a script.
# This flag keeps that diagnostic from recursing if IT times out too.
_collecting_diagnostics = False


def _compile_errors_for(script_path: str) -> list:
    """
    Ask EPLAN why a script produced no result file.

    EPLAN's script engine reports compile failures only to its own
    system-message tree: the remote ExecuteScript call still returns success
    in well under a second, the script never runs, and the sole symptom here
    is the result file never appearing. So on timeout, go read the tree.

    Returns the messages EPLAN logged against this script file (its
    "compile errors in <file>:" header, the CS#### lines, and the
    "<file> cannot be compiled" footer), oldest first - or [] if EPLAN
    logged none, which means a genuine timeout: the script compiled and is
    still running, or it died without writing its result.
    """
    global _collecting_diagnostics
    if _collecting_diagnostics:
        return []
    _collecting_diagnostics = True
    try:
        res = get_system_messages(min_level="Error", max_messages=200)
        if not res.get("success"):
            return []
        # The generated file name carries a per-execution uuid, so matching on
        # it cannot pick up messages from an earlier script.
        basename = os.path.basename(script_path)
        texts = [m.get("text", "") for m in res.get("messages") or []]
        marked = [i for i, t in enumerate(texts) if basename in t]
        if not marked:
            return []
        return texts[marked[0]:marked[-1] + 1]
    except Exception:
        # A diagnostic must never turn a timeout into a different failure.
        return []
    finally:
        _collecting_diagnostics = False


def _execute_script(script_content: str, timeout: float = 30.0) -> dict:
    """
    Execute a C# script in EPLAN and return results.

    Args:
        script_content: The C# script code
        timeout: Max seconds to wait for results

    Returns:
        dict with success status and results/error
    """
    manager, error = _get_connected_manager()
    if error:
        return error

    _ensure_dirs()

    # Generate unique IDs for this execution
    exec_id = str(uuid.uuid4())[:8]
    script_path = os.path.join(SCRIPT_DIR, f"script_{exec_id}.cs")
    result_path = os.path.join(RESULTS_DIR, f"result_{exec_id}.json")

    # Inject result path into script
    script_with_path = script_content.replace(
        "{{RESULT_PATH}}", result_path.replace("\\", "\\\\")
    )

    try:
        # Write script
        with open(script_path, "w", encoding="utf-8") as f:
            f.write(script_with_path)

        # Execute only - deliberately NOT RegisterScript.
        #
        # RegisterScript installs a script's PERSISTENT hooks
        # ([DeclareAction] / [DeclareEventHandler] / [DeclareMenu]). Every
        # script generated here has only a [Start] method, which ExecuteScript
        # compiles and runs on its own, so registering it accomplishes nothing
        # and EPLAN complains "The script does not contain attributes for
        # loading." - once per script call.
        #
        # That error surfaces in EPLAN's own UI, NOT in the remote-API result:
        # the RegisterScript call still returns success in ~0.45s, which is why
        # it went unnoticed for so long. Field-confirmed on EPLAN 2027.
        #
        # Dropping Register + the matching Unregister also removes two
        # remote-API round-trips per script: measured on 2027, the median
        # execute_custom_script call goes 0.39s -> 0.22s.
        exec_result = manager.execute_action(
            f'ExecuteScript /ScriptFile:"{script_path}"'
        )
        if not exec_result.get("success"):
            return {
                "success": False,
                "message": f"Failed to execute script: {exec_result.get('message')}",
            }

        # Wait for results file
        start_time = time.time()
        while not os.path.exists(result_path):
            if time.time() - start_time > timeout:
                # Far and away the most common cause is a C# compile error,
                # which EPLAN reports to its own message tree and not to us.
                # Say so, instead of blaming a timeout EPLAN never had.
                compile_errors = _compile_errors_for(script_path)
                if compile_errors:
                    cs_lines = [e for e in compile_errors if e.startswith("CS")]
                    return {
                        "success": False,
                        "message": "Script did not compile: "
                                   + " | ".join(cs_lines or compile_errors),
                        "compile_errors": compile_errors,
                    }
                return {
                    "success": False,
                    "message": f"Timeout waiting for script results after "
                               f"{timeout:g}s (EPLAN logged no compile error "
                               f"for this script, so it compiled and either is "
                               f"still running or died without writing)",
                }
            time.sleep(0.1)

        # Small delay to ensure file is fully written
        time.sleep(0.1)

        # Read results
        with open(result_path, "r", encoding="utf-8") as f:
            results = json.load(f)

        return {"success": True, "results": results}

    except Exception as e:
        return {"success": False, "message": str(e)}
    finally:
        # Cleanup - no UnregisterScript, since nothing was registered (see above).
        try:
            if os.path.exists(script_path):
                os.remove(script_path)
            if os.path.exists(result_path):
                os.remove(result_path)
        except OSError:
            pass


# =============================================================================
# PARTS DATABASE (MDPartsManagement)
# =============================================================================


def parts_db_query(
    filter_property: str = None,
    filter_value: str = None,
    return_properties: List[str] = None,
    limit: int = 100,
) -> dict:
    """
    Query parts from the EPLAN parts database.

    Uses MDPartsManagement API for direct database access.

    Args:
        filter_property: A member of MDPart to filter on - "PartNr",
            "Variant", "ProductGroup", "ProductSubGroup". (The ARTICLE_*
            fields live on the property list, not on MDPart, and cannot be
            filtered on here.)
        filter_value: Substring to match, case-sensitive
        return_properties: Properties to return. Accepts raw parts-DB names
            ("ARTICLE_DESCR1"), MDPart members ("PartNr"), or the friendly
            aliases in _PARTS_PROP_ALIASES ("Description1", "Manufacturer").
            Default: part number, description 1, manufacturer, product
            group, product subgroup. Names are resolved before the query and
            the RESOLVED name is the key in each returned part.
        limit: Maximum number of parts to return

    Returns:
        dict with parts list and count. Any requested property that exists on
        neither the property list nor MDPart comes back as "" and is named in
        "unknownProperties".
    """
    # limit is interpolated into the C# source outside any string literal, so
    # it must be a real integer - anything else would be code injection.
    try:
        limit = int(limit)
    except (TypeError, ValueError):
        return {"success": False, "error": f"Invalid limit: {limit!r}. Must be an integer."}

    if return_properties is None:
        return_properties = [
            "PartNr",
            "ARTICLE_DESCR1",
            "ARTICLE_MANUFACTURER",
            "ProductGroup",
            "ProductSubGroup",
        ]

    props_array = ", ".join(
        [f'"{cs_escape(_resolve_prop_name(p))}"' for p in return_properties]
    )

    filter_code = ""
    if filter_property and filter_value:
        if not _CS_IDENTIFIER.match(filter_property):
            return _identifier_error(filter_property, "filter_property")
        filter_code = f'''
                    .Where(p => Convert.ToString(p.{filter_property}).Contains("{cs_escape(filter_value)}"))'''

    script = f"""using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using Eplan.EplApi.MasterData;
using Eplan.EplApi.Scripting;

public class PartsQuery_{uuid.uuid4().hex[:6]}
{{
{_PARTS_PROP_HELPERS_CS}
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();
        var partsList = new List<Dictionary<string, object>>();
        var unknownProps = new List<string>();

        try
        {{
            var mdParts = new MDPartsManagement();
            using (var db = mdParts.OpenDatabase())
            {{
                var parts = db.Parts{filter_code}
                    .Take({limit})
                    .ToList();

                string[] propsToGet = new string[] {{ {props_array} }};

                foreach (var part in parts)
                {{
                    var partDict = new Dictionary<string, object>();
                    foreach (var propName in propsToGet)
                    {{
                        // MDPart's own members (PartNr, ProductGroup...) are
                        // not on the property list, so try both places.
                        string val;
                        if (ReadProp(part.Properties, propName, out val)
                            || ReadProp(part, propName, out val))
                        {{
                            partDict[propName] = val;
                        }}
                        else
                        {{
                            partDict[propName] = "";
                            if (!unknownProps.Contains(propName)) unknownProps.Add(propName);
                        }}
                    }}
                    partsList.Add(partDict);
                }}

                results["success"] = true;
                results["count"] = partsList.Count;
                results["parts"] = partsList;
                if (unknownProps.Count > 0) results["unknownProperties"] = unknownProps;
            }}
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}

        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results, Newtonsoft.Json.Formatting.Indented);
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", json);
    }}
}}
"""
    return _execute_script(script)


def parts_db_count(filter_property: str = None, filter_value: str = None) -> dict:
    """
    Count parts in the EPLAN parts database.

    Args:
        filter_property: A member of MDPart to filter on - "PartNr",
            "Variant", "ProductGroup", "ProductSubGroup" (see
            parts_db_query; ARTICLE_* fields cannot be filtered on here)
        filter_value: Substring to match, case-sensitive

    Returns:
        dict with count
    """
    filter_code = ""
    if filter_property and filter_value:
        if not _CS_IDENTIFIER.match(filter_property):
            return _identifier_error(filter_property, "filter_property")
        # Convert.ToString() rather than a null-conditional chain: it yields
        # "" for null, and works for the value-type members too.
        filter_code = f'.Where(p => Convert.ToString(p.{filter_property}).Contains("{cs_escape(filter_value)}"))'

    script = f"""using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using Eplan.EplApi.MasterData;
using Eplan.EplApi.Scripting;

public class PartsCount_{uuid.uuid4().hex[:6]}
{{
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();

        try
        {{
            var mdParts = new MDPartsManagement();
            using (var db = mdParts.OpenDatabase())
            {{
                int count = db.Parts{filter_code}.Count();
                results["success"] = true;
                results["count"] = count;
            }}
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}

        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results);
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", json);
    }}
}}
"""
    return _execute_script(script)


def parts_db_get_part(part_number: str) -> dict:
    """
    Get detailed information about a specific part.

    Args:
        part_number: The part number to look up

    Returns:
        dict with part details
    """
    part_number_cs = cs_escape(part_number)
    script = f'''using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using Eplan.EplApi.MasterData;
using Eplan.EplApi.Scripting;

public class PartsGet_{uuid.uuid4().hex[:6]}
{{
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();

        try
        {{
            var mdParts = new MDPartsManagement();
            using (var db = mdParts.OpenDatabase())
            {{
                var part = db.Parts.FirstOrDefault(p => p.PartNr == "{part_number_cs}");

                if (part != null)
                {{
                    // Plain assignment, not a dictionary index initializer:
                    // that syntax is C# 6, and on 2026 it is a hard
                    // CS1525 (2027 accepts it - write to the older floor).
                    var props = part.Properties;
                    var partDict = new Dictionary<string, object>();
                    partDict["PartNr"] = props.ARTICLE_PARTNR.ToString();
                    partDict["Description1"] = props.ARTICLE_DESCR1.ToString();
                    partDict["Description2"] = props.ARTICLE_DESCR2.ToString();
                    partDict["Description3"] = props.ARTICLE_DESCR3.ToString();
                    partDict["Manufacturer"] = props.ARTICLE_MANUFACTURER.ToString();
                    partDict["Supplier"] = props.ARTICLE_SUPPLIER.ToString();
                    partDict["OrderNr"] = props.ARTICLE_ORDERNR.ToString();
                    partDict["ProductGroup"] = part.ProductGroup.ToString();
                    partDict["ProductSubGroup"] = part.ProductSubGroup.ToString();
                    // GenericProductGroup, not ProductTopGroup: that is the
                    // name of the enum TYPE, and MDPart has no member by it.
                    // Reflection over MDPart on 2026 lists exactly three
                    // group members - ProductGroup, ProductSubGroup and
                    // GenericProductGroup (whose type is ProductTopGroup).
                    // Getting this wrong is CS1061, i.e. another silent
                    // timeout.
                    partDict["ProductTopGroup"] = part.GenericProductGroup.ToString();

                    results["success"] = true;
                    results["found"] = true;
                    results["part"] = partDict;
                }}
                else
                {{
                    results["success"] = true;
                    results["found"] = false;
                }}
            }}
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}

        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results, Newtonsoft.Json.Formatting.Indented);
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", json);
    }}
}}
'''
    return _execute_script(script)


def parts_db_create(part_number: str, properties: dict = None) -> dict:
    """
    Create a new part in the parts database and optionally set properties.

    Uses MDPartsDatabase.AddPart (verified in the P8 docs; throws if the
    part already exists — this function reports that as an error instead of
    silently updating; use parts_db_update for existing parts).

    Args:
        part_number: Part number of the new part (must not exist yet)
        properties: Optional dict of parts-DB property names to string
            values, e.g. {"ARTICLE_MANUFACTURER": "Siemens",
            "ARTICLE_DESCR1": "Circuit breaker"}. The _PARTS_PROP_ALIASES
            friendly names ("Manufacturer", "Description1") work too.

    Returns:
        dict with success status, "propertiesSet", and "propertiesFailed" -
        the latter naming each property that could not be written and why.
    """
    part_number_cs = cs_escape(part_number)
    # Property names/values go into parallel C# string arrays (each element
    # cs_escape'd) and are applied at runtime via reflection with a single
    # loop variable - never minted into C# identifiers, so an arbitrary
    # property name cannot break or inject into the script.
    items = [(_resolve_prop_name(n), v) for n, v in (properties or {}).items()]
    names_array = ", ".join(f'"{cs_escape(name)}"' for name, _ in items)
    values_array = ", ".join(f'"{cs_escape(value)}"' for _, value in items)

    script = f'''using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using Eplan.EplApi.MasterData;
using Eplan.EplApi.Scripting;

public class PartsCreate_{uuid.uuid4().hex[:6]}
{{
{_PARTS_PROP_HELPERS_CS}
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();
        var setProps = new List<string>();
        var failedProps = new List<string>();
        string[] propNames = new string[] {{ {names_array} }};
        string[] propValues = new string[] {{ {values_array} }};

        try
        {{
            var mdParts = new MDPartsManagement();
            using (var db = mdParts.OpenDatabase())
            {{
                var existing = db.Parts.FirstOrDefault(p => p.PartNr == "{part_number_cs}");
                if (existing != null)
                {{
                    results["success"] = false;
                    results["error"] = "Part already exists: {part_number_cs} (use parts_db_update)";
                }}
                else
                {{
                    var part = db.AddPart("{part_number_cs}");
                    for (int i = 0; i < propNames.Length; i++)
                    {{
                        try
                        {{
                            string why = WriteProp(part.Properties, propNames[i], propValues[i]);
                            if (why == null)
                            {{
                                setProps.Add(propNames[i]);
                            }}
                            else
                            {{
                                failedProps.Add(propNames[i] + ": " + why);
                            }}
                        }}
                        catch (Exception ep)
                        {{
                            failedProps.Add(propNames[i] + ": " + ep.Message);
                        }}
                    }}
                    results["success"] = true;
                    results["created"] = "{part_number_cs}";
                    results["propertiesSet"] = setProps;
                    if (failedProps.Count > 0) results["propertiesFailed"] = failedProps;
                }}
            }}
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}

        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results, Newtonsoft.Json.Formatting.Indented);
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", json);
    }}
}}
'''
    return _execute_script(script)


def parts_db_update(part_number: str, property_name: str, property_value: str) -> dict:
    """
    Update a property on a part in the database.

    Args:
        part_number: The part number to update
        property_name: Property to update - a raw parts-DB name
            ("ARTICLE_DESCR1") or one of the _PARTS_PROP_ALIASES
            ("Description1", "Manufacturer")
        property_value: New value

    Returns:
        dict with success status and, on success, "value": the property read
        straight back out of the database. Writes take effect immediately -
        there is no separate save/commit step.
    """
    part_number_cs = cs_escape(part_number)
    property_name_cs = cs_escape(_resolve_prop_name(property_name))
    property_value_cs = cs_escape(property_value)
    script = f'''using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using Eplan.EplApi.MasterData;
using Eplan.EplApi.Scripting;

public class PartsUpdate_{uuid.uuid4().hex[:6]}
{{
{_PARTS_PROP_HELPERS_CS}
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();

        try
        {{
            var mdParts = new MDPartsManagement();
            using (var db = mdParts.OpenDatabase())
            {{
                var part = db.Parts.FirstOrDefault(p => p.PartNr == "{part_number_cs}");

                if (part != null)
                {{
                    string why = WriteProp(part.Properties, "{property_name_cs}", "{property_value_cs}");
                    if (why == null)
                    {{
                        string readBack;
                        ReadProp(part.Properties, "{property_name_cs}", out readBack);
                        results["success"] = true;
                        results["updated"] = true;
                        results["value"] = readBack;
                    }}
                    else
                    {{
                        results["success"] = false;
                        results["error"] = "{property_name_cs}: " + why;
                    }}
                }}
                else
                {{
                    results["success"] = false;
                    results["error"] = "Part not found: {part_number_cs}";
                }}
            }}
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}

        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results);
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", json);
    }}
}}
'''
    return _execute_script(script)


def parts_db_list_product_groups() -> dict:
    """
    List all product groups and subgroups in the parts database.

    Returns:
        dict with product groups
    """
    script = f"""using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using Eplan.EplApi.MasterData;
using Eplan.EplApi.Scripting;

public class PartsGroups_{uuid.uuid4().hex[:6]}
{{
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();

        try
        {{
            var groups = Enum.GetNames(typeof(MDPartsDatabaseItem.Enums.ProductGroup)).ToList();
            var subGroups = Enum.GetNames(typeof(MDPartsDatabaseItem.Enums.ProductSubGroup)).ToList();
            var topGroups = Enum.GetNames(typeof(MDPartsDatabaseItem.Enums.ProductTopGroup)).ToList();

            results["success"] = true;
            results["productGroups"] = groups;
            results["productSubGroups"] = subGroups;
            results["productTopGroups"] = topGroups;
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}

        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results, Newtonsoft.Json.Formatting.Indented);
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", json);
    }}
}}
"""
    return _execute_script(script)


# =============================================================================
# SETTINGS API (Direct typed access)
# =============================================================================


def settings_get_string(setting_path: str, index: int = 0) -> dict:
    """
    Get a string setting from EPLAN.

    Args:
        setting_path: Full setting path (e.g., "USER.TrDMProject.UserData.Longname")
        index: Setting index (default 0)

    Returns:
        dict with setting value
    """
    script = f'''using System;
using System.IO;
using System.Collections.Generic;
using Eplan.EplApi.Base;
using Eplan.EplApi.Scripting;

public class SettingsGetStr_{uuid.uuid4().hex[:6]}
{{
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();

        try
        {{
            var settings = new Settings();
            string value = settings.GetStringSetting("{cs_escape(setting_path)}", {int(index)});
            results["success"] = true;
            results["value"] = value;
            results["type"] = "string";
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}

        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results);
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", json);
    }}
}}
'''
    return _execute_script(script)


def settings_set_string(setting_path: str, value: str, index: int = 0) -> dict:
    """
    Set a string setting in EPLAN.

    Args:
        setting_path: Full setting path
        value: Value to set
        index: Setting index (default 0)

    Returns:
        dict with success status
    """
    script = f'''using System;
using System.IO;
using System.Collections.Generic;
using Eplan.EplApi.Base;
using Eplan.EplApi.Scripting;

public class SettingsSetStr_{uuid.uuid4().hex[:6]}
{{
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();

        try
        {{
            var settings = new Settings();
            settings.SetStringSetting("{cs_escape(setting_path)}", "{cs_escape(value)}", {int(index)});
            results["success"] = true;
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}

        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results);
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", json);
    }}
}}
'''
    return _execute_script(script)


def settings_get_bool(setting_path: str, index: int = 0) -> dict:
    """
    Get a boolean setting from EPLAN.

    Args:
        setting_path: Full setting path
        index: Setting index (default 0)

    Returns:
        dict with setting value
    """
    script = f'''using System;
using System.IO;
using System.Collections.Generic;
using Eplan.EplApi.Base;
using Eplan.EplApi.Scripting;

public class SettingsGetBool_{uuid.uuid4().hex[:6]}
{{
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();

        try
        {{
            var settings = new Settings();
            bool value = settings.GetBoolSetting("{cs_escape(setting_path)}", {int(index)});
            results["success"] = true;
            results["value"] = value;
            results["type"] = "bool";
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}

        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results);
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", json);
    }}
}}
'''
    return _execute_script(script)


def settings_set_bool(setting_path: str, value: bool, index: int = 0) -> dict:
    """
    Set a boolean setting in EPLAN.

    Args:
        setting_path: Full setting path
        value: Value to set
        index: Setting index (default 0)

    Returns:
        dict with success status
    """
    value_str = "true" if value else "false"
    script = f'''using System;
using System.IO;
using System.Collections.Generic;
using Eplan.EplApi.Base;
using Eplan.EplApi.Scripting;

public class SettingsSetBool_{uuid.uuid4().hex[:6]}
{{
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();

        try
        {{
            var settings = new Settings();
            settings.SetBoolSetting("{cs_escape(setting_path)}", {value_str}, {int(index)});
            results["success"] = true;
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}

        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results);
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", json);
    }}
}}
'''
    return _execute_script(script)


def settings_get_int(setting_path: str, index: int = 0) -> dict:
    """
    Get an integer setting from EPLAN.

    Args:
        setting_path: Full setting path
        index: Setting index (default 0)

    Returns:
        dict with setting value
    """
    script = f'''using System;
using System.IO;
using System.Collections.Generic;
using Eplan.EplApi.Base;
using Eplan.EplApi.Scripting;

public class SettingsGetInt_{uuid.uuid4().hex[:6]}
{{
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();

        try
        {{
            var settings = new Settings();
            int value = settings.GetNumericSetting("{cs_escape(setting_path)}", {int(index)});
            results["success"] = true;
            results["value"] = value;
            results["type"] = "int";
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}

        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results);
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", json);
    }}
}}
'''
    return _execute_script(script)


def settings_set_int(setting_path: str, value: int, index: int = 0) -> dict:
    """
    Set an integer setting in EPLAN.

    Args:
        setting_path: Full setting path
        value: Value to set
        index: Setting index (default 0)

    Returns:
        dict with success status
    """
    script = f'''using System;
using System.IO;
using System.Collections.Generic;
using Eplan.EplApi.Base;
using Eplan.EplApi.Scripting;

public class SettingsSetInt_{uuid.uuid4().hex[:6]}
{{
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();

        try
        {{
            var settings = new Settings();
            settings.SetNumericSetting("{cs_escape(setting_path)}", {int(value)}, {int(index)});
            results["success"] = true;
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}

        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results);
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", json);
    }}
}}
'''
    return _execute_script(script)


def settings_get_double(setting_path: str, index: int = 0) -> dict:
    """
    Get a double/float setting from EPLAN.

    Args:
        setting_path: Full setting path
        index: Setting index (default 0)

    Returns:
        dict with setting value
    """
    script = f'''using System;
using System.IO;
using System.Collections.Generic;
using Eplan.EplApi.Base;
using Eplan.EplApi.Scripting;

public class SettingsGetDbl_{uuid.uuid4().hex[:6]}
{{
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();

        try
        {{
            var settings = new Settings();
            double value = settings.GetDoubleSetting("{cs_escape(setting_path)}", {int(index)});
            results["success"] = true;
            results["value"] = value;
            results["type"] = "double";
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}

        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results);
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", json);
    }}
}}
'''
    return _execute_script(script)


def settings_set_double(setting_path: str, value: float, index: int = 0) -> dict:
    """
    Set a double/float setting in EPLAN.

    Args:
        setting_path: Full setting path
        value: Value to set
        index: Setting index (default 0)

    Returns:
        dict with success status
    """
    script = f'''using System;
using System.IO;
using System.Collections.Generic;
using Eplan.EplApi.Base;
using Eplan.EplApi.Scripting;

public class SettingsSetDbl_{uuid.uuid4().hex[:6]}
{{
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();

        try
        {{
            var settings = new Settings();
            settings.SetDoubleSetting("{cs_escape(setting_path)}", {float(value)}, {int(index)});
            results["success"] = true;
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}

        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results);
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", json);
    }}
}}
'''
    return _execute_script(script)


# =============================================================================
# PATH MAP (Variable substitution)
# =============================================================================


def pathmap_substitute(path_with_variables: str) -> dict:
    """
    Substitute EPLAN path variables in a string.

    Args:
        path_with_variables: Path with EPLAN variables (e.g., "$(PROJECTPATH)")

    Common variables:
        $(PROJECTPATH) - Current project path
        $(PROJECTNAME) - Current project name
        $(DOC) - Documents folder
        $(ELOGIN) - Current user login
        $(MD_MACROS) - Macros master data path
        $(MD_PARTS) - Parts master data path

    Returns:
        dict with substituted path
    """
    # Escape for a C# regular string literal (NOT verbatim - a verbatim
    # literal would double backslashes into the path and leave quotes able
    # to break out).
    escaped_path = cs_escape(path_with_variables)

    script = f'''using System;
using System.IO;
using System.Collections.Generic;
using Eplan.EplApi.Base;
using Eplan.EplApi.Scripting;

public class PathMap_{uuid.uuid4().hex[:6]}
{{
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();

        try
        {{
            string substituted = PathMap.SubstitutePath("{escaped_path}");
            results["success"] = true;
            results["original"] = "{escaped_path}";
            results["substituted"] = substituted;
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}

        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results);
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", json);
    }}
}}
'''
    return _execute_script(script)


def pathmap_get_common_paths() -> dict:
    """
    Get all common EPLAN path variables and their current values.

    Returns:
        dict with path variables and values
    """
    script = f"""using System;
using System.IO;
using System.Collections.Generic;
using Eplan.EplApi.Base;
using Eplan.EplApi.Scripting;

public class PathMapAll_{uuid.uuid4().hex[:6]}
{{
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();
        var paths = new Dictionary<string, string>();

        string[] variables = new string[]
        {{
            "$(PROJECTPATH)",
            "$(PROJECTNAME)",
            "$(DOC)",
            "$(ELOGIN)",
            "$(MD_MACROS)",
            "$(MD_PARTS)",
            "$(MD_SYMBOLS)",
            "$(MD_FORMS)",
            "$(MD_SCHEMES)",
            "$(MD_IMAGES)",
            "$(TEMPPATH)",
            "$(USERSETTINGSPATH)"
        }};

        try
        {{
            foreach (var v in variables)
            {{
                try
                {{
                    paths[v] = PathMap.SubstitutePath(v);
                }}
                catch
                {{
                    paths[v] = "(not available)";
                }}
            }}

            results["success"] = true;
            results["paths"] = paths;
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}

        string json = Newtonsoft.Json.JsonConvert.SerializeObject(results, Newtonsoft.Json.Formatting.Indented);
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", json);
    }}
}}
"""
    return _execute_script(script)


# =============================================================================
# CUSTOM SCRIPT EXECUTION
# =============================================================================


_MESSAGE_LEVELS = ("Message", "Warning", "Error", "FatalError")


def get_system_messages(min_level: str = "Warning", max_messages: int = 100) -> dict:
    """
    Read EPLAN's system message tree - the same list the user sees in the
    GUI's system messages dialog.

    Covers everything since EPLAN started (startup errors, add-in load
    problems, script compile errors, action warnings), not just messages
    from MCP-executed actions. The definitive way to answer "what errors is
    EPLAN showing?" without looking at the screen.

    Args:
        min_level: Minimum severity: "Message" (everything), "Warning",
            "Error", or "FatalError". Default "Warning".
        max_messages: Return at most this many, keeping the NEWEST ones
            (default 100).

    Each returned message is {"text", "level", "occurrences"}: "level" is the
    entry's own severity (BaseException.MessageLevel - not "Level", which
    does not exist and raises CS1061; verified live 2026-09-01) so a
    min_level="Message" call can still be filtered/grouped by severity
    client-side. "occurrences" is EPLAN's own count of consecutive identical
    messages joined into one tree item (see BaseException.NumberOfOccurrences)
    - usually 1, since consolidation depends on EPLAN's logging mode, not
    something this tool controls.
    """
    if min_level not in _MESSAGE_LEVELS:
        return {"success": False,
                "error": f"min_level must be one of {_MESSAGE_LEVELS}, got {min_level!r}"}
    try:
        max_messages = int(max_messages)
        if max_messages <= 0:
            raise ValueError
    except (TypeError, ValueError):
        return {"success": False, "error": "max_messages must be a positive integer"}

    script = f"""using System;
using System.IO;
using System.Collections.Generic;
using Eplan.EplApi.Base;
using Eplan.EplApi.Scripting;

public class McpGetSysMessages
{{
    [Start]
    public void Run()
    {{
        var results = new Dictionary<string, object>();
        var msgs = new List<Dictionary<string, object>>();
        try
        {{
            var col = new SysMessagesCollection(0, MessageLevel.{min_level});
            var it = col.GetSysMsgEnumerator();
            while (it.MoveNext())
            {{
                var m = it.Current as BaseException;
                if (m != null && !string.IsNullOrEmpty(m.Message))
                {{
                    var entry = new Dictionary<string, object>();
                    entry["text"] = m.Message;
                    entry["level"] = m.MessageLevel.ToString();
                    entry["occurrences"] = m.NumberOfOccurrences;
                    msgs.Add(entry);
                }}
            }}
            int total = msgs.Count;
            if (total > {max_messages})
            {{
                msgs = msgs.GetRange(total - {max_messages}, {max_messages});
            }}
            results["success"] = true;
            results["total"] = total;
            results["messages"] = msgs;
        }}
        catch (Exception ex)
        {{
            results["success"] = false;
            results["error"] = ex.Message;
        }}
        File.WriteAllText(@"{{{{RESULT_PATH}}}}", Newtonsoft.Json.JsonConvert.SerializeObject(results));
    }}
}}
"""
    res = _execute_script(script)
    if not res.get("success"):
        return res
    inner = res.get("results") or {}
    return {"success": inner.get("success", False),
            "min_level": min_level,
            "total_in_tree": inner.get("total"),
            "messages": inner.get("messages", []),
            "error": inner.get("error")}


def execute_custom_script(script_code: str, timeout_seconds: float = 30.0) -> dict:
    """
    Execute a custom C# script in EPLAN.

    The script should write results to a JSON file at the path specified by
    the {{RESULT_PATH}} placeholder.

    Args:
        script_code: Complete C# script code with {{RESULT_PATH}} placeholder
        timeout_seconds: Max seconds to wait for the script to write its result
            file before giving up (default 30s). Raise this for scripts that
            walk large collections (e.g. every page/function in a big project).

    Returns:
        dict with script results

    Example script:
        using System;
        using System.IO;
        using System.Collections.Generic;
        using Eplan.EplApi.Scripting;

        public class MyScript
        {
            [Start]
            public void Run()
            {
                var results = new Dictionary<string, object>();
                results["success"] = true;
                results["message"] = "Hello from EPLAN!";

                string json = Newtonsoft.Json.JsonConvert.SerializeObject(results);
                File.WriteAllText(@"{{RESULT_PATH}}", json);
            }
        }
    """
    return _execute_script(script_code, timeout=timeout_seconds)
