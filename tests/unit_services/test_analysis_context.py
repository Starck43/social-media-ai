from types import SimpleNamespace

import pytest

from app.services.ai.analysis_context import analysis_fingerprint
from app.services.ai.dedup import filter_by_context_receipts


def _scenario(**overrides):
    values = {
        "id": 7,
        "analysis_types": ["sentiment", "topics"],
        "content_types": ["text"],
        "scope": {"brands": ["Acme"], "min_confidence": 0.6},
        "base_prompt": "Analyze {text}",
        "media_overrides": {"image": "Describe {text}"},
        "summary_prompt": "Summarize",
        "max_tokens": 256,
        "output_schema": {"type": "object", "properties": {"sentiment": {"type": "string"}}},
        "text_llm_model_id": 11,
        "image_llm_model_id": None,
        "video_llm_model_id": None,
        "llm_strategy": "cost_efficient",
        "is_active": True,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_fingerprint_ignores_order_and_operational_job_identity():
    first = analysis_fingerprint(
        _scenario(),
        trigger_config={"keywords": ["Launch", "Acme"]},
        task_payload={"brands": ["Acme", "Globex"], "agent_task_id": 1, "force_reanalyze": True},
        output_schema={"type": "object", "properties": {"x": {"type": "string"}}},
    )
    second = analysis_fingerprint(
        _scenario(),
        trigger_config={"keywords": ["Launch", "Acme"]},
        task_payload={"force_reanalyze": False, "brands": ["Acme", "Globex"], "agent_task_id": 99},
        output_schema={"properties": {"x": {"type": "string"}}, "type": "object"},
    )
    assert first == second


def test_fingerprint_changes_for_scenario_targets_trigger_or_output_contract():
    base = analysis_fingerprint(_scenario(), task_payload={"brands": ["Acme"]})
    assert analysis_fingerprint(_scenario(id=8), task_payload={"brands": ["Acme"]}) != base
    assert analysis_fingerprint(_scenario(), task_payload={"brands": ["Globex"]}) != base
    changed_trigger = analysis_fingerprint(
        _scenario(), trigger_config={"keywords": ["launch"]}, task_payload={"brands": ["Acme"]}
    )
    assert changed_trigger != base
    assert analysis_fingerprint(
        _scenario(), task_payload={"brands": ["Acme"]}, output_schema={"type": "array"}
    ) != base


@pytest.mark.asyncio
async def test_receipt_lookup_failure_propagates_before_provider_admission(monkeypatch):
    from app.models import AnalyzedContentReceipt

    async def fail_lookup(*args, **kwargs):
        raise RuntimeError("storage unavailable")

    monkeypatch.setattr(AnalyzedContentReceipt.objects, "covered_hashes", fail_lookup)
    with pytest.raises(RuntimeError, match="storage unavailable"):
        await filter_by_context_receipts(
            [{"platform": "telegram", "external_id": "1", "text": "sample"}],
            3,
            9,
            "f" * 64,
        )
