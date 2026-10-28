"""AUD-06: actual analyze adapters, synthetic HTTP and no DB/provider writes.

Module-local infrastructure doubles do not alter global imports. Real httpx
MockTransport, Pydantic validation exceptions and pure journal builders exercise
privacy, outcome and accounting seams; this is not PostgreSQL acceptance.
"""

import asyncio
import json
import unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import httpx
from pydantic import BaseModel
from tests.shared.decomposition_source_loader import load_decomposed_source as load_isolated_source

ROOT = Path(__file__).resolve().parents[2]
SENTINELS = (
    "TOKEN-SENTINEL-P10-T06",
    "postgresql://dsn-user:dsn-secret@private.invalid/database",
    "PROMPT-SENTINEL-P10-T06",
    "https://media.invalid/private?signature=MEDIA-SENTINEL-P10-T06",
)
PRIVATE = " ".join(SENTINELS)
CACHE = load_isolated_source("_p10_t06_cache", ROOT / "app/services/ai/cache_pricing.py")
JOURNAL = load_isolated_source(
    "_p10_t06_journal",
    ROOT / "app/services/ai/attempt_journal.py",
    {"app.services.ai.cache_pricing": CACHE},
)


SCENARIO_SCHEMA = load_isolated_source("_p10_t06_scenario_schema", ROOT / "app/services/ai/scenario_schema.py")
SCHEMA = load_isolated_source(
    "_p10_t06_json_schema",
    ROOT / "app/services/ai/json_schema_builder.py",
    {"app.services.ai.scenario_schema": SCENARIO_SCHEMA},
)


class Output(BaseModel):
    score: int


def validate(parsed, model, *, strict):
    # Deliberately use the real, input-bearing Pydantic exception: even a
    # validator that exposes input must not cross the adapter's diagnostic seam.
    assert strict is True
    return model.model_validate(parsed).model_dump()


