"""Deterministic supplier price comparison calculator (P12-T05).

Implements exact Decimal arithmetic (ROUND_HALF_EVEN) for normalizing supplier quotes
across currencies (RUB, USD, EUR), VAT modes (with_vat, without_vat), and pack sizes.

Key invariants:
  1. Never use float for financial calculations; inputs must be Decimal, int, or numeric string.
  2. Never call an LLM or interpolate missing values: None is strictly preserved as "MISSING_PRICE"
     with human-readable label "Не указана (требуется уточнение у поставщика)".
  3. Source provenance follows the 4-layer connector architecture (P12-T08):
     - Platform.params: global API endpoint/protocol settings (admin-configured, no secrets)
     - app/integrations/catalog/: versioned service preset & form schema (developer-maintained)
     - Connection: workspace-scoped connection instance (user-configured)
     - Encrypted secret store: credentials referenced by Connection (never exposed in UI)
"""

from __future__ import annotations

from decimal import ROUND_HALF_EVEN, Decimal
from typing import Any

MISSING_PRICE = "MISSING_PRICE"
MISSING_PRICE_HUMAN_LABEL = "Цена не указана — нужен запрос поставщику"

VAT_MODE_WITH_VAT = "with_vat"
VAT_MODE_WITHOUT_VAT = "without_vat"

DEFAULT_FX_RATES_TO_RUB: dict[str, Decimal] = {
    "RUB": Decimal("1.00"),
    "USD": Decimal("92.50"),
    "EUR": Decimal("100.20"),
}

DEFAULT_VAT_RATE_PERCENT = Decimal("20")
TWO_PLACES = Decimal("0.01")


class CalculationInputError(ValueError):
    """Raised when invalid types (such as float) or negative quantities are passed."""


def to_exact_decimal(value: Decimal | int | str | None) -> Decimal | None:
    """Convert value to exact Decimal, rejecting float to prevent binary rounding drift."""
    if value is None:
        return None
    if isinstance(value, float):
        raise CalculationInputError("Float values are forbidden in financial calculations; use Decimal or str.")
    if isinstance(value, Decimal):
        return value
    if isinstance(value, int):
        return Decimal(value)
    if isinstance(value, str):
        stripped = value.strip()
        if not stripped or stripped == MISSING_PRICE:
            return None
        return Decimal(stripped)
    raise CalculationInputError(f"Unsupported numeric type: {type(value).__name__}")


def quantize_money(amount: Decimal) -> Decimal:
    """Round monetary amount to 2 decimal places using banker's rounding (ROUND_HALF_EVEN)."""
    return amount.quantize(TWO_PLACES, rounding=ROUND_HALF_EVEN)


def format_rub_human(amount: Decimal | str | None) -> str:
    """Format a normalized RUB amount or MISSING_PRICE into plain Russian text for any user."""
    if amount is None or amount == MISSING_PRICE:
        return MISSING_PRICE_HUMAN_LABEL
    dec = to_exact_decimal(amount)
    if dec is None:
        return MISSING_PRICE_HUMAN_LABEL
    q = quantize_money(dec)
    parts = f"{q:,.2f}".split(".")
    integer_part = parts[0].replace(",", " ")
    return f"{integer_part},{parts[1]} ₽"


def normalize_unit_price_to_rub_no_vat(
    unit_price: Decimal | int | str | None,
    currency: str = "RUB",
    vat_mode: str = VAT_MODE_WITHOUT_VAT,
    vat_rate_percent: Decimal | int | str = DEFAULT_VAT_RATE_PERCENT,
    units_per_pack: Decimal | int | str = Decimal("1"),
    fx_rates: dict[str, Decimal] | None = None,
) -> Decimal | str:
    """Normalize a supplier quote price into 1 base unit in RUB without VAT.

    Returns MISSING_PRICE if unit_price is None.
    """
    raw_price = to_exact_decimal(unit_price)
    if raw_price is None:
        return MISSING_PRICE

    pack_size = to_exact_decimal(units_per_pack)
    if pack_size is None or pack_size <= Decimal("0"):
        raise CalculationInputError("units_per_pack must be greater than zero.")

    rates = fx_rates or DEFAULT_FX_RATES_TO_RUB
    curr_code = currency.strip().upper()
    if curr_code not in rates:
        raise CalculationInputError(f"Unsupported currency code: {currency}")

    fx_rate = to_exact_decimal(rates[curr_code])
    assert fx_rate is not None

    # 1. Price per single base unit in original currency
    base_unit_in_orig = raw_price / pack_size

    # 2. Convert to RUB
    price_in_rub = base_unit_in_orig * fx_rate

    # 3. Strip VAT if quote includes VAT
    if vat_mode == VAT_MODE_WITH_VAT:
        vat_rate = to_exact_decimal(vat_rate_percent)
        assert vat_rate is not None
        divisor = Decimal("1") + (vat_rate / Decimal("100"))
        price_in_rub_no_vat = price_in_rub / divisor
    elif vat_mode == VAT_MODE_WITHOUT_VAT:
        price_in_rub_no_vat = price_in_rub
    else:
        raise CalculationInputError(f"Unsupported vat_mode: {vat_mode}")

    return quantize_money(price_in_rub_no_vat)


