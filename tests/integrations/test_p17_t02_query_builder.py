"""Unit tests for typed 1C OData query builder, pagination, and Decimal normalization (P17-T02)."""

from __future__ import annotations

import json
from datetime import date, datetime, timezone
from decimal import Decimal
from urllib.parse import unquote

import pytest

from app.services.onec.metadata import parse_odata_metadata_xml
from app.services.onec.query import (
    MAX_ROWS_PER_REQUEST,
    MonetaryValue,
    OneCFilterPredicate,
    OneCQueryError,
    OneCQueryPlan,
    OneCSortSpec,
    build_odata_query_target,
    decode_page_token,
    encode_page_token,
    parse_odata_json_rows,
)
from tests.integrations.test_p17_t02_metadata import SYNTHETIC_METADATA_XML

ALLOWED_ALL = frozenset(
    {
        "Catalog_Номенклатура",
        "Catalog_Контрагенты",
        "Document_РеализацияТоваровУслуг",
        "AccumulationRegister_ТоварыНаСкладах",
        "InformationRegister_ЦеныНоменклатуры",
    }
)


def _catalog():
    return parse_odata_metadata_xml(SYNTHETIC_METADATA_XML)


def test_catalog_query_builds_select_filter_orderby_top_and_skip():
    catalog = _catalog()
    plan = OneCQueryPlan(
        entity_set="Catalog_Номенклатура",
        select_fields=("Ref_Key", "Code", "Description", "ЦенаБазовая"),
        filters=(
            OneCFilterPredicate("DeletionMark", "eq", False),
            OneCFilterPredicate("Description", "startswith", "Сервер 'Эльбрус'"),
            OneCFilterPredicate("ЦенаБазовая", "ge", Decimal("10500.25")),
        ),
        order_by=(OneCSortSpec("Code", "asc"),),
        top=250,
        skip=50,
    )
    validated = build_odata_query_target(
        plan,
        catalog,
        allowed_entity_sets=ALLOWED_ALL,
        base_path="/erp",
    )
    decoded = unquote(validated.odata_relative_path)
    assert decoded.startswith("/erp/odata/standard.odata/Catalog_Номенклатура?")
    assert "$format=json" in decoded
    # Валюта_Key is automatically added to OData $select to resolve currency for ЦенаБазовая
    assert "$select=Ref_Key,Code,Description,ЦенаБазовая,Валюта_Key" in decoded
    assert (
        "$filter=DeletionMark eq false and startswith(Description,'Сервер ''Эльбрус''') and ЦенаБазовая ge 10500.25m"
        in decoded
    )
    assert "$orderby=Code asc" in decoded
    assert "$top=250" in decoded
    assert "$skip=50" in decoded


def test_document_query_supports_date_counterparty_and_item_filters():
    catalog = _catalog()
    cp_guid = "11111111-2222-3333-4444-555555555555"
    item_guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    plan = OneCQueryPlan(
        entity_set="Document_РеализацияТоваровУслуг",
        select_fields=("Ref_Key", "Number", "Date", "СуммаДокумента", "КоличествоПозиций"),
        date_from=datetime(2026, 11, 1, 0, 0, 0, tzinfo=timezone.utc),
        date_to=date(2026, 11, 11),
        counterparty_key=cp_guid,
        item_key=item_guid,
        filters=(OneCFilterPredicate("Posted", "eq", True), OneCFilterPredicate("КоличествоПозиций", "gt", 2)),
        top=1000,
    )
    validated = build_odata_query_target(plan, catalog, allowed_entity_sets=ALLOWED_ALL)
    decoded = unquote(validated.odata_relative_path)
    assert "Posted eq true" in decoded
    assert "КоличествоПозиций gt 2" in decoded
    assert "Date ge datetime'2026-11-01T00:00:00'" in decoded
    assert "Date le datetime'2026-11-11T00:00:00'" in decoded
    assert f"Контрагент_Key eq guid'{cp_guid}'" in decoded
    assert f"Номенклатура_Key eq guid'{item_guid}'" in decoded
    assert "$top=1000" in decoded


def test_register_balance_function_and_period_parameter():
    catalog = _catalog()
    item_guid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    plan = OneCQueryPlan(
        entity_set="AccumulationRegister_ТоварыНаСкладах",
        register_function="Balance",
        period=datetime(2026, 11, 11, 12, 30, 0),
        select_fields=("Номенклатура_Key", "Склад_Key", "КоличествоBalance", "СтоимостьBalance"),
        item_key=item_guid,
        top=500,
    )
    validated = build_odata_query_target(plan, catalog, allowed_entity_sets=ALLOWED_ALL, base_path="/base")
    decoded = unquote(validated.odata_relative_path)
    assert (
        "/base/odata/standard.odata/AccumulationRegister_ТоварыНаСкладах/Balance(Period=datetime'2026-11-11T12:30:00')?"
        in decoded
    )
    assert f"Номенклатура_Key eq guid'{item_guid}'" in decoded

    # Calling Balance on a Catalog is rejected
    bad_plan = OneCQueryPlan(
        entity_set="Catalog_Номенклатура",
        register_function="Balance",
        select_fields=("Ref_Key",),
    )
    with pytest.raises(OneCQueryError, match="balance_not_supported"):
        build_odata_query_target(bad_plan, catalog, allowed_entity_sets=ALLOWED_ALL)


