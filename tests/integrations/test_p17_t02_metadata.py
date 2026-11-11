"""Unit tests for 1C OData $metadata parsing and per-connection allowlists (P17-T02)."""

from __future__ import annotations

import pytest

from app.integrations.onec.transport import MAX_BODY_BYTES
from app.services.onec.metadata import (
    OneCMetadataError,
    classify_entity_set_prefix,
    extract_connection_allowlist,
    is_valid_identifier,
    parse_odata_metadata_xml,
    validate_entity_access,
)

SYNTHETIC_METADATA_XML = """<?xml version="1.0" encoding="UTF-8"?>
<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx" Version="1.0">
  <edmx:DataServices xmlns:m="http://schemas.microsoft.com/ado/2007/08/dataservices/metadata" m:DataServiceVersion="3.0">
    <Schema xmlns="http://schemas.microsoft.com/ado/2009/11/edm" Namespace="StandardODATA">
      <EntityType Name="Catalog_Номенклатура">
        <Key><PropertyRef Name="Ref_Key"/></Key>
        <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="Code" Type="Edm.String" Nullable="false"/>
        <Property Name="Description" Type="Edm.String" Nullable="true"/>
        <Property Name="DeletionMark" Type="Edm.Boolean" Nullable="false"/>
        <Property Name="ЦенаБазовая" Type="Edm.Decimal" Precision="15" Scale="2"/>
        <Property Name="Валюта_Key" Type="Edm.String" Nullable="true"/>
      </EntityType>
      <EntityType Name="Catalog_Контрагенты">
        <Key><PropertyRef Name="Ref_Key"/></Key>
        <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="Description" Type="Edm.String"/>
        <Property Name="ИНН" Type="Edm.String"/>
      </EntityType>
      <EntityType Name="Document_РеализацияТоваровУслуг">
        <Key><PropertyRef Name="Ref_Key"/></Key>
        <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="Number" Type="Edm.String" Nullable="false"/>
        <Property Name="Date" Type="Edm.DateTime" Nullable="false"/>
        <Property Name="Posted" Type="Edm.Boolean" Nullable="false"/>
        <Property Name="Контрагент_Key" Type="Edm.Guid" Nullable="true"/>
        <Property Name="Номенклатура_Key" Type="Edm.Guid" Nullable="true"/>
        <Property Name="СуммаДокумента" Type="Edm.Decimal" Precision="15" Scale="2"/>
        <Property Name="ВалютаДокумента_Key" Type="Edm.String" Nullable="true"/>
        <Property Name="КоличествоПозиций" Type="Edm.Int32" Nullable="false"/>
        <Property Name="ВесБрутто" Type="Edm.Double" Nullable="true"/>
      </EntityType>
      <EntityType Name="AccumulationRegister_ТоварыНаСкладах_RecordType">
        <Key>
          <PropertyRef Name="Period"/>
          <PropertyRef Name="Recorder"/>
        </Key>
        <Property Name="Period" Type="Edm.DateTime" Nullable="false"/>
        <Property Name="Recorder" Type="Edm.Guid" Nullable="false"/>
        <Property Name="Номенклатура_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="Склад_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="КоличествоBalance" Type="Edm.Decimal" Precision="15" Scale="3"/>
        <Property Name="СтоимостьBalance" Type="Edm.Decimal" Precision="15" Scale="2"/>
      </EntityType>
      <EntityType Name="InformationRegister_ЦеныНоменклатуры">
        <Key>
          <PropertyRef Name="Period"/>
          <PropertyRef Name="Номенклатура_Key"/>
        </Key>
        <Property Name="Period" Type="Edm.DateTime" Nullable="false"/>
        <Property Name="Номенклатура_Key" Type="Edm.Guid" Nullable="false"/>
        <Property Name="Цена" Type="Edm.Decimal" Precision="15" Scale="2"/>
        <Property Name="Currency" Type="Edm.String" Nullable="true"/>
      </EntityType>
      <EntityType Name="Task_ЗадачаИсполнителя">
        <Key><PropertyRef Name="Ref_Key"/></Key>
        <Property Name="Ref_Key" Type="Edm.Guid" Nullable="false"/>
      </EntityType>
      <EntityContainer Name="StandardODATA" m:IsDefaultEntityContainer="true">
        <EntitySet Name="Catalog_Номенклатура" EntityType="StandardODATA.Catalog_Номенклатура"/>
        <EntitySet Name="Catalog_Контрагенты" EntityType="StandardODATA.Catalog_Контрагенты"/>
        <EntitySet Name="Document_РеализацияТоваровУслуг" EntityType="StandardODATA.Document_РеализацияТоваровУслуг"/>
        <EntitySet Name="AccumulationRegister_ТоварыНаСкладах" EntityType="StandardODATA.AccumulationRegister_ТоварыНаСкладах_RecordType"/>
        <EntitySet Name="InformationRegister_ЦеныНоменклатуры" EntityType="StandardODATA.InformationRegister_ЦеныНоменклатуры"/>
        <EntitySet Name="Task_ЗадачаИсполнителя" EntityType="StandardODATA.Task_ЗадачаИсполнителя"/>
        <FunctionImport Name="Balance" EntitySet="AccumulationRegister_ТоварыНаСкладах" ReturnType="Collection(StandardODATA.AccumulationRegister_ТоварыНаСкладах_RecordType)"/>
      </EntityContainer>
    </Schema>
  </edmx:DataServices>
</edmx:Edmx>
"""


