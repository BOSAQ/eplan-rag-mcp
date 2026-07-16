"""Offline tests for the AAS package (mapping, builder, inspect, dry-run import).

Run from eplan-p8-mcp-server/:  python -m pytest tests/ -v
No EPLAN needed - everything here is the pure-Python side of the AAS tools.
"""

import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "mcp_server"))

from api.aas import builder, mapping  # noqa: E402
from api.aas.import_ import inspect_package, import_parts  # noqa: E402

SAMPLE_PART = {
    "PartNr": "SIE.3RV2011-1EA10",
    "Description1": "Circuit breaker size S00",
    "Description2": "",
    "Manufacturer": "SIE",
    "Supplier": "SIE",
    "OrderNr": "3RV2011-1EA10",
    "ProductGroup": "Motor overload protection",
    "ProductSubGroup": "Circuit breaker",
    "ProductTopGroup": "Electrical engineering",
}


def test_id_short_sanitization():
    assert builder._id_short("3RV2011-1EA10").startswith("_")
    assert " " not in builder._id_short("my part name")
    assert builder._id_short("") == "unnamed"


def test_make_identifier_stable_and_safe():
    ident = builder.make_identifier("aas", "SIE.3RV2011-1EA10")
    assert ident == builder.make_identifier("aas", "SIE.3RV2011-1EA10")
    assert " " not in ident and ident.startswith(builder.ID_BASE)


def test_nameplate_maps_part_fields():
    sm = builder.build_nameplate(SAMPLE_PART, SAMPLE_PART["PartNr"])
    assert sm.id_short == "Nameplate"
    assert sm.semantic_id.key[0].value == mapping.NAMEPLATE_SEMANTIC_ID
    values = {e.id_short: e.value for e in sm.submodel_element}
    assert values["ManufacturerName"] == "SIE"
    assert values["ManufacturerProductDesignation"] == "Circuit breaker size S00"
    assert values["OrderCodeOfManufacturer"] == "3RV2011-1EA10"
    assert values["ProductArticleNumberOfManufacturer"] == "SIE.3RV2011-1EA10"


def test_nameplate_skips_empty_fields():
    sm = builder.build_nameplate({"Manufacturer": "SIE", "OrderNr": ""}, "x")
    id_shorts = [e.id_short for e in sm.submodel_element]
    assert id_shorts == ["ManufacturerName"]


def test_technical_data_general_information_and_extras():
    part = dict(SAMPLE_PART, VOLTAGE="690 V")
    sm = builder.build_technical_data(part, part["PartNr"])
    collections = {c.id_short: c for c in sm.submodel_element}
    general = {e.id_short: e.value for e in collections["GeneralInformation"].value}
    assert general["ManufacturerArticleNumber"] == "SIE.3RV2011-1EA10"
    technical = {e.id_short: e.value for e in collections["TechnicalProperties"].value}
    assert technical["VOLTAGE"] == "690 V"


def test_shell_references_submodels():
    sm = builder.build_nameplate(SAMPLE_PART, SAMPLE_PART["PartNr"])
    shell = builder.build_shell(SAMPLE_PART["PartNr"], "part", [sm])
    assert len(shell.submodel) == 1
    assert shell.asset_information.global_asset_id.startswith(builder.ID_BASE)


@pytest.fixture
def sample_aasx(tmp_path):
    submodels = [
        builder.build_nameplate(SAMPLE_PART, SAMPLE_PART["PartNr"]),
        builder.build_technical_data(SAMPLE_PART, SAMPLE_PART["PartNr"]),
    ]
    shell = builder.build_shell(SAMPLE_PART["PartNr"], "part", submodels)
    path = str(tmp_path / "part.aasx")
    summary = builder.write_aasx(path, [shell], submodels)
    assert os.path.isfile(path)
    assert summary["shells"] == [shell.id]
    return path


def test_roundtrip_inspect(sample_aasx):
    result = inspect_package(sample_aasx)
    assert result["success"]
    assert len(result["shells"]) == 1
    id_shorts = {sm["idShort"] for sm in result["submodels"]}
    assert id_shorts == {"Nameplate", "TechnicalData"}
    nameplate = next(sm for sm in result["submodels"] if sm["idShort"] == "Nameplate")
    assert nameplate["semanticId"] == mapping.NAMEPLATE_SEMANTIC_ID
    assert nameplate["properties"]["ManufacturerName"] == "SIE"


def test_inspect_missing_file():
    result = inspect_package("Z:/does/not/exist.aasx")
    assert result["success"] is False
    assert "error" in result


def test_import_dry_run_proposes_correct_writes(sample_aasx):
    result = import_parts(sample_aasx, dry_run=True)
    assert result["success"] and result["dryRun"]
    [plan] = result["proposed"]
    assert plan["partNumber"] == "SIE.3RV2011-1EA10"
    assert plan["fields"]["ARTICLE_MANUFACTURER"] == "SIE"
    assert plan["fields"]["ARTICLE_DESCR1"] == "Circuit breaker size S00"
    assert plan["fields"]["ARTICLE_ORDERNR"] == "3RV2011-1EA10"
    # ARTICLE_PARTNR is the identity, never a field write
    assert "ARTICLE_PARTNR" not in plan["fields"]


def test_export_project_offline_documents_only(tmp_path):
    from api.aas.export_ import export_project

    doc = tmp_path / "report.pdf"
    doc.write_bytes(b"%PDF-1.4 fake")
    out = str(tmp_path / "project.aasx")
    result = export_project(
        "TestProject",
        out,
        document_paths=[str(doc), "Z:/missing.pdf"],
        properties={"ProjectName": "TestProject", "Customer": "ACME"},
    )
    assert result["success"], result
    assert result["missingDocuments"] == ["Z:/missing.pdf"]

    inspection = inspect_package(out)
    id_shorts = {sm["idShort"] for sm in inspection["submodels"]}
    assert id_shorts == {"TechnicalData", "HandoverDocumentation"}
    assert any("report" in f for f in inspection["embeddedFiles"])


def test_export_project_rejects_empty_export(tmp_path):
    from api.aas.export_ import export_project

    result = export_project("Empty", str(tmp_path / "e.aasx"))
    assert result["success"] is False