def test_hard_row_limit_and_pagination_token_binding():
    catalog = _catalog()
    with pytest.raises(OneCQueryError, match="invalid_limit"):
        OneCQueryPlan(
            entity_set="Catalog_Номенклатура",
            select_fields=("Ref_Key",),
            top=MAX_ROWS_PER_REQUEST + 1,
        )
    with pytest.raises(OneCQueryError, match="invalid_limit"):
        OneCQueryPlan(entity_set="Catalog_Номенклатура", select_fields=("Ref_Key",), top=0)
    with pytest.raises(OneCQueryError, match="invalid_limit"):
        OneCQueryPlan(entity_set="Catalog_Номенклатура", select_fields=("Ref_Key",), top=True)  # type: ignore[arg-type]

    plan_page_1 = OneCQueryPlan(
        entity_set="Catalog_Номенклатура",
        select_fields=("Ref_Key", "Description"),
        top=2,
    )
    validated_1 = build_odata_query_target(plan_page_1, catalog, allowed_entity_sets=ALLOWED_ALL)
    token = encode_page_token(plan_fingerprint=validated_1.plan_fingerprint, skip=2)
    assert decode_page_token(token, expected_fingerprint=validated_1.plan_fingerprint) == 2

    plan_page_2 = OneCQueryPlan(
        entity_set="Catalog_Номенклатура",
        select_fields=("Ref_Key", "Description"),
        top=2,
        page_token=token,
    )
    validated_2 = build_odata_query_target(plan_page_2, catalog, allowed_entity_sets=ALLOWED_ALL)
    assert validated_2.effective_skip == 2
    assert "$skip=2" in validated_2.odata_relative_path

    # Reusing the token on a modified query plan (different filter/fields) fails closed
    tampered_plan = OneCQueryPlan(
        entity_set="Catalog_Номенклатура",
        select_fields=("Ref_Key", "Code"),
        top=2,
        page_token=token,
    )
    with pytest.raises(OneCQueryError, match="invalid_page_token"):
        build_odata_query_target(tampered_plan, catalog, allowed_entity_sets=ALLOWED_ALL)


def test_parse_odata_json_rows_normalizes_monetary_decimals_and_strips_extra_fields():
    catalog = _catalog()
    plan = OneCQueryPlan(
        entity_set="Document_РеализацияТоваровУслуг",
        select_fields=("Ref_Key", "Number", "СуммаДокумента", "КоличествоПозиций"),
        top=2,
        default_currency="RUB",
    )
    validated = build_odata_query_target(plan, catalog, allowed_entity_sets=ALLOWED_ALL)

    raw_json = (
        '{"odata.metadata":"https://erp.example.com/$metadata","value":['
        '{"Ref_Key":"11111111-2222-3333-4444-555555555555","Number":"РТУ-001",'
        '"СуммаДокумента":1250.505,"ВалютаДокумента_Key":"RUB","КоличествоПозиций":3,"UnrequestedSecret":"hidden"},'
        '{"Ref_Key":"22222222-2222-3333-4444-555555555555","Number":"РТУ-002",'
        '"СуммаДокумента":"499.995","ВалютаДокумента_Key":"USD","КоличествоПозиций":1}'
        "]}"
    ).encode("utf-8")

    rows, next_token = parse_odata_json_rows(raw_json, validated)
    assert len(rows) == 2
    assert next_token is not None
    assert "UnrequestedSecret" not in rows[0]

    money_0 = rows[0]["СуммаДокумента"]
    assert isinstance(money_0, MonetaryValue)
    assert money_0.amount == Decimal("1250.50")  # banker's rounding (ROUND_HALF_EVEN)
    assert money_0.currency == "RUB"
    assert money_0.formatted == "1 250,50 ₽"
    assert money_0.to_dict() == {"amount": "1250.50", "currency": "RUB", "formatted": "1 250,50 ₽"}

    money_1 = rows[1]["СуммаДокумента"]
    assert isinstance(money_1, MonetaryValue)
    assert money_1.amount == Decimal("500.00")
    assert money_1.currency == "USD"
    assert money_1.formatted == "500.00 USD"


