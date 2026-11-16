"""The dormant `0155` nullability split is tolerated only while the switch is off."""

from __future__ import annotations

from scripts.migrations.q14_compare_schemas import compare


def _catalog(*, nullable: bool, column_type: str = "integer") -> dict:
    return {
        "tables": {"tenants": "r"},
        "enums": {},
        "sequences": {},
        "functions": {},
        "triggers": [],
        "columns": {"tenants": [{"name": "plan_holder_user_id", "nullable": nullable, "type": column_type}]},
        "constraints": {"tenants": []},
        "indexes": {"tenants": []},
    }


def test_dormant_switch_accepts_the_finalized_nullable_split():
    result = compare(_catalog(nullable=False), _catalog(nullable=True), dormant_service_plans=True)
    assert result["status"] == "PASS"
    assert result["differences"] == {}
    assert any("plan_holder_user_id" in entry for entry in result["allowed_differences"])


def test_switch_on_reports_the_nullable_difference():
    result = compare(_catalog(nullable=False), _catalog(nullable=True), dormant_service_plans=False)
    assert result["status"] == "DRIFT"
    assert "columns" in result["differences"]


def test_other_holder_column_changes_still_drift():
    result = compare(
        _catalog(nullable=False, column_type="bigint"),
        _catalog(nullable=True),
        dormant_service_plans=True,
    )
    assert result["status"] == "DRIFT"
    assert "columns" in result["differences"]


def test_reversed_nullability_is_not_tolerated():
    result = compare(_catalog(nullable=True), _catalog(nullable=False), dormant_service_plans=True)
    assert result["status"] == "DRIFT"
