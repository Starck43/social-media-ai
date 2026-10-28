"""P5-T01: reject ambiguous generated JSON before typed validation loses keys.

Actual client/helper/digest source, MockTransport and pure journal builders;
module-local infrastructure doubles, no DB, paid HTTP, runtime or real sends.
"""

import json
import unittest
from pathlib import Path
from types import SimpleNamespace as NS
from unittest.mock import AsyncMock

import httpx
from tests.shared.decomposition_source_loader import load_decomposed_source as load_isolated_source

ROOT = Path(__file__).resolve().parents[2]
PRIVATE = "P5-T01-PRIVATE-GENERATED-CONTENT"
CACHE = load_isolated_source("_p5_t01_cache", ROOT / "app/services/ai/cache_pricing.py")
JOURNAL = load_isolated_source(
    "_p5_t01_journal", ROOT / "app/services/ai/attempt_journal.py", {"app.services.ai.cache_pricing": CACHE}
)
CONTRACTS = load_isolated_source("_p5_t01_contracts", ROOT / "app/services/ai/output_contracts.py")
SANITIZER = load_isolated_source("_p5_t01_sanitizer", ROOT / "app/services/ai/prompt_sanitizer.py")


def load_client():
    def validate(parsed, model, *, strict):
        return model.model_validate(parsed, strict=strict).model_dump()

    module = load_isolated_source(
        "_p5_t01_client",
        ROOT / "app/services/ai/llm_client.py",
        {
            "app.core.config": NS(settings=NS()),
            "app.models": NS(LLMModel=object),
            "app.services.ai.cache_pricing": CACHE,
            "app.services.ai.attempt_journal": JOURNAL,
            "app.services.ai.json_schema_builder": NS(validate_with_pydantic=validate),
        },
    )
    module._personal_admission = AsyncMock(return_value=(None, None))
    module._verify_personal_dispatch_day = AsyncMock()
    module._revalidate_personal_dispatch_before_http = AsyncMock()
    module._observe_personal_outcome = AsyncMock()
    module._record_llm_usage = AsyncMock()
    return module


class GeneratedJSONTests(unittest.TestCase):
    def setUp(self):
        self.module = load_client()

    def rejects(self, raw):
        with self.assertRaisesRegex(ValueError, "^invalid_structured_output$") as caught:
            self.module._try_json(raw)
        self.assertNotIn(PRIVATE, str(caught.exception))

    def test_duplicate_summary_cannot_bypass_raw_contract(self):
        raw = '{"summary":"first","summary":"second"}'
        with self.assertRaises(CONTRACTS.OutputContractError):
            CONTRACTS.validate_output(raw, CONTRACTS.DigestSummary)
        self.rejects(raw)

    def test_duplicate_nested_object_is_rejected(self):
        self.rejects('{"analysis":{"target":"first","target":"second"}}')

    def test_duplicate_inside_array_is_rejected(self):
        self.rejects('{"facts":[{"key":"first","key":"second"}]}')

    def test_unicode_escape_equivalent_keys_are_duplicates(self):
        self.rejects(r'{"summary":"first","\u0073ummary":"second"}')

    def test_duplicate_names_and_values_are_not_in_error(self):
        self.rejects(json.dumps({PRIVATE: 1})[:-1] + ',"' + PRIVATE + '":"' + PRIVATE + '"}')

    def test_same_key_in_distinct_objects_is_valid(self):
        raw = '{"facts":[{"key":"first"},{"key":"second"}]}'
        self.assertEqual(self.module._try_json(raw), json.loads(raw))

    def test_valid_json_preserves_unicode_and_field_values(self):
        raw = '{"summary":"Новость","nested":{"value":1},"items":[1,2]}'
        self.assertEqual(self.module._try_json(raw), json.loads(raw))

    def test_legacy_plain_text_and_nonobject_fallback_is_preserved(self):
        for raw in ("ordinary analysis", "not { complete json", "[1,2]", "null", "42", None):
            with self.subTest(raw=raw):
                self.assertEqual(self.module._try_json(raw), {"analysis": raw})