class AdapterPrivacyContract:
    def setUp(self):
        self.module = load_isolated_source(
            "_p10_t06_client",
            ROOT / "app/services/ai/llm_client.py",
            {
                "app.core.config": SimpleNamespace(settings=SimpleNamespace()),
                "app.models": SimpleNamespace(LLMModel=object),
                "app.services.ai.cache_pricing": CACHE,
                "app.services.ai.json_schema_builder": SimpleNamespace(validate_with_pydantic=validate),
                "app.services.ai.attempt_journal": JOURNAL,
            },
        )
        # Only analyze is owned: constructor catalog/credential validation and
        # admission/storage are independent infrastructure seams, not bypasses
        # offered by application code.
        self.client = getattr(self.module, self.adapter).__new__(getattr(self.module, self.adapter))
        self.client.provider = SimpleNamespace(name="provider", api_format=self.api_format, auth_header=None)
        self.client.model = SimpleNamespace(
            id=23,
            name="model",
            custom_endpoint_path="/analyze",
            input_cost_per_1k=0.001,
            output_cost_per_1k=0.002,
            cache_read_cost_per_1m=0.0001,
            cache_write_5m_cost_per_1m=0.0002,
            cache_write_1h_cost_per_1m=0.0003,
        )
        self.client.model_name = "model"
        self.client.api_key = SENTINELS[0]
        self.client.base_url = "https://provider.invalid"
        self.client.max_tokens = 40
        self.client.default_temperature = 0.3
        self.client.timeout = 5
        self.client._rate_limit = AsyncMock()
        self.module._personal_admission = AsyncMock(return_value=(None, None))
        self.module._verify_personal_dispatch_day = AsyncMock()
        self.module._revalidate_personal_dispatch_before_http = AsyncMock()
        self.module._observe_personal_failure = AsyncMock()
        self.module._observe_cancelled_personal = AsyncMock()
        self.module._observe_personal_outcome = AsyncMock()
        self.module._record_llm_usage = AsyncMock()
        self.requests = []

    def install_http(self, *, raw=None, status=200, error=None, text=None):
        def handle(request):
            self.requests.append(request)
            if error is not None:
                raise error
            if text is not None:
                return httpx.Response(status, text=text)
            return httpx.Response(status, json=raw)

        transport = httpx.MockTransport(handle)
        self.module.httpx = SimpleNamespace(
            AsyncClient=lambda: httpx.AsyncClient(transport=transport),
            TimeoutException=httpx.TimeoutException,
            HTTPStatusError=httpx.HTTPStatusError,
            TransportError=httpx.TransportError,
        )

    def raw(self, content=None, *, usage=True):
        content = content if content is not None else {"score": PRIVATE}
        text = json.dumps(content)
        if self.api_format == "openai":
            raw = {"choices": [{"message": {"content": text}}]}
            tokens = {"prompt_tokens": 12, "completion_tokens": 4, "prompt_tokens_details": {"cached_tokens": 3}}
        elif self.api_format == "anthropic":
            raw = {"content": [{"type": "text", "text": text}]}
            tokens = {"input_tokens": 12, "output_tokens": 4, "cache_read_input_tokens": 3}
        else:
            raw = {"content": text}
            tokens = {"prompt_tokens": 12, "completion_tokens": 4}
        if usage:
            raw["usage"] = {**tokens, "cost": "0.125"}
        return raw

    async def invoke(self, **kwargs):
        with self.assertLogs(self.module.logger, level="WARNING") as logs:
            result = await self.client.analyze(PRIVATE, media_urls=[SENTINELS[3]], **kwargs)
        self.assert_private_absent(result, logs.output)
        self.assertEqual(set(result["request"]), {"model", "llm_model_id", "provider", "dispatch_id"})
        self.assertEqual(result["request"]["llm_model_id"], 23)
        self.assertEqual(len(result["request"]["dispatch_id"]), 32)
        self.assertEqual(result["parsed"], {})
        # No traceback/exception objects are retained by operational log records.
        self.assertTrue(all(record.exc_info is None for record in logs.records))
        self.module._record_llm_usage.assert_awaited_once()
        self.assertFalse(self.module._record_llm_usage.await_args.kwargs["success"])
        return result

    def assert_private_absent(self, *values):
        rendered = repr(values)
        for sentinel in SENTINELS:
            self.assertNotIn(sentinel, rendered)

    def record(self, result):
        records = JOURNAL.build_attempt_records(
            {"text_analysis": result},
            None,
            tenant_id=7,
            source_id=3,
        )
        self.assertEqual(len(records), 1)
        return records[0]

    def assert_transport(self, result, code):
        self.assertEqual(result["response"], {"error": code})
        self.assertEqual(result["accounting_usage"], {"prompt_tokens": None, "completion_tokens": None})
        self.assertIsNone(result["accounting_cost_usd"])
        self.assertNotIn("usage", result)
        record = self.record(result)
        self.assertEqual(record["outcome"], "failed_transport")
        priced = JOURNAL.price_attempt(record, {"id": 23, "input_cost_per_1k": 0.001, "output_cost_per_1k": 0.002})
        self.assertIsNone(priced["cost_usd"])
        self.assertEqual(priced["cost_certainty"], "undefined")
        self.module._observe_personal_failure.assert_awaited_once_with(None)
        self.module._observe_personal_outcome.assert_not_awaited()

    async def test_http_error_body_and_request_are_not_diagnostics(self):
        self.install_http(raw={"error": PRIVATE}, status=429)
        result = await self.invoke()
        self.assert_transport(result, "http_error")
        self.assertIn(SENTINELS[2], self.requests[0].content.decode())  # actually dispatched to the fake transport

    async def test_timeout_message_and_signed_url_are_private(self):
        self.install_http(error=httpx.ReadTimeout(PRIVATE))
        self.assert_transport(await self.invoke(), "timeout")

    async def test_transport_exception_and_dsn_are_private(self):
        self.install_http(error=httpx.ConnectError(PRIVATE))
        self.assert_transport(await self.invoke(), "transport_error")

    async def test_unexpected_exception_is_allowlisted(self):
        self.install_http(error=RuntimeError(PRIVATE))
        self.assert_transport(await self.invoke(), "provider_request_failed")

    async def test_json_decode_failure_has_no_body_echo(self):
        self.install_http(text=PRIVATE)
        self.assert_transport(await self.invoke(), "provider_request_failed")

    async def test_validation_failure_preserves_usage_cost_cache_and_outcome(self):
        self.install_http(raw=self.raw())
        result = await self.invoke(pydantic_model=Output)
        self.assertEqual(result["response"], {"error": "invalid_structured_output"})
        self.assertEqual(result["accounting_usage"], {"prompt_tokens": 12, "completion_tokens": 4})
        self.assertEqual(Decimal(result["accounting_cost_usd"]), Decimal("0.125"))
        self.assertTrue(result["accounting_cost_reported"])
        if self.api_format != "custom":
            self.assertEqual(result["accounting_cache_usage"]["cache_read_tokens"], 3)
        record = self.record(result)
        self.assertEqual(record["outcome"], "failed_output")
        self.assertEqual(record["model_row_id"], 23)
        priced = JOURNAL.price_attempt(record, None)
        self.assertEqual(priced["cost_usd"], Decimal("0.125000000"))
        self.assertEqual(priced["cost_certainty"], "priced")
        observed = self.module._observe_personal_outcome.await_args.kwargs
        self.assertEqual(observed["outcome"], "failed_output")
        self.assertEqual(observed["dispatch_state"], "response_received")
        self.assertIs(observed["accounting_envelope"], result)

    async def test_real_strict_validator_failure_is_private(self):
        self.module.validate_with_pydantic = SCHEMA.validate_with_pydantic
        self.install_http(raw=self.raw())
        result = await self.invoke(pydantic_model=Output)
        self.assertEqual(result["response"]["error"], "invalid_structured_output")
        self.assertEqual(self.record(result)["outcome"], "failed_output")
        self.assertEqual(result["accounting_usage"]["prompt_tokens"], 12)

    async def test_real_strict_validator_success_preserves_output(self):
        self.module.validate_with_pydantic = SCHEMA.validate_with_pydantic
        raw = self.raw({"score": 1})
        self.install_http(raw=raw)
        result = await self.client.analyze(PRIVATE, pydantic_model=Output)
        self.assertEqual(result["parsed"], {"score": 1})
        self.assertEqual(result["response"], raw)
        self.assertTrue(self.module._record_llm_usage.await_args.kwargs["success"])

    async def test_unexpected_validation_exception_is_also_private(self):
        self.module.validate_with_pydantic = Mock(side_effect=RuntimeError(PRIVATE))
        self.install_http(raw=self.raw({"score": 1}))
        result = await self.invoke(pydantic_model=Output)
        self.assertEqual(result["response"]["error"], "invalid_structured_output")
        self.assertEqual(self.record(result)["outcome"], "failed_output")

    async def test_provider_error_with_http_200_never_echoes_body(self):
        raw = self.raw()
        raw["error"] = {"message": PRIVATE, "url": SENTINELS[3]}
        self.install_http(raw=raw)
        result = await self.invoke()
        self.assertEqual(self.record(result)["outcome"], "failed_output")
        self.assertEqual(result["accounting_usage"]["prompt_tokens"], 12)

    async def test_nonobject_output_fails_closed(self):
        self.install_http(raw=[PRIVATE])
        result = await self.invoke()
        self.assertEqual(result["response"]["error"], "invalid_structured_output")
        self.assertEqual(self.record(result)["outcome"], "failed_output")

    async def test_unknown_output_accounting_stays_unknown_not_free(self):
        self.install_http(raw=self.raw(usage=False))
        result = await self.invoke(pydantic_model=Output)
        record = self.record(result)
        self.assertEqual(record["outcome"], "failed_output")
        self.assertIsNone(record["prompt_tokens"])
        self.assertIsNone(record["completion_tokens"])
        priced = JOURNAL.price_attempt(record, None)
        self.assertIsNone(priced["cost_usd"])
        self.assertEqual(priced["cost_certainty"], "undefined")

    async def test_malformed_usage_cost_evidence_never_echoes_sentinels(self):
        raw = self.raw()
        raw["usage"] = dict(
            prompt_tokens=PRIVATE, completion_tokens=PRIVATE, input_tokens=PRIVATE, output_tokens=PRIVATE, cost=PRIVATE
        )
        self.install_http(raw=raw)
        result = await self.invoke(pydantic_model=Output)
        self.assertIsNone(result["accounting_usage"]["prompt_tokens"])
        self.assertIsNone(result["accounting_cost_usd"])
        # Preserve the journal's existing evidence semantics: a nonnumeric
        # string is not a recognized price report. Do not invent a claim.
        self.assertEqual(
            result["accounting_cost_reported"], self.client._accounting_cost(raw)["accounting_cost_reported"]
        )
        self.assertIsNone(self.record(result)["prompt_tokens"])

    async def test_unresolved_currency_cannot_become_free(self):
        raw = self.raw()
        raw["usage"]["currency"] = PRIVATE
        self.install_http(raw=raw)
        result = await self.invoke(pydantic_model=Output)
        self.assertTrue(result["accounting_cost_reported"])
        self.assertIsNone(result["accounting_cost_usd"])
        priced = JOURNAL.price_attempt(self.record(result), {"input_cost_per_1k": 0, "output_cost_per_1k": 0})
        self.assertIsNone(priced["cost_usd"])
        self.assertEqual(priced["cost_certainty"], "undefined")

    async def test_explicit_reported_zero_is_preserved(self):
        raw = self.raw()
        raw["usage"]["cost"] = 0
        self.install_http(raw=raw)
        result = await self.invoke(pydantic_model=Output)
        self.assertTrue(result["accounting_cost_reported"])
        self.assertEqual(Decimal(result["accounting_cost_usd"]), Decimal(0))
        priced = JOURNAL.price_attempt(self.record(result), None)
        self.assertEqual(priced["cost_usd"], Decimal(0))
        self.assertEqual(priced["cost_certainty"], "free")

    async def test_failure_dispatch_identity_is_stable_on_rejournal(self):
        self.install_http(error=RuntimeError(PRIVATE))
        result = await self.invoke()
        first = self.record(result)
        self.assertEqual(first["attempt_key"], self.record(result)["attempt_key"])
        # A fresh invocation is a new attempt even for identical prompt/failure.
        self.module._record_llm_usage.reset_mock()
        self.module._observe_personal_failure.reset_mock()
        second = await self.invoke()
        self.assertNotEqual(result["request"]["dispatch_id"], second["request"]["dispatch_id"])
        self.assertNotEqual(first["attempt_key"], self.record(second)["attempt_key"])

    async def test_reserved_dispatch_identity_survives_redaction(self):
        self.module._personal_admission.return_value = ({"receipt": "synthetic"}, "a" * 32)
        self.install_http(raw=self.raw())
        result = await self.invoke(pydantic_model=Output)
        self.assertEqual(result["request"]["dispatch_id"], "a" * 32)
        self.assertEqual(self.module._observe_personal_outcome.await_args.args[0], {"receipt": "synthetic"})

    async def test_success_content_is_intentionally_preserved(self):
        raw = self.raw({"score": 1, "analysis": PRIVATE})
        self.install_http(raw=raw)
        self.module.logger = Mock()
        result = await self.client.analyze(PRIVATE, media_urls=[SENTINELS[3]])
        self.assertEqual(result["response"], raw)
        self.assertEqual(result["parsed"]["analysis"], PRIVATE)
        self.assertEqual(result["request"]["prompt"], PRIVATE)
        self.module.logger.warning.assert_not_called()
        self.module.logger.error.assert_not_called()
        self.assertTrue(self.module._record_llm_usage.await_args.kwargs["success"])
        self.assertEqual(self.module._observe_personal_outcome.await_args.kwargs["outcome"], "succeeded")

    async def test_cancellation_propagates_without_reclassification(self):
        self.install_http(error=asyncio.CancelledError())
        with self.assertRaises(asyncio.CancelledError):
            await self.client.analyze(PRIVATE)
        self.module._observe_cancelled_personal.assert_awaited_once()
        self.module._record_llm_usage.assert_not_awaited()
        self.module._observe_personal_failure.assert_not_awaited()

    def test_unknown_error_code_and_extra_request_fields_are_not_echoed(self):
        meta = self.client._analysis_request_meta(PRIVATE)
        meta.update(endpoint=PRIVATE, messages=[PRIVATE], media_urls=[SENTINELS[3]])
        with self.assertLogs(self.module.logger, "ERROR") as logs:
            result = self.client._analyze_failure(meta, PRIVATE)
        self.assertEqual(result["response"]["error"], "provider_request_failed")
        self.assert_private_absent(result, logs.output)


class OpenAIPrivacyTests(AdapterPrivacyContract, unittest.IsolatedAsyncioTestCase):
    adapter = "OpenAICompatibleClient"
    api_format = "openai"


class AnthropicPrivacyTests(AdapterPrivacyContract, unittest.IsolatedAsyncioTestCase):
    adapter = "AnthropicClient"
    api_format = "anthropic"


class CustomPrivacyTests(AdapterPrivacyContract, unittest.IsolatedAsyncioTestCase):
    adapter = "CustomClient"
    api_format = "custom"


if __name__ == "__main__":
    unittest.main()
