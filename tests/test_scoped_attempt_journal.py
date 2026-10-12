"""Non-analyzer attempt journal: chat fallback tries, payer binding and pricing; no DB.

Run: python tests/test_scoped_attempt_journal.py
The journal module is loaded isolated (stdlib only). `chat_with_fallback` and
`journal_chat_attempt` are executed from their exact source with local doubles;
no app, SQLAlchemy, database, provider or network import happens.
"""

import ast
import logging
import sys
import unittest
from decimal import Decimal
from pathlib import Path
from types import ModuleType
from types import SimpleNamespace as NS
from typing import Any, Optional
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

from source_import_isolation import load_isolated_source

ROOT = Path(__file__).resolve().parents[1]
journal = load_isolated_source("_scoped_attempt_journal_test", ROOT / "app/services/ai/attempt_journal.py")

PRIVATE = "PRIVATE-CUSTOMER-SECRET"
PRICED = {"id": 5, "input_cost_per_1k": Decimal("0.5"), "output_cost_per_1k": Decimal("1.5")}
FREE = {"id": 6, "input_cost_per_1k": Decimal("0"), "output_cost_per_1k": Decimal("0")}


class Journal:
    def __init__(self):
        self.rows = []

    async def record_attempt(self, record):
        self.rows.append(record)
        return True


def chat_envelope(raw=None, failed=False, **kwargs):
    return journal.chat_attempt_envelope(
        model_id="m", provider_name="prov", dispatch_id=uuid4().hex, raw=raw, failed=failed, **kwargs
    )


async def record(envelopes, tariff=PRICED, tenant_id=7, purpose="agent_chat"):
    sink = Journal()
    created = await journal.record_scoped_attempts(
        envelopes,
        purpose=purpose,
        tenant_id=tenant_id,
        journal=sink,
        tariff_resolver=AsyncMock(side_effect=lambda ids: {model_id: tariff for model_id in ids}),
    )
    return created, sink.rows


