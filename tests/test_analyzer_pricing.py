"""LLM cost accounting precision (`AIAnalyzer._price_usage`).

`ai_analytics.estimated_cost` is NUMERIC(14,6) USD cents since migration 0060. It used
to be INTEGER, so a cheap-model call (well under a cent) rounded to 0 and the write path
stored NULL — "cost unknown" — which made daily SUM() under-report the real spend.
These tests pin the Decimal contract the widened column relies on. No DB: the tariff
lookup is stubbed.
"""

from decimal import Decimal
from types import SimpleNamespace

from app.services.ai import analyzer as analyzer_module
from app.services.ai.analyzer import AIAnalyzer


class _StubQuery:
    """Mimics `LLMModel.objects.filter(...).first()` without opening a session."""

    def __init__(self, model):
        self._model = model

    def filter(self, *args, **kwargs):
        return self

    async def first(self):
        return self._model


class _StubManager:
    def __init__(self, model):
        self._model = model

    def filter(self, *args, **kwargs):
        return _StubQuery(self._model)


def _stub_tariff(monkeypatch, model):
    """Replace the module-level LLMModel used for the tariff lookup."""
    monkeypatch.setattr(analyzer_module, "LLMModel", SimpleNamespace(objects=_StubManager(model)))


def _model(input_cost_per_1k, output_cost_per_1k):
    return SimpleNamespace(input_cost_per_1k=input_cost_per_1k, output_cost_per_1k=output_cost_per_1k)


def _usage(request_tokens=0, response_tokens=0, model_id="cheap-model"):
    return {"model_id": model_id, "request_tokens": request_tokens, "response_tokens": response_tokens}


class TestPriceUsage:
    async def test_sub_cent_call_keeps_its_fraction(self, monkeypatch):
        # 100 tok in @ $0.01/1K + 50 tok out @ $0.02/1K = $0.002 = 0.2 cents — the value
        # integer storage erased (int(round(0.2)) == 0, then written as NULL).
        _stub_tariff(monkeypatch, _model(Decimal("0.01"), Decimal("0.02")))

        cents = await AIAnalyzer()._price_usage([_usage(100, 50)])

        assert cents == Decimal("0.200000")
        # The write path keeps a falsy cost as NULL, so a real 0.2c call must stay > 0.
        assert cents > 0

    async def test_result_is_decimal_on_the_column_scale(self, monkeypatch):
        # Tariffs may also arrive as plain floats from hand-edited llm_models rows.
        _stub_tariff(monkeypatch, _model(0.015, 0.06))

        cents = await AIAnalyzer()._price_usage([_usage(1000, 1000)])

        assert isinstance(cents, Decimal)
        assert cents == Decimal("7.500000")
        assert cents.as_tuple().exponent == -6  # NUMERIC(14,6): 1e-8 USD resolution

    async def test_quantizes_half_up(self, monkeypatch):
        # 1 tok in @ $0.000005/1K = $0.000000005 = exactly 0.0000005 cents — the rounding tie.
        _stub_tariff(monkeypatch, _model(Decimal("0.000005"), Decimal(0)))

        assert await AIAnalyzer()._price_usage([_usage(1, 0)]) == Decimal("0.000001")

    async def test_multiple_results_sum_before_rounding(self, monkeypatch):
        # Rounding per result would lose a cent here; the total is priced once.
        _stub_tariff(monkeypatch, _model(Decimal("0.01"), Decimal(0)))

        usage = [_usage(100, 0), _usage(100, 0), _usage(100, 0)]  # 0.1c each → 0.3c

        assert await AIAnalyzer()._price_usage(usage) == Decimal("0.300000")

    async def test_unknown_model_contributes_nothing(self, monkeypatch):
        # Never fall back to hardcoded rates: an unpriced model must not invent cost.
        _stub_tariff(monkeypatch, None)

        assert await AIAnalyzer()._price_usage([_usage(5000, 5000)]) == Decimal(0)

    async def test_missing_usage_is_free(self, monkeypatch):
        _stub_tariff(monkeypatch, _model(Decimal("1"), Decimal("1")))

        empty_usage = [_usage(0, 0), _usage(500, 0, model_id=None)]

        assert await AIAnalyzer()._price_usage([]) == Decimal(0)
        assert await AIAnalyzer()._price_usage(empty_usage) == Decimal(0)
