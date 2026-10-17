"""Pure checks for the scenario model picker guard (`model_suitability`).

No DB, no app import beyond the module under test: the fleet facts that make a
model unfit for analysis (classifier families, never-succeeded dispatches) are
encoded once here.
"""

from types import SimpleNamespace as NS

from app.services.ai.model_suitability import (
    analysis_capable,
    is_classifier_like,
    is_refusal_summary,
    never_succeeded,
)


def test_classifier_families_are_detected_in_name_or_model_id():
    assert is_classifier_like("nvidia/nemotron-3.5-content-safety:free")
    assert is_classifier_like("meta-llama/llama-guard-4")
    assert is_classifier_like("NVIDIA: Aegis Safety")
    assert is_classifier_like("openai/omni-moderation-latest")
    assert not is_classifier_like("nvidia/Qwen3.8-27B-NVFP4", "Qwen 3.8")
    assert not is_classifier_like("zai-org/GLM-5.3-Flash", "GLM 5.3 Flash (free)")


def test_never_succeeded_needs_three_dispatches_and_zero_wins():
    assert never_succeeded(11, 11), "11 dispatched, 11 failed — dead weight, hidden"
    assert never_succeeded(18, 18), "the same for the Gemma-style row"
    assert not never_succeeded(2, 2), "too few dispatches to judge"
    assert not never_succeeded(24, 22), "2 successes still count"
    assert not never_succeeded(None, None), "unknown counters never hide"


def test_analysis_capable_tolerates_missing_attributes():
    assert analysis_capable(NS(model_id="qwen/qwen3.8", name="Qwen", use_count=6, fail_count=0))
    assert not analysis_capable(NS(model_id="x/llama-guard-4", name="Guard", use_count=0, fail_count=0))
    assert analysis_capable(NS()), "a bare object with no counters must not be hidden"


def test_refusal_summaries_are_detected():
    # The exact wording stored in a poisoned analysis row (2026-10-06, id 1078).
    assert is_refusal_summary("Недостаточно данных для анализа. Контент не предоставлен.")
    assert is_refusal_summary("  недостаточно данных для анализа")
    assert is_refusal_summary("Insufficient data provided.")
    assert not is_refusal_summary(None)
    assert not is_refusal_summary("")
    assert not is_refusal_summary(123)
    assert not is_refusal_summary("Автор делится источниками творческого вдохновения."), (
        "a real summary about content must never be treated as a refusal"
    )
