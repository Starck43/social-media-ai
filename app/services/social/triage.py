"""Conservative deterministic ranking of authorized, already-collected social metadata.

No collection, model dispatch, body cache, incident declaration or delivery. Unknown
signals stay visible as uncertain. Recommendations use fixed bounded scores; they
are neither probabilities nor evidence of urgency.
"""

import re
from dataclasses import dataclass
from datetime import datetime

from app.services.attention.contracts import AttentionAction, AttentionItem

_INJECTION = re.compile(
    r"ignore\s+.{0,30}instructions|system\s*prompt|bypass\s+approval|"
    r"игнорируй.{0,30}(инструкции|правила)|системный\s+промпт",
    re.I,
)
_ATTENTION = re.compile(r"\b(urgent|outage|fraud|breach|срочно|авария|мошенничество|утечка)\b", re.I)
_WAIT = re.compile(r"\b(newsletter|webinar|дайджест|вебинар|анонс)\b", re.I)
_EMAIL = re.compile(r"[\w.+-]+@[\w.-]+\.[\w-]+")
_PHONE = re.compile(r"(?<!\w)\+?\d[\d ()-]{7,}\d(?!\w)")


def redacted_title(value: str) -> str:
    value = re.sub(r"[\x00-\x1f\x7f]", " ", str(value))
    value = _INJECTION.sub("[инструкция скрыта]", value)
    value = _EMAIL.sub("[email скрыт]", value)
    return _PHONE.sub("[телефон скрыт]", value)[:160].strip() or "Социальный результат"


@dataclass(frozen=True)
class SocialEvidence:
    tenant_id: int
    source_id: int
    analysis_id: int
    title: str
    observed_at: datetime
    data_boundary: str = "local_only"


def classify(evidence: SocialEvidence) -> AttentionItem:
    text = evidence.title
    if _INJECTION.search(text):
        category, score, reason = "uncertain", 60, "Подозрительные инструкции в данных; нужна ручная проверка."
    elif _ATTENTION.search(text):
        category, score, reason = (
            "needs_attention",
            80,
            "Найден маркер возможного риска; проверьте исходные свидетельства.",
        )
    elif _WAIT.search(text):
        category, score, reason = (
            "can_wait",
            20,
            "Справочный или плановый материал; приоритет рекомендован по заголовку.",
        )
    else:
        category, score, reason = "uncertain", 50, "Недостаточно сигналов в заголовке; важность требует проверки."
    return AttentionItem(
        provider="social",
        kind="analysis",
        authoritative_ref=f"analysis:{evidence.analysis_id}",
        title=redacted_title(text),
        reason=reason,
        category=category,
        recommended_priority=score,
        priority_basis="deterministic_title_v1",
        freshness=evidence.observed_at,
        allowed_actions=(
            AttentionAction("open_evidence", "Открыть свидетельства", f"/app/analytics/{evidence.analysis_id}"),
        ),
        data_boundary=evidence.data_boundary,
    )


def rank(evidence: list[SocialEvidence], *, tenant_id: int, allowed_source_ids: frozenset[int]) -> list[AttentionItem]:
    """Defense in depth: reject mixed-tenant/unselected input before classification."""
    if type(tenant_id) is not int or tenant_id <= 0:
        raise PermissionError("social_triage_workspace_required")
    unique = {}
    for row in evidence:
        if row.tenant_id != tenant_id or row.source_id not in allowed_source_ids:
            continue
        item = classify(row)
        unique[item.authoritative_ref] = item
    return sorted(
        unique.values(),
        key=lambda item: (-item.recommended_priority, -item.freshness.timestamp(), item.authoritative_ref),
    )