class AdapterContract:
    def setUp(self):
        self.module = load_client()
        cls = getattr(self.module, self.adapter)
        # Catalog/credential/admission/storage are separate infrastructure;
        # production guards are not modified by the test or implementation.
        self.client = cls.__new__(cls)
        self.client.provider = NS(name="provider", api_format=self.api_format, auth_header=None)
        self.client.model = NS(
            id=11,
            name="model",
            model_id="model",
            custom_endpoint_path="/analyze",
            input_cost_per_1k=0.001,
            output_cost_per_1k=0.002,
        )
        self.client.api_key = "synthetic-key"
        self.client.base_url = "https://provider.invalid"
        self.client.model_name = "model"
        self.client.max_tokens = 40
        self.client.default_temperature = 0.2
        self.client.timeout = 5
        self.client._rate_limit = AsyncMock()

    def install(self, generated, *, usage=True):
        if self.api_format == "openai":
            raw = {"choices": [{"message": {"content": generated}}]}
            tokens = {"prompt_tokens": 7, "completion_tokens": 3}
        elif self.api_format == "anthropic":
            raw = {"content": [{"type": "text", "text": generated}]}
            tokens = {"input_tokens": 7, "output_tokens": 3}
        else:
            raw = {"content": generated}
            tokens = {"prompt_tokens": 7, "completion_tokens": 3}
        if usage:
            raw["usage"] = {**tokens, "cost": "0.125"}
        transport = httpx.MockTransport(lambda request: httpx.Response(200, json=raw))
        self.module.httpx = NS(
            AsyncClient=lambda: httpx.AsyncClient(transport=transport),
            HTTPStatusError=httpx.HTTPStatusError,
            TimeoutException=httpx.TimeoutException,
            TransportError=httpx.TransportError,
        )

    def record(self, result):
        return JOURNAL.build_attempt_records({"text_analysis": result}, None, tenant_id=7, source_id=3)[0]

    async def test_duplicate_summary_is_rejected_before_typed_validation(self):
        self.install('{"summary":123,"summary":"' + PRIVATE + '"}')
        with self.assertLogs(self.module.logger, "WARNING") as logs:
            result = await self.client.analyze("synthetic prompt", pydantic_model=CONTRACTS.DigestSummary)
        self.assertEqual(result["parsed"], {})
        self.assertEqual(result["response"], {"error": "invalid_structured_output"})
        self.assertNotIn(PRIVATE, repr(result) + repr(logs.output))
        self.assertEqual(self.record(result)["outcome"], "failed_output")
        self.assertEqual(result["accounting_usage"], {"prompt_tokens": 7, "completion_tokens": 3})
        self.assertEqual(result["accounting_cost_usd"], "0.125")
        self.assertEqual(len(result["request"]["dispatch_id"]), 32)
        self.assertFalse(self.module._record_llm_usage.await_args.kwargs["success"])
        self.assertEqual(self.module._observe_personal_outcome.await_args.kwargs["outcome"], "failed_output")

    async def test_nested_duplicate_is_rejected_without_optional_schema(self):
        self.install('{"analysis":{"value":1,"value":2}}')
        with self.assertLogs(self.module.logger, "WARNING"):
            result = await self.client.analyze("synthetic prompt")
        self.assertEqual(result["response"]["error"], "invalid_structured_output")
        self.assertEqual(result["parsed"], {})

    async def test_failed_duplicate_keeps_unknown_accounting_unknown(self):
        self.install('{"summary":"first","summary":"second"}', usage=False)
        with self.assertLogs(self.module.logger, "WARNING"):
            result = await self.client.analyze("synthetic prompt")
        record = self.record(result)
        self.assertEqual(record["outcome"], "failed_output")
        self.assertIsNone(record["prompt_tokens"])
        self.assertIsNone(record["completion_tokens"])
        priced = JOURNAL.price_attempt(record, None)
        self.assertIsNone(priced["cost_usd"])
        self.assertEqual(priced["cost_certainty"], "undefined")

    async def test_valid_summary_is_not_redacted_or_rejected(self):
        self.install('{"summary":"News"}')
        result = await self.client.analyze("synthetic prompt", pydantic_model=CONTRACTS.DigestSummary)
        self.assertEqual(result["parsed"], {"summary": "News"})
        self.assertNotIn("error", result["response"])
        self.assertEqual(self.record(result)["outcome"], "succeeded")
        self.assertTrue(self.module._record_llm_usage.await_args.kwargs["success"])

    async def test_digest_rejects_duplicate_but_journals_the_failed_attempt(self):
        self.install('{"summary":"first","summary":"' + PRIVATE + '"}')
        journal = NS(
            PURPOSE_DIGEST=JOURNAL.PURPOSE_DIGEST,
            STAGE_DIGEST_SUMMARY=JOURNAL.STAGE_DIGEST_SUMMARY,
            journal_scoped_attempts=AsyncMock(),
        )
        builder = load_isolated_source(
            "_p5_t01_digest",
            ROOT / "app/services/digest/builder.py",
            {
                "app.core.config": NS(settings=NS()),
                "app.services.ai.llm_client": NS(LLMClientFactory=NS(create=lambda model: self.client)),
                "app.services.ai.reporting": NS(
                    ReportAggregator=object, ReportScope=object, normalize_digest_axis=lambda value: value
                ),
                "app.services.ai.output_contracts": CONTRACTS,
                "app.services.ai.prompt_sanitizer": SANITIZER,
                "app.services.digest.coverage": NS(job_coverage_note=AsyncMock()),
                "app.services.digest.render": NS(render_digest=lambda *a, **kw: ""),
                "app.services.tenancy.resolver": NS(
                    current_daily_cost_limit=AsyncMock(return_value=0), daily_cost_today=AsyncMock(return_value=0)
                ),
                "app.models": NS(
                    LLMModel=NS(objects=NS(resolve_default_model=AsyncMock(return_value=self.client.model)))
                ),
                "app.services.ai.attempt_journal": journal,
            },
        )
        with self.assertLogs(self.module.logger, "WARNING"):
            summary, info = await builder._summarize({"brief": "synthetic brief"})
        self.assertIsNone(summary)
        self.assertEqual(info["error"], "summary_provider_failed")
        self.assertEqual(info["cost"], 0.125)
        journal.journal_scoped_attempts.assert_awaited_once()
        attempts = journal.journal_scoped_attempts.await_args.args[0]
        failed = attempts[journal.STAGE_DIGEST_SUMMARY]
        self.assertEqual(failed["response"]["error"], "invalid_structured_output")
        self.assertEqual(self.record(failed)["outcome"], "failed_output")
        self.assertNotIn(PRIVATE, repr(info) + repr(failed))


class OpenAIDuplicateTests(AdapterContract, unittest.IsolatedAsyncioTestCase):
    adapter = "OpenAICompatibleClient"
    api_format = "openai"


class AnthropicDuplicateTests(AdapterContract, unittest.IsolatedAsyncioTestCase):
    adapter = "AnthropicClient"
    api_format = "anthropic"


class CustomDuplicateTests(AdapterContract, unittest.IsolatedAsyncioTestCase):
    adapter = "CustomClient"
    api_format = "custom"


if __name__ == "__main__":
    unittest.main()
