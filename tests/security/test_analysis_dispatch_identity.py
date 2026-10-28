"""Actual analyze clients and journal keys with isolated imports/transports.

Run from the repository root (source-only, no root DB conftest):
PYTHONPATH=.:tests .venv/bin/python -m pytest tests/security/test_analysis_dispatch_identity.py \
    --noconftest -p no:cacheprovider -o addopts=""

Actual clients, catalog/dispatch validation, cache pricing and attempt journal;
module-local storage/settings doubles, no app bootstrap or network. Personal
billing is disabled in this fixture; no PostgreSQL/whole-ledger acceptance.
"""

import asyncio
import builtins
import hashlib
import json
import socket
import sys
import unittest
from copy import deepcopy
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
from uuid import UUID

import httpx
from tests.shared.decomposition_source_loader import load_decomposed_source as load_isolated_source

ROOT = Path(__file__).resolve().parents[2]
DAY = date(2026, 10, 12)


class Model:
    pass


class AnalysisDispatchIdentityTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        before = {name for name in sys.modules if name == "app" or name.startswith("app.")}
        self.addCleanup(
            lambda: self.assertEqual({name for name in sys.modules if name == "app" or name.startswith("app.")}, before)
        )
        normal_import = builtins.__import__

        def guarded_import(name, *args, **kwargs):
            if name == "app" or name.startswith("app."):
                raise ImportError("Undeclared application import in isolated dispatch test: " + name)
            return normal_import(name, *args, **kwargs)

        self.enterContext(patch("builtins.__import__", side_effect=guarded_import))
        self.enterContext(patch.object(socket.socket, "connect", side_effect=AssertionError("No network")))
        self.enterContext(patch.object(socket, "getaddrinfo", side_effect=AssertionError("No DNS")))
        self.cache_pricing = load_isolated_source("_dispatch_cache_pricing", ROOT / "app/services/ai/cache_pricing.py")
        self.catalog = load_isolated_source(
            "_dispatch_catalog_context",
            ROOT / "app/core/personal_catalog_context.py",
            imports={"app.core.permissions": SimpleNamespace(get_current_user=lambda: None)},
        )
        self.enterContext(self.catalog.catalog_execution_scope(7))
        tenant_context = load_isolated_source("_dispatch_tenant_context", ROOT / "app/core/tenant_context.py")

        async def fresh_model(**kwargs):
            self.assertEqual(kwargs, {"id": 4, "owner_user_id": 7, "is_active": True})
            return self.selected_model

        query = SimpleNamespace(get=AsyncMock(side_effect=fresh_model))
        Model.objects = SimpleNamespace(select_related=Mock(return_value=query))
        self.dispatch = load_isolated_source(
            "_dispatch_personal_gate",
            ROOT / "app/services/ai/personal_dispatch.py",
            imports={
                "app.core.personal_catalog_context": self.catalog,
                "app.core.tenant_context": tenant_context,
                "app.models": SimpleNamespace(
                    LLMModel=Model,
                    User=SimpleNamespace(
                        objects=SimpleNamespace(get=AsyncMock(return_value=SimpleNamespace(id=7, is_active=True)))
                    ),
                ),
            },
        )
        self.requests = []
        self.identities = []
        self.error = None
        self.status_code = 200
        self.raw = {}
        test = self

        class Transport:
            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                return False

            async def post(self, url, *, headers, json, timeout):
                test.assertEqual(test.module.uuid4.call_count, len(test.requests) + 1)
                test.requests.append((url, deepcopy(json), dict(headers)))
                await asyncio.sleep(0)
                if test.error is not None:
                    raise test.error
                return httpx.Response(
                    test.status_code,
                    json=deepcopy(test.raw),
                    request=httpx.Request("POST", url),
                )

        self.module = load_isolated_source(
            "_analysis_dispatch_clients",
            ROOT / "app/services/ai/llm_client.py",
            imports={
                "app.core.config": SimpleNamespace(
                    settings=SimpleNamespace(LLM_DEFAULT_TIMEOUT=60.0, PERSONAL_BILLING_ENABLED=False)
                ),
                "app.models": SimpleNamespace(LLMModel=Model),
                "app.core.personal_catalog_context": self.catalog,
                "app.services.ai.personal_dispatch": self.dispatch,
                "app.services.ai.cache_pricing": self.cache_pricing,
                "app.services.ai.attempt_journal": SimpleNamespace(
                    provider_cost_evidence=lambda data: test.journal.provider_cost_evidence(data),
                ),
                "app.services.ai.json_schema_builder": SimpleNamespace(
                    validate_with_pydantic=lambda parsed, model, strict: parsed,
                ),
            },
            module_imports={
                "httpx": SimpleNamespace(
                    AsyncClient=Transport,
                    TimeoutException=httpx.TimeoutException,
                    HTTPStatusError=httpx.HTTPStatusError,
                    TransportError=httpx.TransportError,
                ),
            },
        )
        self.module.logger = Mock()
        self.module._record_llm_usage = AsyncMock()

        def allocate():
            value = UUID(int=len(self.identities) + 1)
            self.identities.append(value.hex)
            return value

        self.module.uuid4 = Mock(side_effect=allocate)
        self.journal = load_isolated_source(
            "_analysis_dispatch_journal",
            ROOT / "app/services/ai/attempt_journal.py",
            imports={
                "app.models.managers.llm_attempt_manager": SimpleNamespace(LLMAttemptManager=lambda: None),
                "app.services.ai.cache_pricing": self.cache_pricing,
            },
        )

    def client(self, name):
        provider = SimpleNamespace(
            name="Synthetic",
            owner_user_id=7,
            is_active=True,
            api_format={"OpenAICompatibleClient": "openai", "AnthropicClient": "anthropic", "CustomClient": "custom"}[
                name
            ],
            base_url="https://provider.invalid",
            auth_header=None,
            get_api_key=lambda: "synthetic-test-key",
        )
        model = SimpleNamespace(
            id=4,
            owner_user_id=7,
            is_active=True,
            capabilities=["text"],
            name="synthetic-model",
            model_id="synthetic-model",
            provider=provider,
            max_tokens=20,
            default_temperature=0.3,
            custom_endpoint_path="/test",
            input_cost_per_1k=0.001,
            output_cost_per_1k=0.002,
            pricing_token_unit=1000,
            cache_read_cost_per_1m=0,
            cache_write_5m_cost_per_1m=0,
            cache_write_1h_cost_per_1m=0,
        )
        self.selected_model = model
        client = getattr(self.module, name)(model)
        client._rate_limit = AsyncMock()
        self.raw = {
            "choices": [{"message": {"content": '{"summary": "same"}'}}],
            "content": [{"type": "text", "text": '{"summary": "same"}'}],
            "usage": {
                "prompt_tokens": 120,
                "completion_tokens": 40,
                "input_tokens": 120,
                "output_tokens": 40,
                "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                "cache_read_input_tokens": 0,
                "cache_creation_input_tokens": 0,
            },
        }
        if name == "CustomClient":
            self.raw["content"] = {"summary": "same"}
        return client

    @staticmethod
    def tariff(input_rate, output_rate, **overrides):
        """Explicit 1K ordinary rates and zero cache charges for synthetic tests."""
        return {
            "id": 4,
            "input_cost_per_1k": input_rate,
            "output_cost_per_1k": output_rate,
            "pricing_token_unit": 1000,
            "cache_read_cost_per_1m": 0,
            "cache_write_5m_cost_per_1m": 0,
            "cache_write_1h_cost_per_1m": 0,
            **overrides,
        }

    def classes(self):
        return ("OpenAICompatibleClient", "AnthropicClient", "CustomClient")

    def records(self, envelope, **changes):
        context = {
            "tenant_id": 7,
            "source_id": 3,
            "analysis_date": DAY,
            "task_payload": {"job_id": 11, "agent_task_id": 5},
        }
        context.update(changes)
        return self.journal.build_attempt_records({"text_analysis": envelope}, None, **context)

    async def test_identical_calls_allocate_distinct_ids_before_http(self):
        for name in self.classes():
            with self.subTest(client=name):
                client = self.client(name)
                first = await client.analyze("same")
                second = await client.analyze("same")
                self.assertNotEqual(first["request"]["dispatch_id"], second["request"]["dispatch_id"])
                self.assertEqual(first["usage"], second["usage"])
                self.assertEqual(first["request"]["model"], "synthetic-model")
                self.assertEqual(first["request"]["provider"], "synthetic")
        self.assertEqual(len(self.identities), 6)
        self.assertEqual(len(self.requests), 6)

    async def test_generated_identity_is_not_sent_to_the_provider(self):
        for name in self.classes():
            with self.subTest(client=name):
                result = await self.client(name).analyze("same", temperature=0.2)
                identity = result["request"]["dispatch_id"]
                _, payload, headers = self.requests[-1]
                self.assertNotIn("dispatch_id", payload)
                self.assertNotIn(identity, repr(payload))
                self.assertNotIn(identity, repr(headers))
                if name != "CustomClient":
                    self.assertEqual(payload["temperature"], 0.2)

    async def test_caller_keyword_does_not_override_owned_dispatch_identity(self):
        for name in self.classes():
            with self.subTest(client=name):
                result = await self.client(name).analyze("same", dispatch_id="f" * 32)
                self.assertEqual(result["request"]["dispatch_id"], self.identities[-1])
                self.assertNotEqual(result["request"]["dispatch_id"], "f" * 32)

    async def test_timeout_envelopes_keep_owned_identity_and_model(self):
        self.error = httpx.TimeoutException("synthetic timeout")
        for name in self.classes():
            with self.subTest(client=name):
                result = await self.client(name).analyze("same")
                self.assertEqual(result["response"]["error"], "timeout")
                self.assertEqual(result["request"]["dispatch_id"], self.identities[-1])
                self.assertEqual(result["request"]["model"], "synthetic-model")
                self.assertNotIn("usage", result)
                self.assertEqual(self.records(result)[0]["outcome"], self.journal.OUTCOME_FAILED_TRANSPORT)
                self.assertEqual(set(result["request"]), {"model", "llm_model_id", "provider", "dispatch_id"})
                for sensitive in ("messages", "prompt", "endpoint"):
                    self.assertNotIn(sensitive, result["request"])

    async def test_non_success_http_envelopes_keep_the_same_allocated_identity(self):
        self.status_code = 503
        for name in self.classes():
            with self.subTest(client=name):
                result = await self.client(name).analyze("same")
                self.assertIn("error", result["response"])
                self.assertEqual(result["request"]["dispatch_id"], self.identities[-1])
        self.assertEqual(len(self.identities), len(self.requests))

    async def test_exception_envelopes_have_distinct_identities(self):
        self.error = RuntimeError("synthetic transport failure")
        for name in self.classes():
            with self.subTest(client=name):
                client = self.client(name)
                first = await client.analyze("same")
                second = await client.analyze("same")
                self.assertNotEqual(self.records(first)[0]["attempt_key"], self.records(second)[0]["attempt_key"])

    async def test_invalid_output_preserves_dispatch_identity(self):
        self.module.validate_with_pydantic = Mock(side_effect=ValueError("synthetic invalid output"))
        for name in self.classes():
            with self.subTest(client=name):
                result = await self.client(name).analyze("same", pydantic_model=Model)
                self.assertEqual(result["response"]["error"], "invalid_structured_output")
                self.assertEqual(result["parsed"], {})
                self.assertEqual(result["request"]["dispatch_id"], self.identities[-1])

    async def test_concurrent_invocations_do_not_share_a_client_identity(self):
        for name in self.classes():
            with self.subTest(client=name):
                client = self.client(name)
                results = await asyncio.gather(client.analyze("same"), client.analyze("same"))
                self.assertEqual(len({result["request"]["dispatch_id"] for result in results}), 2)

    async def test_missing_key_does_not_allocate_or_dispatch(self):
        for name in self.classes():
            with self.subTest(client=name):
                client = self.client(name)
                client.api_key = None
                with self.assertRaises(ValueError):
                    await client.analyze("same")
        self.module.uuid4.assert_not_called()
        self.assertEqual(self.requests, [])

    async def test_missing_custom_endpoint_does_not_allocate_or_dispatch(self):
        client = self.client("CustomClient")
        client.model.custom_endpoint_path = None
        with self.assertRaises(ValueError):
            await client.analyze("same")
        self.module.uuid4.assert_not_called()
        self.assertEqual(self.requests, [])

    async def test_provider_response_cannot_replace_the_owned_identity(self):
        client = self.client("OpenAICompatibleClient")
        self.raw["dispatch_id"] = "f" * 32
        result = await client.analyze("same")
        self.assertEqual(result["request"]["dispatch_id"], self.identities[0])
        self.assertNotEqual(result["request"]["dispatch_id"], result["response"]["dispatch_id"])

    async def test_identical_usage_from_separate_dispatches_has_separate_journal_keys(self):
        client = self.client("OpenAICompatibleClient")
        first = await client.analyze("same")
        second = await client.analyze("same")
        self.assertEqual(first["usage"], second["usage"])
        self.assertNotEqual(self.records(first)[0]["attempt_key"], self.records(second)[0]["attempt_key"])

    async def test_retry_identity_does_not_depend_on_usage_or_projection_fields(self):
        envelope = await self.client("OpenAICompatibleClient").analyze("same")
        changed = deepcopy(envelope)
        changed["usage"] = {"prompt_tokens": 999, "completion_tokens": 1}
        changed["request"]["model"] = "changed"
        original_key = self.records(envelope)[0]["attempt_key"]
        retried_key = self.records(
            changed,
            source_id=88,
            analysis_date=date(2026, 10, 13),
            task_payload={"job_id": 99},
        )[0]["attempt_key"]
        self.assertEqual(retried_key, original_key)
        self.assertNotEqual(self.records(envelope, tenant_id=8)[0]["attempt_key"], original_key)

    async def test_malformed_identity_is_rejected_not_replaced_with_a_new_one(self):
        envelope = await self.client("OpenAICompatibleClient").analyze("same")
        allocated = self.module.uuid4.call_count
        for invalid in ("", "short", "A" * 32, "g" * 32, True, 123):
            with self.subTest(kind=type(invalid).__name__):
                changed = deepcopy(envelope)
                changed["request"]["dispatch_id"] = invalid
                with self.assertRaisesRegex(ValueError, "Invalid analysis dispatch identity"):
                    self.records(changed)
        self.assertEqual(self.module.uuid4.call_count, allocated)

    def test_legacy_envelope_retains_its_original_key(self):
        envelope = {
            "request": {"model": "m"},
            "usage": {"prompt_tokens": 120, "completion_tokens": 40},
        }
        serialized = json.dumps(
            [7, "analysis", "text_analysis", 3, DAY.isoformat(), 11, 5, "m", 120, 40],
            sort_keys=True,
            separators=(",", ":"),
        )
        expected = hashlib.sha256(serialized.encode("utf-8")).hexdigest()
        self.assertEqual(self.records(envelope)[0]["attempt_key"], expected)
        self.assertEqual(self.records(deepcopy(envelope))[0]["attempt_key"], expected)

    async def test_persistence_retry_is_idempotent_and_another_dispatch_is_recorded(self):
        client = self.client("OpenAICompatibleClient")
        envelopes = [await client.analyze("same"), await client.analyze("same")]
        stored = {}

        async def record_attempt(record):
            self.assertNotIn("dispatch_id", record)  # Existing schema, nonce-derived key only.
            key = record["attempt_key"]
            if key in stored:
                return False
            stored[key] = deepcopy(record)
            return True

        fake = SimpleNamespace(record_attempt=record_attempt)
        resolver = AsyncMock(return_value={(4, "synthetic", "synthetic-model"): self.tariff(1, 1)})
        for envelope, expected in ((envelopes[0], 1), (envelopes[1], 1), (envelopes[0], 0)):
            created = await self.journal.record_analysis_attempts(
                {"text_analysis": envelope},
                None,
                source=SimpleNamespace(id=3, tenant_id=7),
                analysis_date=DAY,
                journal=fake,
                tariff_resolver=resolver,
            )
            self.assertEqual(created, expected)
        self.assertEqual(len(stored), 2)
        self.assertEqual(resolver.await_count, 3)
        resolver.assert_awaited_with({(4, "synthetic", "synthetic-model")})
        for record in stored.values():
            self.assertEqual(record["cost_usd"], Decimal("0.160000000"))
            self.assertEqual(record["pricing_policy_version"], "cache-tariff-v2")
            self.assertEqual(record["pricing_token_unit"], 1000)

    async def test_explicit_free_tariff_preserves_missing_usage_evidence(self):
        for name in self.classes():
            with self.subTest(client=name):
                client = self.client(name)
                self.raw.pop("usage")
                result = await client.analyze("same")
                self.assertEqual(result["usage"]["prompt_tokens"], 0)
                self.assertEqual(result["usage"]["completion_tokens"], 0)
                self.assertEqual(result["accounting_usage"], {"prompt_tokens": None, "completion_tokens": None})
                record = self.records(result)[0]
                priced = self.journal.price_attempt(record, self.tariff(0, 0))
                self.assertEqual(priced["cost_usd"], Decimal(0))
                self.assertEqual(priced["cost_certainty"], self.journal.CERTAINTY_FREE)

    async def test_partial_usage_preserves_the_known_field_without_inventing_the_other(self):
        for name in self.classes():
            with self.subTest(client=name):
                client = self.client(name)
                self.raw["usage"] = {"input_tokens" if name == "AnthropicClient" else "prompt_tokens": 17}
                result = await client.analyze("same")
                record = self.records(result)[0]
                self.assertEqual(record["prompt_tokens"], 17)
                self.assertIsNone(record["completion_tokens"])
                priced = self.journal.price_attempt(record, self.tariff(1, 1))
                self.assertIsNone(priced["cost_usd"])
                self.assertEqual(priced["cost_certainty"], self.journal.CERTAINTY_UNKNOWN_USAGE)

    async def test_malformed_provider_usage_is_not_coerced_to_accounting_tokens(self):
        for name in self.classes():
            for invalid in (None, True, -1, 1.5, "120", {}, 2147483648):
                with self.subTest(client=name, kind=type(invalid).__name__):
                    client = self.client(name)
                    prompt_field = "input_tokens" if name == "AnthropicClient" else "prompt_tokens"
                    completion_field = "output_tokens" if name == "AnthropicClient" else "completion_tokens"
                    self.raw["usage"] = {prompt_field: invalid, completion_field: 3}
                    result = await client.analyze("same")
                    record = self.records(result)[0]
                    self.assertIsNone(record["prompt_tokens"])
                    self.assertEqual(record["completion_tokens"], 3)

    async def test_non_mapping_usage_is_unknown_and_does_not_break_the_returned_envelope(self):
        for name in self.classes():
            for invalid in (None, [], "untrusted", 5):
                with self.subTest(client=name, kind=type(invalid).__name__):
                    client = self.client(name)
                    self.raw["usage"] = invalid
                    result = await client.analyze("same")
                    self.assertIsNone(self.records(result)[0]["prompt_tokens"])
                    self.assertIsNone(self.records(result)[0]["completion_tokens"])

    async def test_known_zero_token_counts_are_not_confused_with_missing_usage(self):
        for name in self.classes():
            with self.subTest(client=name):
                client = self.client(name)
                # This custom endpoint explicitly uses OpenAI-style cache counters.
                if name == "CustomClient":
                    client.provider.api_format = "openai"
                self.raw["usage"] = {
                    "prompt_tokens_details": {"cached_tokens": 0, "cache_write_tokens": 0},
                    "cache_read_input_tokens": 0,
                    "cache_creation_input_tokens": 0,
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "input_tokens": 0,
                    "output_tokens": 0,
                }
                result = await client.analyze("same")
                priced = self.journal.price_attempt(self.records(result)[0], self.tariff(1, 2))
                self.assertEqual(priced["cost_usd"], Decimal(0))
                self.assertEqual(priced["cost_certainty"], self.journal.CERTAINTY_PRICED)

    async def test_custom_reported_usage_reaches_journal_without_changing_legacy_projection(self):
        client = self.client("CustomClient")
        # A custom endpoint can explicitly declare the documented OpenAI usage format.
        client.provider.api_format = "openai"
        result = await client.analyze("same")
        self.assertEqual(result["usage"]["cost"], 0.0)
        record = self.records(result)[0]
        self.assertEqual((record["prompt_tokens"], record["completion_tokens"]), (120, 40))
        priced = self.journal.price_attempt(record, self.tariff(0.001, 0.002))
        self.assertEqual(priced["cost_usd"], Decimal("0.000200000"))

    async def test_provider_owned_accounting_field_cannot_replace_client_evidence(self):
        for name in self.classes():
            with self.subTest(client=name):
                client = self.client(name)
                self.raw.pop("usage")
                self.raw["accounting_usage"] = {"prompt_tokens": 0, "completion_tokens": 0}
                result = await client.analyze("same")
                self.assertEqual(result["accounting_usage"], {"prompt_tokens": None, "completion_tokens": None})

    async def test_invalid_structured_output_remains_failed_output_when_usage_is_missing(self):
        self.module.validate_with_pydantic = Mock(side_effect=ValueError("synthetic invalid output"))
        for name in self.classes():
            with self.subTest(client=name):
                client = self.client(name)
                self.raw.pop("usage")
                result = await client.analyze("same", pydantic_model=Model)
                self.assertEqual(self.records(result)[0]["outcome"], self.journal.OUTCOME_FAILED_OUTPUT)
                self.assertIsNone(self.records(result)[0]["prompt_tokens"])

    def test_explicit_malformed_accounting_block_never_falls_back_to_projection_zeros(self):
        for invalid in (None, [], "untrusted", {}, {"prompt_tokens": True, "completion_tokens": "0"}):
            with self.subTest(kind=type(invalid).__name__):
                envelope = {
                    "request": {"model": "m"},
                    "accounting_usage": invalid,
                    "usage": {"prompt_tokens": 0, "completion_tokens": 0},
                    "response": {"usage": {"prompt_tokens": 0, "completion_tokens": 0}},
                }
                record = self.records(envelope)[0]
                self.assertIsNone(record["prompt_tokens"])
                self.assertIsNone(record["completion_tokens"])

    def test_missing_or_invalid_tariff_rate_is_unknown_not_free(self):
        record = {"prompt_tokens": 120, "completion_tokens": 40}
        for field in ("input_cost_per_1k", "output_cost_per_1k"):
            for invalid in (None, True, -1, "bad", "", "NaN", "Infinity", "1e100", "0.000000001"):
                with self.subTest(field=field, kind=type(invalid).__name__):
                    tariff = {"id": 4, "input_cost_per_1k": 0, "output_cost_per_1k": 0, field: invalid}
                    priced = self.journal.price_attempt(record, tariff)
                    self.assertIsNone(priced["cost_usd"])
                    self.assertIsNone(priced[field])
                    self.assertEqual(priced["cost_certainty"], self.journal.CERTAINTY_UNKNOWN_TARIFF)
        priced = self.journal.price_attempt(record, {"id": 4})
        self.assertEqual(priced["cost_certainty"], self.journal.CERTAINTY_UNKNOWN_TARIFF)
        self.assertEqual(record, {"prompt_tokens": 120, "completion_tokens": 40})

    def test_tariff_snapshot_keeps_valid_rate_and_model_when_other_rate_is_unknown(self):
        priced = self.journal.price_attempt(
            {"prompt_tokens": 120, "completion_tokens": 40},
            {"id": 4, "input_cost_per_1k": "0.001", "output_cost_per_1k": None},
        )
        self.assertEqual(priced["llm_model_row_id"], 4)
        self.assertEqual(priced["input_cost_per_1k"], Decimal("0.001"))
        self.assertIsNone(priced["output_cost_per_1k"])
        self.assertIsNone(priced["cost_usd"])

    def test_unrepresentable_total_cost_is_not_inserted_or_clamped_to_free(self):
        priced = self.journal.price_attempt(
            {"prompt_tokens": 2147483647, "completion_tokens": 2147483647},
            {"id": 4, "input_cost_per_1k": "9999999999.99999999", "output_cost_per_1k": 1},
        )
        self.assertIsNone(priced["cost_usd"])
        self.assertEqual(priced["cost_certainty"], self.journal.CERTAINTY_UNKNOWN_TARIFF)

    async def test_provider_reported_cost_overrides_table_calculation_for_each_client(self):
        for name in self.classes():
            with self.subTest(client=name):
                client = self.client(name)
                self.raw["usage"]["cost"] = "0.123456789"
                result = await client.analyze("same")
                priced = self.journal.price_attempt(self.records(result)[0], self.tariff(0, 0))
                self.assertEqual(result["accounting_cost_usd"], "0.123456789")
                self.assertEqual(priced["cost_usd"], Decimal("0.123456789"))
                self.assertEqual(priced["cost_certainty"], self.journal.CERTAINTY_PRICED)
                self.assertNotIn("_reported_cost_usd", priced)

    async def test_explicit_provider_zero_is_free_without_fabricating_token_counts(self):
        for name in self.classes():
            with self.subTest(client=name):
                client = self.client(name)
                self.raw.pop("usage")
                self.raw["cost"] = 0
                result = await client.analyze("same")
                priced = self.journal.price_attempt(self.records(result)[0], self.tariff(1, 1))
                self.assertEqual(priced["cost_usd"], Decimal(0))
                self.assertEqual(priced["cost_certainty"], self.journal.CERTAINTY_FREE)
                self.assertIsNone(priced["prompt_tokens"])
                self.assertIsNone(priced["completion_tokens"])

    async def test_generated_content_and_forged_accounting_fields_are_not_provider_prices(self):
        for name in self.classes():
            with self.subTest(client=name):
                client = self.client(name)
                self.raw["accounting_cost_usd"] = "0"
                self.raw["choices"] = [{"message": {"content": '{"cost": 0}'}}]
                self.raw["content"] = (
                    {"cost": 0} if name == "CustomClient" else [{"type": "text", "text": '{"cost": 0}'}]
                )
                result = await client.analyze("same", accounting_cost_usd="0")
                self.assertIsNone(result["accounting_cost_usd"])
                priced = self.journal.price_attempt(self.records(result)[0], None)
                self.assertIsNone(priced["cost_usd"])
                self.assertEqual(priced["cost_certainty"], self.journal.CERTAINTY_UNDEFINED)

    async def test_price_evidence_does_not_change_dispatch_identity_on_replay(self):
        client = self.client("OpenAICompatibleClient")
        self.raw["usage"]["cost"] = "0.01"
        result = await client.analyze("same")
        changed = deepcopy(result)
        changed["accounting_cost_usd"] = "0.02"
        self.assertEqual(self.records(result)[0]["attempt_key"], self.records(changed)[0]["attempt_key"])

    def test_owned_missing_price_does_not_recover_from_a_projection_or_raw_replacement(self):
        envelope = {
            "request": {"model": "m"},
            "accounting_cost_usd": None,
            "usage": {"prompt_tokens": 0, "completion_tokens": 0, "cost": 0},
            "response": {"cost": 0},
        }
        priced = self.journal.price_attempt(self.records(envelope)[0], None)
        self.assertIsNone(priced["cost_usd"])
        self.assertEqual(priced["cost_certainty"], self.journal.CERTAINTY_UNDEFINED)

    async def test_invalid_legacy_projection_tariff_does_not_discard_reported_actual_cost(self):
        for name in self.classes():
            with self.subTest(client=name):
                client = self.client(name)
                client.model.input_cost_per_1k = float("nan")
                self.raw["cost"] = "0.01"
                result = await client.analyze("same")
                # The provider's own total is not an estimate: it is what was
                # charged, and the adapter passes it through even when the
                # local tariff is unusable. The journal prices the attempt.
                self.assertEqual(result["usage"]["cost"], 0.01)
                priced = self.journal.price_attempt(
                    self.records(result)[0],
                    {"id": 4, "input_cost_per_1k": float("nan"), "output_cost_per_1k": 1},
                )
                self.assertEqual(priced["cost_usd"], Decimal("0.010000000"))
                self.assertEqual(priced["cost_certainty"], self.journal.CERTAINTY_PRICED)

    async def test_v2_unit_and_cache_snapshot_prices_exactly_without_changing_identity(self):
        for name in self.classes():
            with self.subTest(client=name):
                client = self.client(name)
                if name == "CustomClient":
                    client.provider.api_format = "openai"
                result = await client.analyze("same")
                record = self.records(result)[0]
                original = deepcopy(record)
                self.assertEqual(record["pricing_policy_version"], "cache-tariff-v2")
                for unit, expected in ((1000, "0.000200000"), (1000000, "0.000000200")):
                    with self.subTest(unit=unit):
                        priced = self.journal.price_attempt(
                            record, self.tariff("0.001", "0.002", pricing_token_unit=unit)
                        )
                        self.assertEqual(priced["cost_usd"], Decimal(expected))
                        self.assertEqual(priced["cost_certainty"], self.journal.CERTAINTY_PRICED)
                        self.assertEqual(priced["pricing_token_unit"], unit)
                        for field in self.cache_pricing.CACHE_RATE_FIELDS:
                            self.assertEqual(priced[field], Decimal(0))
                        self.assertEqual(priced["attempt_key"], record["attempt_key"])
                self.assertEqual(record, original)

    async def test_missing_cache_evidence_is_undefined_not_invented_zero(self):
        for name in self.classes():
            with self.subTest(client=name):
                result = await self.client(name).analyze("same")
                envelope = deepcopy(result)
                # The owned cache block stays unknown; ordinary token counts are not enough.
                envelope["accounting_cache_usage"] = self.cache_pricing.normalize_cache_usage({}, None)
                priced = self.journal.price_attempt(self.records(envelope)[0], self.tariff(1, 2))
                self.assertIsNone(priced["cost_usd"])
                self.assertEqual(priced["cost_certainty"], self.journal.CERTAINTY_UNDEFINED)
                self.assertEqual(priced["pricing_policy_version"], "cache-tariff-v2")
                self.assertEqual(priced["pricing_token_unit"], 1000)

    async def test_unknown_custom_format_does_not_imply_a_known_cache_protocol(self):
        result = await self.client("CustomClient").analyze("same")
        self.assertEqual(result["accounting_cache_usage"]["format"], "unknown")
        record = self.records(result)[0]
        priced = self.journal.price_attempt(record, self.tariff("0.001", "0.002"))
        self.assertEqual((priced["prompt_tokens"], priced["completion_tokens"]), (120, 40))
        self.assertIsNone(priced["cost_usd"])
        self.assertEqual(priced["cost_certainty"], self.journal.CERTAINTY_UNDEFINED)

    async def test_explicit_null_or_invalid_v2_unit_is_rejected_not_defaulted(self):
        client = self.client("OpenAICompatibleClient")
        record = self.records(await client.analyze("same"))[0]
        for unit in (None, True, 0, 1, "1000", 1000.0):
            with self.subTest(unit=repr(unit)):
                with self.assertRaises(ValueError):
                    self.journal.price_attempt(record, self.tariff(1, 2, pricing_token_unit=unit))

    def test_legacy_policy_retains_1k_semantics_and_null_unit(self):
        record = self.records({"request": {"model": "m"}, "usage": {"prompt_tokens": 120, "completion_tokens": 40}})[0]
        self.assertEqual(record["pricing_policy_version"], "provider-price-v1")
        priced = self.journal.price_attempt(record, self.tariff("0.001", "0.002", pricing_token_unit=1000000))
        self.assertEqual(priced["cost_usd"], Decimal("0.000200000"))
        self.assertIsNone(priced["pricing_token_unit"])
        self.assertEqual(priced["attempt_key"], record["attempt_key"])

    def test_fixture_does_not_bypass_actual_personal_catalog_ownership(self):
        client = self.client("OpenAICompatibleClient")
        client.model.owner_user_id = 8
        with self.assertRaises(PermissionError):
            self.module.OpenAICompatibleClient(client.model)
        self.assertEqual(self.requests, [])
        self.assertEqual(self.identities, [])

    async def test_free_v2_snapshot_requires_explicit_zero_cache_rates(self):
        for name in self.classes():
            with self.subTest(client=name):
                client = self.client(name)
                self.raw.pop("usage")
                record = self.records(await client.analyze("same"))[0]
                for field in self.cache_pricing.CACHE_RATE_FIELDS:
                    with self.subTest(missing_rate=field):
                        priced = self.journal.price_attempt(record, self.tariff(0, 0, **{field: None}))
                        self.assertIsNone(priced["cost_usd"])
                        self.assertEqual(priced["cost_certainty"], self.journal.CERTAINTY_UNDEFINED)
                        self.assertIsNone(priced[field])

    async def test_absent_legacy_double_unit_defaults_but_receipt_stores_explicit_unit(self):
        record = self.records(await self.client("OpenAICompatibleClient").analyze("same"))[0]
        tariff = self.tariff("0.001", "0.002")
        tariff.pop("pricing_token_unit")
        priced = self.journal.price_attempt(record, tariff)
        self.assertEqual(priced["pricing_token_unit"], 1000)
        self.assertEqual(priced["cost_usd"], Decimal("0.000200000"))
        self.assertEqual(priced["pricing_policy_version"], "cache-tariff-v2")

    async def test_cache_rate_remains_per_million_when_ordinary_rate_unit_changes(self):
        client = self.client("OpenAICompatibleClient")
        self.raw["usage"]["prompt_tokens_details"]["cached_tokens"] = 40
        record = self.records(await client.analyze("same"))[0]
        for unit, expected in ((1000, "0.000240000"), (1000000, "0.000080160")):
            with self.subTest(unit=unit):
                priced = self.journal.price_attempt(
                    record, self.tariff("0.001", "0.002", pricing_token_unit=unit, cache_read_cost_per_1m=2)
                )
                self.assertEqual(priced["cost_usd"], Decimal(expected))
                self.assertEqual(priced["cost_certainty"], self.journal.CERTAINTY_PRICED)
                self.assertEqual(priced["cache_read_cost_per_1m"], Decimal(2))


if __name__ == "__main__":
    unittest.main(verbosity=2)