def calculate_row_total_rub_no_vat(
    unit_price_rub_no_vat: Decimal | str | None,
    requested_qty: Decimal | int | str,
) -> Decimal | str:
    """Calculate total line cost in RUB without VAT, preserving MISSING_PRICE."""
    if unit_price_rub_no_vat is None or unit_price_rub_no_vat == MISSING_PRICE:
        return MISSING_PRICE
    unit_dec = to_exact_decimal(unit_price_rub_no_vat)
    if unit_dec is None:
        return MISSING_PRICE
    qty_dec = to_exact_decimal(requested_qty)
    if qty_dec is None or qty_dec <= Decimal("0"):
        raise CalculationInputError("requested_qty must be positive.")
    return quantize_money(unit_dec * qty_dec)


def build_comparison_row(
    *,
    item_name: str,
    sku: str,
    requested_qty: Decimal | int | str,
    requested_unit_label: str = "ед.",
    supplier_name: str,
    quote_freshness: str,
    pack_label: str,
    lead_time_days: int,
    unit_price: Decimal | int | str | None,
    currency: str = "RUB",
    vat_mode: str = VAT_MODE_WITHOUT_VAT,
    vat_rate_percent: Decimal | int | str = DEFAULT_VAT_RATE_PERCENT,
    units_per_pack: Decimal | int | str = Decimal("1"),
    evidence_ref: str,
    connection_label: str = "Подключение в рабочей области",
    service_preset_key: str = "email_imap",
    fx_rates: dict[str, Decimal] | None = None,
) -> dict[str, Any]:
    """Build a normalized supplier comparison row matching the web/cases.html contract."""
    normalized_unit = normalize_unit_price_to_rub_no_vat(
        unit_price=unit_price,
        currency=currency,
        vat_mode=vat_mode,
        vat_rate_percent=vat_rate_percent,
        units_per_pack=units_per_pack,
        fx_rates=fx_rates,
    )
    row_total = calculate_row_total_rub_no_vat(normalized_unit, requested_qty)

    raw_dec = to_exact_decimal(unit_price)
    if raw_dec is None:
        orig_display = MISSING_PRICE
        orig_human = "Цена не указана в документе"
        unit_rub_str = MISSING_PRICE
        total_rub_str = MISSING_PRICE
    else:
        q_orig = quantize_money(raw_dec)
        vat_text = "с НДС" if vat_mode == VAT_MODE_WITH_VAT else "без НДС"
        orig_display = f"{q_orig} {currency.upper()} ({vat_text})"
        orig_human = f"{str(q_orig).replace('.', ',')} {currency.upper()} · {vat_text}"
        unit_rub_str = f"{normalized_unit:.2f}"
        total_rub_str = f"{row_total:.2f}"

    return {
        "item_name": item_name,
        "sku": sku,
        "requested_qty": f"{requested_qty} {requested_unit_label}".strip(),
        "requested_qty_raw": str(requested_qty),
        "supplier_name": supplier_name,
        "quote_freshness": quote_freshness,
        "pack_label": pack_label,
        "lead_time_days": lead_time_days,
        "unit_price_original": orig_display,
        "unit_price_original_human": orig_human,
        "unit_price_rub_no_vat": unit_rub_str,
        "unit_price_rub_no_vat_human": format_rub_human(normalized_unit),
        "row_total_rub_no_vat": total_rub_str,
        "row_total_rub_no_vat_human": format_rub_human(row_total),
        "is_missing_price": normalized_unit == MISSING_PRICE,
        "evidence_ref": evidence_ref,
        "connection_label": connection_label,
        "service_preset_key": service_preset_key,
    }