class ScopedAttemptJournalTests(unittest.IsolatedAsyncioTestCase):
    async def test_chat_success_priced_from_raw_usage_with_payer_and_purpose(self):
        raw = {
            "usage": {"prompt_tokens": 1000, "completion_tokens": 2000},
            "choices": [{"message": {"content": PRIVATE}}],
        }
        created, rows = await record({journal.STAGE_CHAT: chat_envelope(raw)})
        self.assertEqual(created, 1)
        row = rows[0]
        self.assertEqual((row["tenant_id"], row["purpose"], row["stage"]), (7, "agent_chat", "chat"))
        self.assertEqual((row["prompt_tokens"], row["completion_tokens"]), (1000, 2000))
        self.assertEqual((row["outcome"], row["cost_certainty"]), ("succeeded", "priced"))
        self.assertEqual(row["cost_usd"], Decimal("3.5"))
        self.assertEqual((row["llm_model_row_id"], row["input_cost_per_1k"]), (5, Decimal("0.5")))
        self.assertIsNone(row["source_id"])
        self.assertNotIn(PRIVATE, repr(row))

    async def test_anthropic_usage_fields(self):
        raw = {"usage": {"input_tokens": 10, "output_tokens": 4}}
        _, rows = await record({"chat": chat_envelope(raw, usage_fields=("input_tokens", "output_tokens"))})
        self.assertEqual((rows[0]["prompt_tokens"], rows[0]["completion_tokens"]), (10, 4))

    async def test_missing_usage_is_unknown_never_projection_zero(self):
        _, rows = await record({"chat": chat_envelope({"choices": []})})
        self.assertEqual((rows[0]["prompt_tokens"], rows[0]["completion_tokens"]), (None, None))
        self.assertEqual((rows[0]["outcome"], rows[0]["cost_certainty"]), ("unknown", "undefined"))
        self.assertIsNone(rows[0]["cost_usd"])

    async def test_explicit_zero_table_is_free_even_without_usage(self):
        _, rows = await record({"chat": chat_envelope({})}, tariff=FREE)
        self.assertEqual((rows[0]["cost_certainty"], rows[0]["cost_usd"]), ("free", Decimal(0)))

    async def test_empty_table_without_model_price_is_undefined(self):
        raw = {"usage": {"prompt_tokens": 3, "completion_tokens": 3}}
        _, rows = await record({"chat": chat_envelope(raw)}, tariff=None)
        self.assertEqual((rows[0]["cost_certainty"], rows[0]["cost_usd"]), ("undefined", None))

    async def test_model_reported_price_arbitrates_including_explicit_zero(self):
        paid = {"usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": "0.25"}}
        zero = {"usage": {"prompt_tokens": 1, "completion_tokens": 1, "cost": 0}}
        _, rows = await record({"a": chat_envelope(paid), "b": chat_envelope(zero)}, tariff=PRICED)
        self.assertEqual((rows[0]["cost_certainty"], rows[0]["cost_usd"]), ("priced", Decimal("0.25")))
        self.assertEqual((rows[1]["cost_certainty"], rows[1]["cost_usd"]), ("free", Decimal(0)))

    async def test_failed_try_is_failed_transport_with_undefined_cost(self):
        _, rows = await record({"chat": chat_envelope(failed=True)})
        self.assertEqual((rows[0]["outcome"], rows[0]["cost_certainty"]), ("failed_transport", "undefined"))
        self.assertEqual((rows[0]["prompt_tokens"], rows[0]["cost_usd"]), (None, None))

    async def test_missing_payer_is_refused_before_any_write(self):
        for tenant_id in (None, "7", True):
            with self.subTest(tenant_id=tenant_id):
                with self.assertRaises(journal.MissingAttemptPayer):
                    await record({"chat": chat_envelope({})}, tenant_id=tenant_id)

    async def test_distinct_dispatches_with_identical_usage_stay_distinct(self):
        raw = {"usage": {"prompt_tokens": 5, "completion_tokens": 5}}
        _, first = await record({"chat": chat_envelope(raw)})
        _, second = await record({"chat": chat_envelope(raw)})
        self.assertNotEqual(first[0]["attempt_key"], second[0]["attempt_key"])

    async def test_digest_analyze_envelope_keeps_owned_accounting(self):
        envelope = {
            "request": {"model": "m", "provider": "prov", "dispatch_id": uuid4().hex, "prompt": PRIVATE},
            "response": {"usage": {"prompt_tokens": 1000, "completion_tokens": 0}},
            "accounting_usage": {"prompt_tokens": 1000, "completion_tokens": 0},
            "accounting_cost_usd": None,
            "accounting_cost_reported": False,
            "usage": {"prompt_tokens": 1000, "completion_tokens": 0, "cost": 0.5},
        }
        _, rows = await record({journal.STAGE_DIGEST_SUMMARY: envelope}, purpose=journal.PURPOSE_DIGEST)
        self.assertEqual((rows[0]["purpose"], rows[0]["stage"]), ("digest", "digest_summary"))
        self.assertEqual((rows[0]["cost_certainty"], rows[0]["cost_usd"]), ("priced", Decimal("0.5")))
        self.assertNotIn(PRIVATE, repr(rows[0]))

    async def test_best_effort_wrapper_logs_without_private_values(self):
        failing = ModuleType("tenant_context")
        failing.current_tenant_id = lambda: None
        modules = {"app": ModuleType("app"), "app.core": ModuleType("app.core"), "app.core.tenant_context": failing}
        with patch.dict(sys.modules, modules), self.assertLogs(journal.logger, "WARNING") as logs:
            self.assertEqual(await journal.journal_scoped_attempts({"chat": chat_envelope({})}, purpose="learn"), 0)
        self.assertEqual(logs.output, [f"WARNING:{journal.logger.name}:attempt_journal_payer_missing purpose=learn"])


class HTTPStatusError(Exception):
    def __init__(self, code):
        self.response = NS(status_code=code)


class AnthropicClient:
    pass


class CustomClient:
    pass


def source_functions(path, names, scope):
    tree = ast.parse((ROOT / path).read_text())
    nodes = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name in names]
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / path), "exec"), scope)
    return scope


