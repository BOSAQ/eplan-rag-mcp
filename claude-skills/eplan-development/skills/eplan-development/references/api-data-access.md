# API Data Access (Parts Database, Properties)

Some `Eplan.EplApi.*` API namespaces work directly inside scripts (no separate add-in needed): notably `Eplan.EplApi.MasterData` for the parts database.

> **Important correction (documented 2026-08):** `using Eplan.EplApi.DataModel;` /
> `using Eplan.EplApi.HEServices;` do **NOT** compile inside the EPLAN script
> engine (CS0234 — the engine references a fixed assembly set). However, the
> full object model IS reachable at **runtime** via reflection, which requires no
> API project and no additional license. See
> `e3d-installation-spaces.md` for the working recipe (LockingStep +
> SelectionSet + InstallationSpace.Create).
>
> **Do not hardcode the assembly name.** `Assembly.Load("Eplan.EplApi.DataModelu")`
> works on 2025 but **fails on EPLAN 2027** with `BadImageFormatException`
> (0x8007000B): there the managed object model is
> `Eplan.EplApi.DataModelNetu` / `Eplan.EplApi.HEServicesNetu`, and the
> un-suffixed name is the mixed-mode **native** twin. Both names exist in the
> 2027 process, so this is a silent wrong-assembly pick. Resolve the type out of
> `AppDomain.CurrentDomain.GetAssemblies()` instead — EPLAN already has the
> managed assembly loaded — which works on both naming schemes.

## Parts database (`MDPartsManagement`)

```csharp
using Eplan.EplApi.MasterData;

MDPartsManagement pm = new MDPartsManagement();
MDPartsDatabase database = pm.OpenDatabase();   // currently configured parts DB
if (database == null) throw new Exception("Could not open parts database");

foreach (MDPart part in database.Parts)
{
    if (part == null || string.IsNullOrEmpty(part.PartNr)) continue;
    // part.PartNr, part.Variant, part.Properties...
}
```

## Part properties

Access via `part.Properties.<PROPERTY_NAME>`. Values are `MDPropertyValue`: check `IsEmpty` before converting.

```csharp
string manufacturer = part.Properties.ARTICLE_MANUFACTURER_NAME.IsEmpty
    ? "" : part.Properties.ARTICLE_MANUFACTURER_NAME.ToString();
string erp = part.Properties.ARTICLE_ERPNR.IsEmpty
    ? "" : part.Properties.ARTICLE_ERPNR.ToString();
bool hasCE = !part.Properties.ARTICLE_CERTIFICATE_CE.IsEmpty
    && part.Properties.ARTICLE_CERTIFICATE_CE.ToBool();
string ul = part.Properties.ARTICLE_CERTIFICATE_UL.IsEmpty
    ? "" : part.Properties.ARTICLE_CERTIFICATE_UL.ToString();
```

Useful article properties:
- `ARTICLE_DESCR1/2/3` — descriptions (multilang; parse with the multilang parser in core-classes.md)
- `ARTICLE_MANUFACTURER_NAME`, `ARTICLE_ERPNR`
- `ARTICLE_CERTIFICATE_CE` (bool), `ARTICLE_CERTIFICATE_UL`
- `ARTICLE_EXTERNAL_DOCUMENT_1` … `ARTICLE_EXTERNAL_DOCUMENT_20` — legacy doc links
- `ARTICLE_EXTERNAL_DOCUMENT_URL[i]` / `ARTICLE_EXTERNAL_DOCUMENT_DESIGNATION[i]` — indexed doc links (newer)

### Indexed vs legacy external documents (fallback pattern)
Newer databases use the indexed `ARTICLE_EXTERNAL_DOCUMENT_URL[i]` properties; older ones the numbered `ARTICLE_EXTERNAL_DOCUMENT_n`. Robust code reads the indexed property and falls back to the numbered one when empty:

```csharp
for (int i = 1; i <= 20; i++)
{
    var pUrl = part.Properties.ARTICLE_EXTERNAL_DOCUMENT_URL[i];
    var pDesg = part.Properties.ARTICLE_EXTERNAL_DOCUMENT_DESIGNATION[i];
    if (pUrl.IsEmpty)
    {
        // fall back to ARTICLE_EXTERNAL_DOCUMENT_1 .. _20 by index
        switch (i)
        {
            case 1: pUrl = part.Properties.ARTICLE_EXTERNAL_DOCUMENT_1; break;
            case 2: pUrl = part.Properties.ARTICLE_EXTERNAL_DOCUMENT_2; break;
            // ... up to 20
        }
    }
    // document paths often contain $(MD_DOCUMENTS) -> resolve with PathMap.SubstitutePath
}
```

