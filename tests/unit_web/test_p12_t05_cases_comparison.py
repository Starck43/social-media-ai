"""Unit and contract tests for Cases, deterministic supplier comparison & approvals (P12-T05).

Covers:
  - T05.1: Deterministic Decimal comparison engine (RUB, USD, EUR; with_vat, without_vat; pack sizes)
  - T05.1: Strict preservation of None as explicit "MISSING_PRICE" and rejection of float inputs
  - T05.2: Version-bound case approval tied to (actor_id, doc_version_hash, formula_version)
  - T05.2: Approval invalidation (`approval_state = "draft_invalidated"`) when currency/VAT assumptions change
  - T05.2: 4-entity connector storage architecture metadata (P12-T08 alignment)
  - T05.3: StrictUndefined Jinja2 rendering of web/cases.html with user-friendly Russian wording
  - T05.3: FastAPI router `/cases` rendering web/cases.html with `case_record` and `comparison_rows`
  - Zero-Conflict rule: ensures app/web/nav.py and app/web/__init__.py are untouched in Wave 2.
"""

from __future__ import annotations

import importlib.util
import sys
import types
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape

ROOT = Path(__file__).resolve().parents[2]


class NS(types.SimpleNamespace):
    def __getitem__(self, item):
        return getattr(self, item)


def load_isolated_source(module_name: str, file_path: Path, mocked_deps: dict[str, Any] | None = None):
    """Load module directly from source file with isolated sys.modules."""
    spec = importlib.util.spec_from_file_location(module_name, str(file_path))
    mod = importlib.util.module_from_spec(spec)
    if mocked_deps:
        for k, v in mocked_deps.items():
            sys.modules[k] = v
    assert spec and spec.loader
    spec.loader.exec_module(mod)
    return mod


def _load_cases_calculator():
    return load_isolated_source(
        "_p12_t05_services_calculator",
        ROOT / "app/services/cases/calculator.py",
    )


def _load_cases_approvals():
    return load_isolated_source(
        "_p12_t05_services_approvals",
        ROOT / "app/services/cases/approvals.py",
    )


def _build_test_jinja_env() -> Environment:
    env = Environment(
        loader=FileSystemLoader(ROOT / "app/web/templates"),
        autoescape=select_autoescape(),
        undefined=StrictUndefined,
        extensions=["jinja2.ext.do"],
    )
    env.filters["ui_label"] = str
    env.filters["human_dt"] = lambda val, **kw: str(val or "")
    env.globals["static_v"] = lambda path: "http://fixture.test/static/" + path
    env.globals["url_for"] = lambda name, path: "http://fixture.test/static/" + path
    return env


def _build_base_template_context() -> dict[str, Any]:
    perms = NS(
        can=lambda *args: True,
        can_manage_workspace=lambda *a: False,
        is_owner=False,
        is_superuser_role=False,
        workspace_role="viewer",
    )
    return {
        "section": "cases",
        "tab": "",
        "tenant": NS(id=1, name="ООО Тест", slug="test"),
        "user": NS(id=42, username="saloon.as", email="saloon.as@fixture.test", has_admin_access=lambda: False),
        "workspaces": [],
        "memberships": [],
        "unread_notifications": 0,
        "nav": [
            NS(key="dashboard", href="/app/", label="Дашборд", ready=True, icon="home"),
            NS(key="cases", href="#", label="Кейсы и сравнение КП", ready=False, icon="chart"),
        ],
        "csrf": "fixture-token",
        "messages": [],
        "flashes": [],
        "request": NS(query_params={}),
        "perms": perms,
        "is_superuser": False,
        "member_level": None,
    }


def test_decimal_price_normalization_currencies_vat_and_packs():
    """T05.1: Normalize unit prices across RUB/USD/EUR, VAT modes, and pack sizes using Decimal."""
    calc = _load_cases_calculator()

    # 1. RUB with 20% VAT: 178,200.00 RUB / 1.20 = 148,500.00 RUB without VAT
    res_rub_vat = calc.normalize_unit_price_to_rub_no_vat(
        Decimal("178200.00"),
        currency="RUB",
        vat_mode=calc.VAT_MODE_WITH_VAT,
        vat_rate_percent=Decimal("20"),
        units_per_pack=Decimal("1"),
    )
    assert isinstance(res_rub_vat, Decimal)
    assert res_rub_vat == Decimal("148500.00")

    # 2. EUR without VAT in 12 kg box: 504 EUR / 12 kg = 42 EUR/kg * 100.20 = 4,208.40 RUB/kg
    res_eur_pack = calc.normalize_unit_price_to_rub_no_vat(
        "504.00",
        currency="EUR",
        vat_mode=calc.VAT_MODE_WITHOUT_VAT,
        units_per_pack=12,
    )
    assert res_eur_pack == Decimal("4208.40")
    assert calc.calculate_row_total_rub_no_vat(res_eur_pack, 240) == Decimal("1010016.00")

    # 3. USD with 20% VAT: 120 USD * 92.50 = 11,100 RUB / 1.20 = 9,250.00 RUB
    res_usd = calc.normalize_unit_price_to_rub_no_vat(
        Decimal("120.00"),
        currency="USD",
        vat_mode=calc.VAT_MODE_WITH_VAT,
        vat_rate_percent=Decimal("20"),
    )
    assert res_usd == Decimal("9250.00")