def test_parse_metadata_extracts_only_supported_prefixes_and_field_types():
    catalog = parse_odata_metadata_xml(SYNTHETIC_METADATA_XML)
    assert catalog.namespace == "StandardODATA"
    assert len(catalog.metadata_sha256) == 64
    assert set(catalog.entity_sets) == {
        "Catalog_Номенклатура",
        "Catalog_Контрагенты",
        "Document_РеализацияТоваровУслуг",
        "AccumulationRegister_ТоварыНаСкладах",
        "InformationRegister_ЦеныНоменклатуры",
    }
    assert "Task_ЗадачаИсполнителя" not in catalog.entity_sets

    items = catalog.get_entity_set("Catalog_Номенклатура")
    assert items.kind == "catalog"
    assert items.key_fields == ("Ref_Key",)
    assert not items.supports_balance
    price_field = items.get_field("ЦенаБазовая")
    assert price_field.logical_type == "decimal"
    assert price_field.is_monetary is True
    assert price_field.currency_field == "Валюта_Key"
    assert items.get_field("DeletionMark").logical_type == "boolean"
    assert items.get_field("Ref_Key").logical_type == "guid"
    assert items.get_field("Code").logical_type == "string"

    docs = catalog.get_entity_set("Document_РеализацияТоваровУслуг")
    assert docs.kind == "document"
    assert docs.date_field == "Date"
    assert docs.counterparty_field == "Контрагент_Key"
    assert docs.item_field == "Номенклатура_Key"
    assert docs.get_field("КоличествоПозиций").logical_type == "integer"
    assert docs.get_field("ВесБрутто").logical_type == "numeric"
    assert docs.get_field("Date").logical_type == "datetime"
    assert docs.get_field("СуммаДокумента").is_monetary is True

    reg = catalog.get_entity_set("AccumulationRegister_ТоварыНаСкладах")
    assert reg.kind == "accumulation_register"
    assert reg.supports_balance is True
    assert reg.period_field == "Period"
    assert reg.item_field == "Номенклатура_Key"
    assert reg.get_field("КоличествоBalance").is_monetary is False
    assert reg.get_field("СтоимостьBalance").is_monetary is True


def test_monetary_override_marks_custom_decimal_field():
    catalog = parse_odata_metadata_xml(
        SYNTHETIC_METADATA_XML,
        monetary_fields_override={"AccumulationRegister_ТоварыНаСкладах": frozenset({"КоличествоBalance"})},
    )
    reg = catalog.get_entity_set("AccumulationRegister_ТоварыНаСкладах")
    assert reg.get_field("КоличествоBalance").is_monetary is True


@pytest.mark.parametrize(
    ("payload", "expected_code"),
    [
        (b"", "empty_metadata"),
        (12345, "invalid_metadata_xml"),
        (b"\xff\xfe\x00\x00invalid", "invalid_metadata_xml"),
        (b"<!DOCTYPE html><edmx:Edmx/>", "unsafe_xml_forbidden"),
        (b'<?xml version="1.0"?><!ENTITY xxe SYSTEM "file:///etc/passwd"><edmx:Edmx/>', "unsafe_xml_forbidden"),
        (b'<?xml-stylesheet type="text/xsl" href="evil.xsl"?><edmx:Edmx/>', "unsafe_xml_forbidden"),
        (b"<not-valid-xml", "invalid_metadata_xml"),
        (b"<Root><Schema/></Root>", "invalid_metadata_xml"),
        (b'<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx"/>', "invalid_metadata_xml"),
        (
            b'<edmx:Edmx xmlns:edmx="http://schemas.microsoft.com/ado/2007/06/edmx"><Schema><EntityContainer/></Schema></edmx:Edmx>',
            "no_supported_entity_sets",
        ),
    ],
)
def test_unsafe_or_malformed_metadata_fails_closed(payload, expected_code):
    with pytest.raises(OneCMetadataError) as exc:
        parse_odata_metadata_xml(payload)
    assert exc.value.code == expected_code


def test_oversized_metadata_is_rejected():
    huge = b"x" * (MAX_BODY_BYTES + 1)
    with pytest.raises(OneCMetadataError, match="metadata_too_large"):
        parse_odata_metadata_xml(huge)