## Writing part properties (and creating/removing parts)

Writes go through the same property list and take effect **immediately** —
there is no save, commit or `Store()` step. Verified live on EPLAN 2026.

```csharp
MDPartsManagement pm = new MDPartsManagement();
using (MDPartsDatabase db = pm.OpenDatabase())
{
    MDPart part = db.AddPart("ZZ-TEST-001");        // throws if it exists

    // Assign through the property-id indexer, or the typed member.
    part.Properties[Eplan.EplApi.MasterData.Properties.MDPartsDatabaseItem.ARTICLE_MANUFACTURER]
        = "BOSAQ";
    part.Properties[Eplan.EplApi.MasterData.Properties.MDPartsDatabaseItem.ARTICLE_DESCR1]
        = "Circuit breaker";                        // MultiLangString-valued

    db.RemovePart(part);                            // permanent, no undo
}
```

`ARTICLE_DESCR1` and friends are multilanguage: a plain string is stored as
`??_??@Circuit breaker;` (no language assigned). Build a `MultiLangString`
if the language matters — see core-classes.md.

### `new MDPropertyValue("x")` does not exist

`MDPropertyValue` has **only a default constructor** — `CS1729: does not
contain a constructor that takes 1 arguments`. The string → `MDPropertyValue`
conversion that makes the assignment above work is compile-time only. To
build one, default-construct and `Set` it:

```csharp
var pv = new MDPropertyValue();
pv.Set("BOSAQ");            // Set(String) / Set(Double) / Set(Boolean) / ...
```

### Reaching a property by name at runtime: mind the ambiguity

Every `ARTICLE_*` member is declared **twice** on
`MDPartsDatabaseItemPropertyList` — once parameterless, once taking an `int`
index (for multi-value properties like `ARTICLE_CUSTOM_DATA_VALUE(i)`). So
the obvious reflection call throws `AmbiguousMatchException: Ambiguous match
found.` for *every* property, and `BindingFlags.DeclaredOnly` does not help
(both overloads are declared on the same type):

```csharp
// WRONG - always throws
var pi = part.Properties.GetType().GetProperty(name);

// RIGHT - pin the empty index-parameter list to select the plain overload
var pi = part.Properties.GetType().GetProperty(
    name,
    BindingFlags.Public | BindingFlags.Instance,
    null, null, Type.EmptyTypes, null);

var value = pi.GetValue(part.Properties, null);     // an MDPropertyValue

var pv = new MDPropertyValue();
pv.Set("BOSAQ");
pi.SetValue(part.Properties, pv, null);             // NOT a bare string:
                                                    // ArgumentException
```

Also note which names live where: `PartNr`, `Variant`, `ProductGroup`,
`ProductSubGroup` and `ProductTopGroup` are members of **`MDPart`**, while
descriptions, manufacturer, order number etc. are `ARTICLE_*` entries on
`MDPart.Properties`. A name-based lookup has to try both objects. There is no
`Description1` or `Manufacturer` on either — those are `ARTICLE_DESCR1` and
`ARTICLE_MANUFACTURER`.

## User-defined properties on parts

User-defined properties live on the part as `UserDefinedPropertyPositions`; each position has `IdentifyingName` (e.g. `"MLX.P025"`) and `Value` (multilang string):

```csharp
foreach (var pos in part.UserDefinedPropertyPositions)
{
    if (pos == null) continue;
    string ident = pos.IdentifyingName ?? "";
    if (ident.Equals("MLX.P025", StringComparison.OrdinalIgnoreCase))
    {
        var v = pos.Value;
        if (v != null) myValue = ParseMultiLang(v.ToString());
    }
}
```

## Practical notes

- Wrap per-part processing in try/catch and continue the loop — a single corrupt part must not abort a full DB scan; log the part number.
- Materialize `database.Parts` into a `List<MDPart>` first if you need a count for a progress bar.
- Document paths from the parts DB frequently contain PathMap variables (`$(MD_DOCUMENTS)\...`) — always resolve with `PathMap.SubstitutePath` before using as a filesystem path, and skip `http(s)://` URLs when expecting files.
- Report progress/results with `new BaseException(msg, MessageLevel.Message).FixMessage()` so runs are traceable in EPLAN's message list.
- For project data (pages, devices, functions): prefer **actions** from scripts (`selectionset`, `edit`, property actions — see actions-reference.md). When an action cannot do the job (e.g. creating installation spaces headless), reach the object model via runtime reflection on the loaded assemblies (see `e3d-installation-spaces.md`), not via `using Eplan.EplApi.DataModel;` — that reference fails to compile.