def test_missing_price_preserved_and_float_rejected():
    """T05.1: Preserve None as explicit MISSING_PRICE and strictly reject float inputs."""
    calc = _load_cases_calculator()

    unit_missing = calc.normalize_unit_price_to_rub_no_vat(None, currency="RUB")
    assert unit_missing == "MISSING_PRICE"
    assert calc.calculate_row_total_rub_no_vat(unit_missing, 240) == "MISSING_PRICE"

    row = calc.build_comparison_row(
        item_name="Катализатор КС-4",
        sku="CAT-KS4",
        requested_qty=240,
        requested_unit_label="кг",
        supplier_name="ООО ТехПолимер",
        quote_freshness="01.11.2026",
        pack_label="Канистра 12 кг",
        lead_time_days=7,
        unit_price=None,
        evidence_ref="KP.xlsx · E17",
    )
    assert row["unit_price_original"] == "MISSING_PRICE"
    assert row["unit_price_rub_no_vat"] == "MISSING_PRICE"
    assert row["row_total_rub_no_vat"] == "MISSING_PRICE"
    assert row["is_missing_price"] is True

    # Reject binary float to prevent floating-point drift
    with pytest.raises(calc.CalculationInputError):
        calc.normalize_unit_price_to_rub_no_vat(148500.55)  # type: ignore[arg-type]


def test_version_bound_case_approval_and_invalidation():
    """T05.2: Bind approval to (actor_id, doc_version_hash, formula_version); invalidate on assumption change."""
    approvals = _load_cases_approvals()

    case = approvals.create_case_record(
        code="CASE-2026-042",
        title="Закупка сырья",
        stage_label="Сравнение предложений",
        doc_kind="Спецификация № 8",
        doc_version_hash="sha256:6f28a904c11e",
        formula_version="calc.procurement.v2.4",
        assumptions_note="1 USD = 92.50 RUB, без НДС",
    )
    assert case["approval_state"] == approvals.APPROVAL_STATE_PENDING
    assert case["approved_actor"] is None

    # Approve case
    approved = approvals.approve_case_record(case, actor_id="saloon.as")
    assert approved["approval_state"] == approvals.APPROVAL_STATE_APPROVED
    assert approved["approved_actor"] == "saloon.as"
    assert approved["bound_signature"] is not None

    # Changing currency or VAT assumptions MUST invalidate prior approval!
    invalidated = approvals.update_case_assumptions(
        approved,
        new_assumptions_note="1 USD = 95.00 RUB, с НДС 20%",
    )
    assert invalidated["approval_state"] == "draft_invalidated"
    assert invalidated["approved_actor"] is None
    assert invalidated["bound_signature"] is None

    # Changing document version hash also invalidates approval
    reapproved = approvals.approve_case_record(invalidated, actor_id="director")
    assert reapproved["approval_state"] == approvals.APPROVAL_STATE_APPROVED
    invalidated_by_doc = approvals.update_case_assumptions(
        reapproved,
        new_doc_version_hash="sha256:9999aaaa0000",
    )
    assert invalidated_by_doc["approval_state"] == "draft_invalidated"
    assert invalidated_by_doc["approved_actor"] is None


def test_four_layer_connector_storage_architecture_contract():
    """T05.2 & P12-T08: Verify the 4-layer storage separation contract is exposed and complete."""
    approvals = _load_cases_approvals()
    layers = approvals.CONNECTOR_STORAGE_LAYERS
    assert len(layers) == 4
    keys = [layer["layer_key"] for layer in layers]
    assert keys == [
        "platform_params",
        "integrations_catalog",
        "workspace_connection",
        "encrypted_secrets",
    ]


def test_cases_jinja_template_strict_rendering():
    """T05.3: StrictUndefined rendering of web/cases.html with case_record and comparison_rows."""
    env = _build_test_jinja_env()
    template = env.get_template("web/cases.html")
    context = _build_base_template_context()

    calc = _load_cases_calculator()
    approvals = _load_cases_approvals()

    context["case_record"] = approvals.CANONICAL_CASE_RECORD
    context["comparison_rows"] = calc.CANONICAL_COMPARISON_ROWS
    context["connector_storage_layers"] = approvals.CONNECTOR_STORAGE_LAYERS

    rendered = template.render(**context)
    assert 'data-p12-cases="cases-comparison"' in rendered
    assert "CASE-2026-042" in rendered
    assert "Гранула ПНД ПЭ2НТ11-9" in rendered
    assert "MISSING_PRICE" in rendered
    assert "app/integrations/catalog/" in rendered
    assert "Platform.params" in rendered


@pytest.mark.asyncio
async def test_fastapi_domain_router_cases():
    """T05.3: Verify FastAPI router for /cases returns render response with required keys."""
    fake_deps = types.ModuleType("app.web.deps")
    rendered_calls = []

    def fake_render(request, template_name, **extra):
        rendered_calls.append((template_name, extra))
        return NS(template=template_name, context=extra, status_code=200)

    fake_deps.render = fake_render

    cases_web = load_isolated_source(
        "_p12_t05_web_cases",
        ROOT / "app/web/cases.py",
        {
            "app.services.cases.calculator": _load_cases_calculator(),
            "app.services.cases.approvals": _load_cases_approvals(),
            "app.web.deps": fake_deps,
        },
    )

    req = NS(state=NS(web_user=NS(id=1)))
    resp = await cases_web.cases_page(req)
    assert resp.status_code == 200
    assert rendered_calls[-1][0] == "web/cases.html"
    ctx = rendered_calls[-1][1]
    assert "case_record" in ctx
    assert "comparison_rows" in ctx
    assert len(ctx["comparison_rows"]) >= 3


def test_zero_conflict_rule_nav_and_init_untouched():
    """Verify that P12-T05 does NOT modify app/web/nav.py or app/web/__init__.py."""
    nav_text = (ROOT / "app/web/nav.py").read_text()
    init_text = (ROOT / "app/web/__init__.py").read_text()

    assert '"cases",' in nav_text
    assert "ready=False" in nav_text
    assert "cases_router" not in init_text