def test_float_inputs_and_invalid_predicates_are_rejected():
    catalog = _catalog()
    with pytest.raises(OneCQueryError, match="invalid_monetary_value"):
        MonetaryValue(amount=12.34, currency="RUB")  # type: ignore[arg-type]
    with pytest.raises(OneCQueryError, match="invalid_currency"):
        MonetaryValue(amount=Decimal("10.00"), currency="INVALID")
    with pytest.raises(OneCQueryError, match="float_forbidden"):
        OneCFilterPredicate("ЦенаБазовая", "eq", 19.99)  # type: ignore[arg-type]
    with pytest.raises(OneCQueryError, match="invalid_filter_operator"):
        OneCFilterPredicate("ЦенаБазовая", "like", Decimal("10"))
    with pytest.raises(OneCQueryError, match="invalid_sort_direction"):
        OneCSortSpec("Code", "random")

    # startswith on a non-string field is rejected
    bad_startswith = OneCQueryPlan(
        entity_set="Catalog_Номенклатура",
        select_fields=("Ref_Key",),
        filters=(OneCFilterPredicate("DeletionMark", "startswith", "true"),),
    )
    with pytest.raises(OneCQueryError, match="invalid_filter_operator"):
        build_odata_query_target(bad_startswith, catalog, allowed_entity_sets=ALLOWED_ALL)

    # gt on a GUID or boolean field is rejected
    bad_guid_cmp = OneCQueryPlan(
        entity_set="Catalog_Номенклатура",
        select_fields=("Ref_Key",),
        filters=(OneCFilterPredicate("Ref_Key", "gt", "11111111-2222-3333-4444-555555555555"),),
    )
    with pytest.raises(OneCQueryError, match="invalid_filter_operator"):
        build_odata_query_target(bad_guid_cmp, catalog, allowed_entity_sets=ALLOWED_ALL)


def test_odata_v2_d_results_envelope_and_oversized_row_count():
    catalog = _catalog()
    plan = OneCQueryPlan(
        entity_set="AccumulationRegister_ТоварыНаСкладах",
        register_function="Balance",
        select_fields=("Номенклатура_Key", "КоличествоBalance", "СтоимостьBalance"),
        top=1,
    )
    validated = build_odata_query_target(plan, catalog, allowed_entity_sets=ALLOWED_ALL)

    v2_body = json.dumps(
        {
            "d": {
                "results": [
                    {
                        "Номенклатура_Key": "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee",
                        "КоличествоBalance": "15.500",
                        "СтоимостьBalance": "31000.00",
                    }
                ],
                "__next": "https://erp.example.com/next",
            }
        }
    ).encode("utf-8")
    rows, next_token = parse_odata_json_rows(v2_body, validated)
    assert len(rows) == 1
    assert rows[0]["КоличествоBalance"] == Decimal("15.500")
    assert rows[0]["СтоимостьBalance"].amount == Decimal("31000.00")
    assert next_token is not None

    # Returning 2 rows when top=1 is rejected
    too_many = json.dumps({"value": [{}, {}]}).encode("utf-8")
    with pytest.raises(OneCQueryError, match="response_too_large"):
        parse_odata_json_rows(too_many, validated)


def test_information_register_period_filter_and_literal_edge_cases():
    catalog = _catalog()
    plan = OneCQueryPlan(
        entity_set="InformationRegister_ЦеныНоменклатуры",
        select_fields=("Period", "Номенклатура_Key", "Цена"),
        period=date(2026, 4, 15),
        item_key="11111111-2222-3333-4444-555555555555",
        filters=(
            OneCFilterPredicate("Period", "ge", "2026-04-01T00:00:00Z"),
        ),
        top=10,
    )
    validated = build_odata_query_target(plan, catalog, allowed_entity_sets=ALLOWED_ALL)
    assert "Period%20le%20datetime%272026-04-15T00%3A00%3A00%27" in validated.odata_relative_path
    assert "Period%20ge%20datetime%272026-04-01T00%3A00%3A00%27" in validated.odata_relative_path

    # Row normalization with null, boolean, integer string, and invalid responses
    cat_plan = OneCQueryPlan(
        entity_set="Catalog_Номенклатура",
        select_fields=("Ref_Key", "DeletionMark", "Code", "ЦенаБазовая"),
        top=5,
    )
    cat_val = build_odata_query_target(cat_plan, catalog, allowed_entity_sets=ALLOWED_ALL)
    rows, _ = parse_odata_json_rows(
        json.dumps(
            {
                "value": [
                    {
                        "Ref_Key": "11111111-2222-3333-4444-555555555555",
                        "DeletionMark": False,
                        "Code": None,
                        "ЦенаБазовая": None,
                    }
                ]
            }
        ).encode("utf-8"),
        cat_val,
    )
    assert rows[0]["DeletionMark"] is False
    assert rows[0]["Code"] is None
    assert rows[0]["ЦенаБазовая"] is None

    with pytest.raises(OneCQueryError, match="empty_response"):
        parse_odata_json_rows(b"", cat_val)
    with pytest.raises(OneCQueryError, match=" invalid_response|invalid_response"):
        parse_odata_json_rows(b"not-json", cat_val)
    with pytest.raises(OneCQueryError, match="invalid_response"):
        parse_odata_json_rows(b'{"unexpected": 1}', cat_val)
    with pytest.raises(OneCQueryError, match="invalid_response"):
        parse_odata_json_rows(
            json.dumps({"value": [{"Ref_Key": "1", "DeletionMark": "not-a-bool"}]}).encode("utf-8"),
            cat_val,
        )

