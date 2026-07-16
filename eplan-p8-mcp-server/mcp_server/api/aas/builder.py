"""
AAS object construction and AASX packaging (pure Python, no EPLAN needed).

Built on basyx-python-sdk (Eclipse BaSyx, AAS metamodel V3, AASX Part 5).
Everything here is offline-testable; the EPLAN-facing data flows live in
export.py / import_.py.
"""

import os
import re

from basyx.aas import model
from basyx.aas.adapter import aasx

from . import mapping

# Base for generated AAS/submodel identifiers. Override per site so IDs are
# globally unique, e.g. AAS_ID_BASE=https://aas.mycompany.com/eplan
ID_BASE = os.environ.get("AAS_ID_BASE", "urn:eplan-mcp")


def _slug(text: str) -> str:
    """Filesystem/IRI-safe version of a part or project name."""
    return re.sub(r"[^A-Za-z0-9._-]+", "-", str(text)).strip("-") or "unnamed"


def _id_short(text: str) -> str:
    """Valid AAS idShort: letters/digits/underscore, must not start with a digit."""
    cleaned = re.sub(r"[^A-Za-z0-9_]+", "_", str(text)).strip("_") or "unnamed"
    if cleaned[0].isdigit():
        cleaned = "_" + cleaned
    return cleaned


def make_identifier(kind: str, name: str) -> str:
    """Build a stable AAS identifier, e.g. urn:eplan-mcp:aas:part-3RV2011."""
    return f"{ID_BASE}:{kind}:{_slug(name)}"


def _external_ref(semantic_id: str) -> model.ExternalReference:
    return model.ExternalReference(
        (model.Key(model.KeyTypes.GLOBAL_REFERENCE, semantic_id),)
    )


def _property(id_short: str, value: str, semantic_id: str = None) -> model.Property:
    return model.Property(
        id_short=id_short,
        value_type=model.datatypes.String,
        value=str(value),
        semantic_id=_external_ref(semantic_id) if semantic_id else None,
    )


def build_nameplate(part: dict, owner_name: str) -> model.Submodel:
    """Digital Nameplate (IDTA 02006) submodel from a parts_db_get_part dict."""
    elements = []
    for part_key, (id_short, semantic_id) in mapping.PART_TO_NAMEPLATE.items():
        value = part.get(part_key)
        if value:
            elements.append(_property(id_short, value, semantic_id))
    return model.Submodel(
        id_=make_identifier("sm:nameplate", owner_name),
        id_short="Nameplate",
        semantic_id=_external_ref(mapping.NAMEPLATE_SEMANTIC_ID),
        submodel_element=elements,
    )


def build_technical_data(part: dict, owner_name: str) -> model.Submodel:
    """Technical Data (IDTA 02003) submodel from a parts_db_get_part dict.

    Mapped fields go into GeneralInformation; any extra keys of the part
    dict (e.g. user-selected ERP/technical properties) go into
    TechnicalProperties verbatim.
    """
    general = []
    mapped_keys = set()
    for part_key, (id_short, semantic_id) in mapping.PART_TO_TECHNICAL_DATA.items():
        value = part.get(part_key)
        if value:
            general.append(_property(id_short, value, semantic_id))
            mapped_keys.add(part_key)

    technical = []
    for key, value in part.items():
        if key not in mapped_keys and key not in mapping.PART_TO_NAMEPLATE and value:
            technical.append(_property(_id_short(key), value))

    collections = [
        model.SubmodelElementCollection(id_short="GeneralInformation", value=general)
    ]
    if technical:
        collections.append(
            model.SubmodelElementCollection(id_short="TechnicalProperties", value=technical)
        )
    return model.Submodel(
        id_=make_identifier("sm:technical-data", owner_name),
        id_short="TechnicalData",
        semantic_id=_external_ref(mapping.TECHNICAL_DATA_SEMANTIC_ID),
        submodel_element=collections,
    )


def build_handover_documentation(document_files: list, owner_name: str) -> model.Submodel:
    """Handover Documentation (IDTA 02004) submodel referencing embedded files.

    Args:
        document_files: list of package-internal paths (e.g. "/aasx/docs/x.pdf")
    """
    elements = []
    for i, internal_path in enumerate(document_files, start=1):
        elements.append(
            model.SubmodelElementCollection(
                id_short=f"Document{i:02d}",
                value=[
                    model.File(
                        id_short="DigitalFile",
                        content_type="application/pdf",
                        value=internal_path,
                    )
                ],
            )
        )
    return model.Submodel(
        id_=make_identifier("sm:handover-documentation", owner_name),
        id_short="HandoverDocumentation",
        semantic_id=_external_ref(mapping.HANDOVER_DOC_SEMANTIC_ID),
        submodel_element=elements,
    )


def build_shell(name: str, kind: str, submodels: list) -> model.AssetAdministrationShell:
    """Asset Administration Shell referencing the given submodels."""
    return model.AssetAdministrationShell(
        id_=make_identifier("aas", name),
        id_short=_id_short(name),
        asset_information=model.AssetInformation(
            asset_kind=model.AssetKind.INSTANCE,
            global_asset_id=make_identifier(f"asset:{kind}", name),
        ),
        submodel={model.ModelReference.from_referable(sm) for sm in submodels},
    )


def write_aasx(output_path: str, shells: list, submodels: list, embedded_files: dict = None) -> dict:
    """Write shells + submodels (+ optional embedded files) to a .aasx package.

    Args:
        output_path: target .aasx path
        shells: AssetAdministrationShell objects
        submodels: Submodel objects
        embedded_files: {package_internal_path: local_file_path} to embed,
            e.g. {"/aasx/docs/manual.pdf": "C:/exports/manual.pdf"}

    Returns:
        summary dict
    """
    object_store = model.DictObjectStore()
    for obj in list(shells) + list(submodels):
        object_store.add(obj)

    file_store = aasx.DictSupplementaryFileContainer()
    for internal_path, local_path in (embedded_files or {}).items():
        with open(local_path, "rb") as f:
            file_store.add_file(internal_path, f, "application/pdf")

    os.makedirs(os.path.dirname(os.path.abspath(output_path)), exist_ok=True)
    with aasx.AASXWriter(output_path) as writer:
        writer.write_aas(
            aas_ids=[shell.id for shell in shells],
            object_store=object_store,
            file_store=file_store,
        )
    return {
        "output_path": os.path.abspath(output_path),
        "shells": [shell.id for shell in shells],
        "submodels": [sm.id for sm in submodels],
        "embedded_files": list((embedded_files or {}).keys()),
    }


def read_aasx(aasx_path: str):
    """Read a .aasx package. Returns (object_store, file_store)."""
    object_store = model.DictObjectStore()
    file_store = aasx.DictSupplementaryFileContainer()
    with aasx.AASXReader(aasx_path) as reader:
        reader.read_into(object_store=object_store, file_store=file_store)
    return object_store, file_store
