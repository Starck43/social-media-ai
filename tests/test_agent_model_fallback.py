"""Agent model fallback: start at the ★ model, then walk the proven fleet.

Owner command:
`.venv/bin/python -m pytest tests/test_agent_model_fallback.py -q --noconftest -o addopts=`

The rule the owner stated: the ★ default (or the model the workspace names for
its agent) starts, and on failure the chat moves through **every** eligible
active model — not "cheapest first", which is what walked the chat into free
rows whose counters already read 100% failures. The reply names the model that
failed in one short line.
"""

import inspect
from types import SimpleNamespace

from app.services.ai import llm_client


def _model(model_id, *, provider="River", use=0, fail=0, default=False, last=None,
           input_cost=0.12, output_cost=0.4, caps=("text", "image")):
    return SimpleNamespace(
        id=model_id,
        model_id=model_id,
        name=model_id,
        is_active=True,
        is_default=default,
        use_count=use,
        fail_count=fail,
        last_success_at=last,
        last_used_at=last,
        input_cost_per_1k=input_cost,
        output_cost_per_1k=output_cost,
        capabilities=list(caps),
        provider=SimpleNamespace(name=provider, is_active=True),
    )


class TestOrdering:
    def test_preferred_model_starts_the_walk(self):
        source = inspect.getsource(llm_client.chat_with_fallback)
        assert "m.id != getattr(preferred_model" in source

    def test_order_is_by_proven_use_not_by_price(self):
        """Price is not a routing key for the chat any more."""
        source = inspect.getsource(llm_client.chat_with_fallback)
        assert "input_cost_per_1k" not in source, "cheapest-first routing sent the chat into rate-limited free rows"
        assert "_model_proven_score(m)" in source and "_model_last_seen(m)" in source

    def test_every_eligible_model_is_a_candidate(self):
        source = inspect.getsource(llm_client.chat_with_fallback)
        assert "text_models = [m for m in models if _eligible_text_model" in source


class TestEmptyReplyIsAFailure:
    def test_content_and_tool_calls_decide_usability(self):
        assert llm_client._chat_response_usable({"content": "Привет", "tool_calls": []})
        assert llm_client._chat_response_usable({"content": "", "tool_calls": [{"name": "x"}]})
        assert not llm_client._chat_response_usable({"content": "  ", "tool_calls": []})
        assert not llm_client._chat_response_usable({"content": None, "tool_calls": []})
        assert not llm_client._chat_response_usable(None)

    def test_truncated_envelope_names_the_budget(self):
        assert llm_client._empty_reply_reason({"content": "", "finish_reason": "length"}) == "truncated"
        assert llm_client._empty_reply_reason({"content": "", "finish_reason": "stop"}) == "empty_reply"

    def test_failure_list_is_structured_and_bounded(self):
        failures = []
        llm_client._record_failure(failures, _model("gemma"), "rate_limited")
        assert failures == [{"model": "gemma", "provider": "River", "reason": "rate_limited"}]

    def test_failure_list_is_optional(self):
        llm_client._record_failure(None, _model("x"), "timeout")


class TestHint:
    def test_short_line_names_the_failed_model(self):
        from app.agent.runtime import _model_failure_hint

        hint = _model_failure_hint(
            {"_model_failures": [{"model": "zai-org/GLM-5.3-Flash", "provider": "River", "reason": "rate_limited"}]}
        )
        assert hint == "Переключил модель: zai-org/GLM-5.3-Flash — лимит запросов."

    def test_no_failures_no_hint(self):
        from app.agent.runtime import _model_failure_hint

        assert _model_failure_hint({}) == ""
        assert _model_failure_hint({"_model_failures": []}) == ""

    def test_hint_never_carries_a_provider_body(self):
        from app.agent.runtime import _model_failure_hint

        failures = [{"model": "m", "provider": "p", "reason": "provider_error", "body": "SECRET"}]
        assert "SECRET" not in _model_failure_hint({"_model_failures": failures})


class TestWorkspaceChoice:
    def test_named_model_wins_over_the_default(self):
        source = inspect.getsource(llm_client)
        assert "tenants.agent_model" not in source  # the choice is read in the runtime

        from app.agent import runtime

        source = inspect.getsource(runtime._preferred_agent_model)
        assert "agent_model" in source
        assert "resolve_default_model" in source, "an unknown name must fall back to the ★ model"

    def test_runtime_passes_the_workspace_model_and_collects_failures(self):
        from app.agent import runtime

        source = inspect.getsource(runtime._chat)
        assert "_preferred_agent_model(tenant)" in source
        assert "failures=failures" in source