def test_connection_allowlist_extraction_and_validation():
    catalog = parse_odata_metadata_xml(SYNTHETIC_METADATA_XML)

    # Missing allowed_entity_sets fails closed (empty allowlist)
    allowed_sets, allowed_fields = extract_connection_allowlist({})
    assert allowed_sets == frozenset()
    assert dict(allowed_fields) == {}

    with pytest.raises(OneCMetadataError, match="entity_not_allowed"):
        validate_entity_access(
            catalog,
            "Catalog_Номенклатура",
            ("Ref_Key", "Description"),
            allowed_entity_sets=allowed_sets,
        )

    # Configured allowlist permits only listed entities and fields
    allowed_sets, allowed_fields = extract_connection_allowlist(
        {
            "allowed_entity_sets": ["Catalog_Номенклатура", "AccumulationRegister_ТоварыНаСкладах"],
            "allowed_fields": {"Catalog_Номенклатура": ["Ref_Key", "Code", "Description", "ЦенаБазовая"]},
        }
    )
    spec, fields = validate_entity_access(
        catalog,
        "Catalog_Номенклатура",
        ("Ref_Key", "Description", "ЦенаБазовая", "Ref_Key"),
        allowed_entity_sets=allowed_sets,
        allowed_fields_by_entity=allowed_fields,
        expected_metadata_sha256=catalog.metadata_sha256,
    )
    assert spec.name == "Catalog_Номенклатура"
    assert tuple(f.name for f in fields) == ("Ref_Key", "Description", "ЦенаБазовая")

    # Non-allowlisted entity is rejected
    with pytest.raises(OneCMetadataError, match="entity_not_allowed"):
        validate_entity_access(
            catalog,
            "Catalog_Контрагенты",
            ("Ref_Key",),
            allowed_entity_sets=allowed_sets,
            allowed_fields_by_entity=allowed_fields,
        )

    # Non-allowlisted field inside allowlisted entity is rejected
    with pytest.raises(OneCMetadataError, match="field_not_allowed"):
        validate_entity_access(
            catalog,
            "Catalog_Номенклатура",
            ("Ref_Key", "DeletionMark"),
            allowed_entity_sets=allowed_sets,
            allowed_fields_by_entity=allowed_fields,
        )

    # Hash mismatch is rejected
    with pytest.raises(OneCMetadataError, match="metadata_hash_mismatch"):
        validate_entity_access(
            catalog,
            "Catalog_Номенклатура",
            ("Ref_Key",),
            allowed_entity_sets=allowed_sets,
            expected_metadata_sha256="0" * 64,
        )


@pytest.mark.parametrize(
    "bad_config",
    [
        "not-a-dict",
        {"allowed_entity_sets": "Catalog_Номенклатура"},
        {"allowed_entity_sets": ["InvalidPrefix_Table"]},
        {"allowed_entity_sets": ["Catalog_Номенклатура"], "allowed_fields": ["not-a-map"]},
        {
            "allowed_entity_sets": ["Catalog_Номенклатура"],
            "allowed_fields": {"Catalog_Контрагенты": ["Ref_Key"]},
        },
        {
            "allowed_entity_sets": ["Catalog_Номенклатура"],
            "allowed_fields": {"Catalog_Номенклатура": ["bad$field"]},
        },
    ],
)
def test_malformed_allowlist_config_raises_fixed_error(bad_config):
    with pytest.raises(OneCMetadataError, match="invalid_allowlist"):
        extract_connection_allowlist(bad_config)


def test_identifier_and_prefix_helpers_and_missing_lookups():
    catalog = parse_odata_metadata_xml(SYNTHETIC_METADATA_XML)
    assert is_valid_identifier("Catalog_Номенклатура")
    assert not is_valid_identifier("Catalog_Номенклатура;DROP")
    assert classify_entity_set_prefix("InformationRegister_Курсы") == "information_register"
    assert classify_entity_set_prefix("Catalog_") is None
    assert classify_entity_set_prefix("Unknown_Set") is None

    with pytest.raises(OneCMetadataError, match="entity_not_found"):
        catalog.get_entity_set("Catalog_Несуществующий")

    entity = catalog.get_entity_set("Catalog_Номенклатура")
    with pytest.raises(OneCMetadataError, match="field_not_found"):
        entity.get_field("НесуществующееПоле")

    with pytest.raises(OneCMetadataError, match="entity_not_found"):
        validate_entity_access(
            catalog,
            "Catalog_ОтсутствуетВМетаданных",
            ("Ref_Key",),
            allowed_entity_sets=frozenset({"Catalog_ОтсутствуетВМетаданных"}),
        )

    with pytest.raises(OneCMetadataError, match="invalid_fields"):
        validate_entity_access(
            catalog,
            "Catalog_Номенклатура",
            (),
            allowed_entity_sets=frozenset({"Catalog_Номенклатура"}),
        )

    with pytest.raises(OneCMetadataError, match="invalid_fields"):
        validate_entity_access(
            catalog,
            "Catalog_Номенклатура",
            ("bad$name",),
            allowed_entity_sets=frozenset({"Catalog_Номенклатура"}),
        )

    with pytest.raises(OneCMetadataError, match="field_not_found"):
        validate_entity_access(
            catalog,
            "Catalog_Номенклатура",
            ("MissingField",),
            allowed_entity_sets=frozenset({"Catalog_Номенклатура"}),
        )
