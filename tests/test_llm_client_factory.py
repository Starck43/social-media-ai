"""Factory routing and legacy-parser boundaries for LLM calls.

Covers two verified facts about the current code:
- `LLMClientFactory.create` picks the client by `provider.api_format`
  (`openai`/`anthropic`/`custom`) and falls back to the OpenAI-compatible
  client on unknown formats. No role-based picking exists (rejected in R04).
- `run_learn`/`run_reflect` validate through strict Pydantic contracts
  (`learned_facts`/`reflection_result`) before any memory write; the tolerant
  `extract_json`/`_clamp_confidence` helpers remain for compatibility only
  and must not be called on the write path.
"""

import ast
import inspect
from types import SimpleNamespace

import pytest

from app.agent import learning
from app.services.ai.llm_client import (
    AnthropicClient,
    CustomClient,
    LLMClientFactory,
    OpenAICompatibleClient,
)


def _stub_model(api_format: str) -> SimpleNamespace:
    provider = SimpleNamespace(
        name=f"test-{api_format}",
        api_format=api_format,
        base_url="https://llm.example.test",
        get_api_key=lambda: "test-key",
    )
    return SimpleNamespace(
        provider=provider,
        name=f"model-{api_format}",
        model_id=f"model-{api_format}",
        max_tokens=512,
        default_temperature=0.2,
        custom_endpoint_path="/v1/test",
    )


@pytest.mark.parametrize(
    ("api_format", "expected_cls"),
    [
        ("openai", OpenAICompatibleClient),
        ("anthropic", AnthropicClient),
        ("custom", CustomClient),
    ],
)
def test_factory_picks_client_by_api_format(api_format, expected_cls):
    assert isinstance(LLMClientFactory.create(_stub_model(api_format)), expected_cls)


def test_factory_is_case_insensitive():
    assert isinstance(LLMClientFactory.create(_stub_model("OpenAI")), OpenAICompatibleClient)


def test_factory_falls_back_to_openai_on_unknown_format():
    assert isinstance(LLMClientFactory.create(_stub_model("mystery")), OpenAICompatibleClient)


def test_factory_requires_a_model():
    with pytest.raises(ValueError, match="LLMModel is required"):
        LLMClientFactory.create(None)


def _write_path_calls(module, func_name: str) -> set[str]:
    tree = ast.parse(inspect.getsource(module))
    calls: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == func_name:
            for child in ast.walk(node):
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    calls.add(child.func.id)
    return calls


@pytest.mark.parametrize("func_name", ["run_learn", "run_reflect"])
def test_learn_reflect_write_path_does_not_use_legacy_helpers(func_name):
    calls = _write_path_calls(learning, func_name)
    assert "extract_json" not in calls
    assert "_clamp_confidence" not in calls


def test_legacy_helpers_retained_for_compatibility():
    assert callable(learning.extract_json)
    assert learning.extract_json('{"facts": []}') == {"facts": []}
    assert learning.extract_json("no json here") is None