class ChatFallbackJournalTests(unittest.IsolatedAsyncioTestCase):
    """Exact `chat_with_fallback` source: one journal call per provider try."""

    def setUp(self):
        self.scope = {
            "Any": Any,
            "Optional": Optional,
            "uuid4": uuid4,
            "logger": Mock(spec=logging.Logger),
            "httpx": NS(HTTPStatusError=HTTPStatusError, TimeoutException=TimeoutError, TransportError=OSError),
            "AnthropicClient": AnthropicClient,
            "CustomClient": CustomClient,
            "_is_tools_not_supported_error": lambda error: False,
            "default_model_sort_key": lambda m: (False, m.id),
            "_eligible_text_model": lambda m, allowed: True,
            "_allowed_model_types": AsyncMock(return_value=None),
            "LLMModel": NS(id=0, objects=NS(filter=lambda **kw: NS(order_by=lambda *a: None))),
        }
        source_functions("app/services/ai/llm_client.py", {"chat_with_fallback", "journal_chat_attempt"}, self.scope)
        self.models, self.errors, self.forwarded, self.journaled = [], {}, [], []
        self.scope["_llm_models"] = AsyncMock(side_effect=lambda query: list(self.models))
        self.scope["LLMClientFactory"] = NS(create=self.create)
        fake = ModuleType("attempt_journal")
        fake.STAGE_CHAT = journal.STAGE_CHAT
        fake.chat_attempt_envelope = journal.chat_attempt_envelope

        async def sink(envelopes, *, purpose):
            self.journaled.append((purpose, envelopes))
            return 1

        fake.journal_scoped_attempts = sink
        names = ("app", "app.services", "app.services.ai")
        self.modules = patch.dict(
            sys.modules, {**{n: ModuleType(n) for n in names}, "app.services.ai.attempt_journal": fake}
        )
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def model(self, identity, key="k"):
        return NS(
            id=identity,
            input_cost_per_1k=0,
            output_cost_per_1k=0,
            api_key=key,
            provider=NS(name="Prov"),
            model_id=f"m{identity}",
        )

    def create(self, model):
        async def chat(messages, **kwargs):
            self.forwarded.append(kwargs)
            if model.id in self.errors:
                raise self.errors[model.id]
            return {
                "content": PRIVATE,
                "usage": {"prompt_tokens": 0},
                "raw": {"usage": {"prompt_tokens": 9, "completion_tokens": 2}},
            }

        return NS(chat=chat, api_key=model.api_key, provider=model.provider, model_name=model.model_id, model=model)

    async def chat(self, **kw):
        return await self.scope["chat_with_fallback"]([{"role": "user", "content": PRIVATE}], **kw)

    async def test_failed_try_and_fallback_success_are_separate_attempts(self):
        self.models = [self.model(1), self.model(2)]
        self.errors[1] = HTTPStatusError(429)
        result = await self.chat(accounting_purpose="learn", temperature=0.2)
        self.assertEqual(result["content"], PRIVATE)
        self.assertEqual(self.forwarded, [{"tools": None, "temperature": 0.2}] * 2)
        self.assertEqual([purpose for purpose, _ in self.journaled], ["learn", "learn"])
        failed, ok = (envelopes["chat"] for _, envelopes in self.journaled)
        self.assertEqual(failed["response"], {"error": journal.CHAT_CALL_FAILED})
        self.assertEqual(ok["accounting_usage"], {"prompt_tokens": 9, "completion_tokens": 2})
        self.assertEqual((failed["request"]["model"], ok["request"]["model"]), ("m1", "m2"))
        self.assertNotEqual(failed["request"]["dispatch_id"], ok["request"]["dispatch_id"])

    async def test_nonretryable_error_is_journaled_and_still_raised(self):
        self.models = [self.model(1), self.model(2)]
        self.errors[1] = HTTPStatusError(400)
        with self.assertRaises(HTTPStatusError):
            await self.chat()
        self.assertEqual([purpose for purpose, _ in self.journaled], ["chat"])

    async def test_pre_dispatch_refusal_is_not_a_paid_attempt(self):
        self.models = [self.model(1, key=None)]
        self.errors[1] = ValueError("API key not set")
        with self.assertRaises(ValueError):
            await self.chat()
        self.assertEqual(self.journaled, [])

    async def test_journal_failure_never_breaks_the_chat(self):
        self.models = [self.model(1)]

        async def broken(envelopes, *, purpose):
            raise RuntimeError(PRIVATE)

        sys.modules["app.services.ai.attempt_journal"].journal_scoped_attempts = broken
        self.assertEqual((await self.chat())["content"], PRIVATE)
        self.scope["logger"].warning.assert_called_once_with("attempt_journal_write_failed purpose=%s", "chat")


if __name__ == "__main__":
    unittest.main()
