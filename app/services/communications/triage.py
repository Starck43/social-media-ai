"""Inbox triage, thread classification, and agent-suggested reply/action proposals (P20-T09).

Invariants (`docs/tasks/p20-t09.md`, `docs/WORKSPACE_UX.md#correspondence`, `D3`, `D11`, `D14`, `D15`):
- Classify threads into four canonical buckets with a human-readable Russian reason:
  - `needs_reply` («Нужен ответ»)
  - `fyi` («К сведению»)
  - `can_wait` («Подождёт»)
  - `uncertain` («Не уверен»)
- Uses metadata and only transient snippet/content the actor is authorized to read (`D3`/`D11`);
  never creates a message body cache or search index (`persisted_body_cache=False`, `indexed_body=False`).
- Propose, never perform:
  - `reply_draft`: editable reply proposal that only enters the P15-T05 outbox as an ordinary draft
    via `app.services.communications.reply.create_reply_draft` when explicitly requested by the user;
    never schedules or sends without outbox approval.
  - `planner_task`: editable human to-do proposal.
  - `followup`: editable follow-up reminder proposal via `app.services.communications.followups`.
  - `document_draft`: editable document draft proposal.
- User corrections (`record_user_triage_override`) are stored as an explicit user decision
  (`basis="user_override"`, `is_model_fact=False`), never misrepresented as a model fact.
- Team mailboxes: `assignee` is exposed only when present on the underlying thread record;
  shared-inbox assignment is not invented (`shared_inbox_supported=False`).
- Prompt-injection patterns in subjects, senders, or transient previews are detected and neutralized;
  they never trigger auto-send, approval bypass, or category escalation tricks.
- Deterministic baseline runs first; optional model refinement requires explicit paid-model approval
  and available budget, surfacing `budget_denied` or `model_failure` transparently while falling back
  to the deterministic baseline.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import select

from app.core.tenant_context import current_tenant_id, current_user_id
from app.models.communications.message import MessageThread, UnifiedMessage
from app.models.workspace_connection import WorkspaceConnection
from app.services.communications.followups import create_followup_item
from app.services.communications.inbox import (
    CHANNEL_BADGES,
    InboxItemUnavailable,
    require_message_permission,
)
from app.services.permissions import has_permission
from app.types import ActionType

TRIAGE_NEEDS_REPLY = "needs_reply"
TRIAGE_FYI = "fyi"
TRIAGE_CAN_WAIT = "can_wait"
TRIAGE_UNCERTAIN = "uncertain"

TRIAGE_CATEGORIES: tuple[str, ...] = (
    TRIAGE_NEEDS_REPLY,
    TRIAGE_FYI,
    TRIAGE_CAN_WAIT,
    TRIAGE_UNCERTAIN,
)

TRIAGE_LABELS: dict[str, str] = {
    TRIAGE_NEEDS_REPLY: "Нужен ответ",
    TRIAGE_FYI: "К сведению",
    TRIAGE_CAN_WAIT: "Подождёт",
    TRIAGE_UNCERTAIN: "Не уверен",
}

TRIAGE_PRIORITY_MAP: dict[str, str] = {
    TRIAGE_NEEDS_REPLY: "high",
    TRIAGE_UNCERTAIN: "medium",
    TRIAGE_CAN_WAIT: "low",
    TRIAGE_FYI: "low",
}

PROPOSAL_REPLY_DRAFT = "reply_draft"
PROPOSAL_PLANNER_TASK = "planner_task"
PROPOSAL_FOLLOWUP = "followup"
PROPOSAL_DOCUMENT_DRAFT = "document_draft"

PROPOSAL_KINDS: tuple[str, ...] = (
    PROPOSAL_REPLY_DRAFT,
    PROPOSAL_PLANNER_TASK,
    PROPOSAL_FOLLOWUP,
    PROPOSAL_DOCUMENT_DRAFT,
)

REVOKED_CONNECTION_STATUSES = frozenset(
    {"revoked", "disabled", "disconnected", "error", "deleted", "unauthorized"}
)

# Per-tenant, per-user decision overrides: (tenant_id, user_id, thread_id) -> dict
# Stored as user decisions (never as model facts).
_USER_TRIAGE_DECISIONS: dict[tuple[int, int, int], dict[str, Any]] = {}

PROMPT_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(?:all\s+|previous\s+|prior\s+|above\s+)?instructions", re.IGNORECASE),
    re.compile(r"disregard\s+(?:all\s+|previous\s+|prior\s+)?instructions", re.IGNORECASE),
    re.compile(r"system\s*prompt", re.IGNORECASE),
    re.compile(r"you\s+are\s+now\s+(?:an?\s+)?(?:unrestricted|admin|system)", re.IGNORECASE),
    re.compile(r"(?:auto[\s_-]*send|send\s+immediately\s+without\s+approval|bypass\s+approval)", re.IGNORECASE),
    re.compile(r"(?:approve\s+all|always\s+allow|execute\s+command)", re.IGNORECASE),
    re.compile(r"забудь\s+(?:все\s+|предыдущие\s+)?инструкции", re.IGNORECASE),
    re.compile(r"игнорируй\s+(?:все\s+|предыдущие\s+)?(?:инструкции|правила)", re.IGNORECASE),
    re.compile(r"отправь\s+(?:ответ\s+)?без\s+(?:подтверждения|согласования|одобрения)", re.IGNORECASE),
    re.compile(r"системный\s+промпт", re.IGNORECASE),
)

URGENT_REPLY_KEYWORDS: tuple[str, ...] = (
    "?",
    "срочно",
    "жду ответа",
    "прошу ответить",
    "подскажите",
    "согласуйте",
    "подтвердите",
    "вопрос",
    "когда сможете",
    "пришлите",
    "счет на оплату",
    "договор",
    "коммерческое предложение",
    "запрос",
    "urgent",
    "please reply",
    "action required",
    "asap",
    "invoice",
)

FYI_KEYWORDS: tuple[str, ...] = (
    "no-reply",
    "noreply",
    "mailer-daemon",
    "donotreply",
    "уведомление",
    "квитанция",
    "чек",
    "автоматическое сообщение",
    "отчет о доставке",
    "принято к сведению",
    "спасибо за ответ",
    "fyi",
    "к сведению",
    "receipt",
    "notification",
    "digest",
    "статус заказа",
)

CAN_WAIT_KEYWORDS: tuple[str, ...] = (
    "рассылка",
    "дайджест",
    "новости",
    "вебинар",
    "приглашение",
    "акция",
    "newsletter",
    "unsubscribe",
    "отписаться",
    "не срочно",
    "на следующей неделе",
    "после праздников",
    "как будет время",
    "обновление блога",
)


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _positive_int(value: Any) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and value > 0


def detect_prompt_injection(text: str | None) -> tuple[bool, list[str]]:
    """Detect prompt-injection phrases in untrusted subject/sender/preview strings."""
    if not text or not isinstance(text, str):
        return False, []
    matched: list[str] = []
    for pattern in PROMPT_INJECTION_PATTERNS:
        m = pattern.search(text)
        if m is not None:
            matched.append(m.group(0)[:64])
    return bool(matched), matched


def sanitize_untrusted_text(text: str | None, *, max_length: int = 240) -> str:
    """Strip control characters and neutralize embedded prompt-injection directives."""
    if not text or not isinstance(text, str):
        return ""
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", " ", text).strip()
    for pattern in PROMPT_INJECTION_PATTERNS:
        cleaned = pattern.sub("[удалена подозрительная инструкция]", cleaned)
    return cleaned[:max_length]


@dataclass(frozen=True)
class TriageProposal:
    """Editable, non-executing proposal attached to a triaged thread."""

    proposal_id: str
    kind: str
    label: str
    title: str
    summary: str
    editable: bool = True
    explicit_confirmation_required: bool = True
    auto_executed: bool = False
    sent_without_approval: bool = False
    fields: dict[str, Any] = field(default_factory=dict)
    target_service: str = ""
    target_route: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "proposal_id": self.proposal_id,
            "kind": self.kind,
            "label": self.label,
            "title": self.title,
            "summary": self.summary,
            "editable": self.editable,
            "explicit_confirmation_required": self.explicit_confirmation_required,
            "auto_executed": False,
            "sent_without_approval": False,
            "fields": dict(self.fields),
            "target_service": self.target_service,
            "target_route": self.target_route,
        }


@dataclass(frozen=True)
class ThreadTriageResult:
    """Permission-filtered read-time triage projection for a single thread."""

    thread_id: int
    tenant_id: int
    channel: str
    channel_label: str
    contact_id: int | None
    contact_display_name: str | None
    subject: str
    category: str
    category_label: str
    reason: str
    confidence: float
    basis: str
    is_model_fact: bool
    baseline_category: str
    baseline_reason: str
    recommended_priority: str
    priority_basis: str
    unread_count: int
    message_count: int
    last_message_at: str | None
    last_direction: str | None
    inbound_message_id: int | None
    assignee: dict[str, Any] | None
    shared_inbox_supported: bool
    data_boundary: str
    persisted_body_cache: bool
    indexed_body: bool
    prompt_injection_detected: bool
    prompt_injection_matches: tuple[str, ...]
    model_status: str
    model_failure: bool
    budget_denied: bool
    model_notice: str | None
    connection_status: str
    stale_or_unavailable: bool
    proposals: tuple[TriageProposal, ...]
    authoritative_ref: str
    authoritative_url: str
    chat_url: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "thread_id": self.thread_id,
            "tenant_id": self.tenant_id,
            "channel": self.channel,
            "channel_label": self.channel_label,
            "contact_id": self.contact_id,
            "contact_display_name": self.contact_display_name,
            "subject": self.subject,
            "category": self.category,
            "category_label": self.category_label,
            "reason": self.reason,
            "confidence": self.confidence,
            "basis": self.basis,
            "is_model_fact": self.is_model_fact,
            "baseline_category": self.baseline_category,
            "baseline_reason": self.baseline_reason,
            "recommended_priority": self.recommended_priority,
            "priority_basis": self.priority_basis,
            "unread_count": self.unread_count,
            "message_count": self.message_count,
            "last_message_at": self.last_message_at,
            "last_direction": self.last_direction,
            "inbound_message_id": self.inbound_message_id,
            "assignee": dict(self.assignee) if isinstance(self.assignee, dict) else None,
            "shared_inbox_supported": self.shared_inbox_supported,
            "data_boundary": self.data_boundary,
            "persisted_body_cache": False,
            "indexed_body": False,
            "prompt_injection_detected": self.prompt_injection_detected,
            "prompt_injection_matches": list(self.prompt_injection_matches),
            "model_status": self.model_status,
            "model_failure": self.model_failure,
            "budget_denied": self.budget_denied,
            "model_notice": self.model_notice,
            "connection_status": self.connection_status,
            "stale_or_unavailable": self.stale_or_unavailable,
            "proposals": [p.to_dict() for p in self.proposals],
            "authoritative_ref": self.authoritative_ref,
            "authoritative_url": self.authoritative_url,
            "chat_url": self.chat_url,
        }

    def to_attention_item(self) -> dict[str, Any]:
        """Project into the P20-T02 / WORKSPACE_UX attention-item contract for «Сегодня»."""
        return {
            "item_key": f"reply:thread:{self.tenant_id}:{self.thread_id}",
            "provider": "communications.inbox_triage",
            "kind": "thread",
            "id": self.thread_id,
            "channel": self.channel,
            "title": self.subject,
            "summary": f"{self.category_label}: {self.reason} · Канал: {self.channel_label} · Непрочитанных: {self.unread_count}",
            "status_label": self.category_label if self.unread_count <= 0 else f"{self.category_label} ({self.unread_count} непрочит.)",
            "triage_category": self.category,
            "triage_label": self.category_label,
            "triage_reason": self.reason,
            "triage_basis": self.basis,
            "is_model_fact": self.is_model_fact,
            "recommended_priority": self.recommended_priority,
            "priority_basis": self.priority_basis,
            "owner_state": "unread" if self.unread_count > 0 else "read",
            "data_boundary": self.data_boundary,
            "freshness": self.last_message_at or "",
            "authoritative_ref": self.authoritative_ref,
            "href": self.authoritative_url,
            "chat_url": self.chat_url,
            "created_at": self.last_message_at or "",
            "stale_or_unavailable": self.stale_or_unavailable,
            "marker_label": (
                "Источник недоступен (только метаданные)"
                if self.stale_or_unavailable
                else (self.model_notice or "")
            ),
            "allowed_actions": [
                {"kind": "open_thread", "label": "Открыть диалог", "href": self.authoritative_url},
                {"kind": "ask_agent", "label": "Спросить агента", "href": self.chat_url},
            ],
            "proposals": [p.to_dict() for p in self.proposals],
        }


def _extract_assignee(thread: Any) -> tuple[dict[str, Any] | None, bool]:
    """Expose assignee only when the inbox thread record actually carries one."""
    assignee_id = (
        thread.get("assignee_id")
        if isinstance(thread, dict)
        else getattr(thread, "assignee_id", None)
    )
    assignee_name = (
        thread.get("assignee_name")
        if isinstance(thread, dict)
        else getattr(thread, "assignee_name", None)
    )
    if _positive_int(assignee_id) or (isinstance(assignee_name, str) and assignee_name.strip()):
        return (
            {
                "id": assignee_id if _positive_int(assignee_id) else None,
                "name": str(assignee_name or f"Участник #{assignee_id}").strip(),
            },
            True,
        )
    return None, False


def classify_thread_baseline(
    *,
    subject: str | None,
    sender: str | None = None,
    unread_count: int = 0,
    message_count: int = 0,
    last_direction: str | None = None,
    is_unavailable: bool = False,
    transient_preview: str | None = None,
    allow_transient_preview: bool = False,
) -> dict[str, Any]:
    """Deterministic metadata-first baseline classifier (`D3`/`D11`).

    Uses only metadata (`subject`, `sender`, `unread_count`, `last_direction`) plus an
    optional in-memory `transient_preview` only when `allow_transient_preview=True`.
    Never caches or indexes message bodies.
    """
    raw_subject = str(subject or "").strip()
    raw_sender = str(sender or "").strip()
    raw_preview = str(transient_preview or "").strip() if allow_transient_preview else ""

    inj_sub, matches_sub = detect_prompt_injection(raw_subject)
    inj_snd, matches_snd = detect_prompt_injection(raw_sender)
    inj_prv, matches_prv = detect_prompt_injection(raw_preview)
    injection_detected = inj_sub or inj_snd or inj_prv
    injection_matches = tuple(dict.fromkeys([*matches_sub, *matches_snd, *matches_prv]))

    safe_subject = sanitize_untrusted_text(raw_subject, max_length=200)
    safe_sender = sanitize_untrusted_text(raw_sender, max_length=120)
    safe_preview = sanitize_untrusted_text(raw_preview, max_length=240) if allow_transient_preview else ""

    if injection_detected:
        return {
            "category": TRIAGE_UNCERTAIN,
            "reason": "Обнаружена попытка внедрения инструкций (prompt injection) в метаданных или тексте; требуется ручная проверка.",
            "confidence": 0.95,
            "prompt_injection_detected": True,
            "prompt_injection_matches": injection_matches,
        }

    if is_unavailable:
        return {
            "category": TRIAGE_UNCERTAIN,
            "reason": "Оригинал сообщения недоступен в источнике; классификация выполнена только по сохранённым метаданным.",
            "confidence": 0.45,
            "prompt_injection_detected": False,
            "prompt_injection_matches": (),
        }

    combined = " ".join(part.lower() for part in (safe_subject, safe_sender, safe_preview) if part)

    # 1. Automated / no-reply / receipt senders and subjects -> FYI («К сведению»)
    for kw in FYI_KEYWORDS:
        if kw in combined:
            return {
                "category": TRIAGE_FYI,
                "reason": f"Автоматическое уведомление или справочное сообщение (признак: «{kw}»).",
                "confidence": 0.88,
                "prompt_injection_detected": False,
                "prompt_injection_matches": (),
            }

    # 2. If last message was outbound and no unread inbound messages remain -> Can wait («Подождёт»)
    if last_direction == "outbound" and unread_count <= 0:
        return {
            "category": TRIAGE_CAN_WAIT,
            "reason": "Последнее сообщение в цепочке отправлено нами; новых входящих нет.",
            "confidence": 0.86,
            "prompt_injection_detected": False,
            "prompt_injection_matches": (),
        }

    # 3. Explicit low-priority / newsletter / deferred phrasing -> Can wait («Подождёт»)
    for kw in CAN_WAIT_KEYWORDS:
        if kw in combined:
            return {
                "category": TRIAGE_CAN_WAIT,
                "reason": f"Несрочная рассылка или отложенное обсуждение (признак: «{kw}»).",
                "confidence": 0.82,
                "prompt_injection_detected": False,
                "prompt_injection_matches": (),
            }

    # 4. Explicit question or urgent request markers on inbound/unread thread -> Needs reply («Нужен ответ»)
    for kw in URGENT_REPLY_KEYWORDS:
        if kw in combined and last_direction != "outbound":
            return {
                "category": TRIAGE_NEEDS_REPLY,
                "reason": (
                    "Входящее сообщение содержит прямой вопрос или запрос действия."
                    if kw == "?"
                    else f"Входящее обращение требует ответа (признак: «{kw}»)."
                ),
                "confidence": 0.9 if unread_count > 0 else 0.78,
                "prompt_injection_detected": False,
                "prompt_injection_matches": (),
            }

    # 5. Unread inbound message from a person with non-empty subject -> Needs reply («Нужен ответ»)
    if unread_count > 0 and last_direction in ("inbound", None) and safe_subject:
        return {
            "category": TRIAGE_NEEDS_REPLY,
            "reason": f"Непрочитанное входящее сообщение ({unread_count} непрочит.) по теме «{safe_subject[:60]}».",
            "confidence": 0.74,
            "prompt_injection_detected": False,
            "prompt_injection_matches": (),
        }

    # 6. Read inbound thread without explicit question/action markers -> Uncertain («Не уверен»)
    if not safe_subject or message_count == 0:
        return {
            "category": TRIAGE_UNCERTAIN,
            "reason": "Недостаточно метаданных (отсутствует тема или сообщения) для уверенной классификации.",
            "confidence": 0.4,
            "prompt_injection_detected": False,
            "prompt_injection_matches": (),
        }

    return {
        "category": TRIAGE_UNCERTAIN,
        "reason": "По метаданным темы не удалось однозначно определить необходимость срочного ответа.",
        "confidence": 0.55,
        "prompt_injection_detected": False,
        "prompt_injection_matches": (),
    }


def build_thread_proposals(
    *,
    thread_id: int,
    channel: str,
    subject: str,
    contact_id: int | None,
    contact_display_name: str | None,
    category: str,
    inbound_message_id: int | None,
    last_message_at: str | None,
    prompt_injection_detected: bool = False,
) -> tuple[TriageProposal, ...]:
    """Generate editable, non-executing proposals for the triaged thread."""
    safe_subject = sanitize_untrusted_text(subject, max_length=140) or f"Диалог #{thread_id}"
    reply_subject = safe_subject if safe_subject.lower().startswith("re:") else f"Re: {safe_subject}"
    counterparty = sanitize_untrusted_text(contact_display_name, max_length=80) or f"Контакт #{contact_id or thread_id}"

    if prompt_injection_detected:
        # Never propose an automated reply text when prompt injection was detected; offer only a safe review task.
        return (
            TriageProposal(
                proposal_id=f"proposal:planner_task:{thread_id}",
                kind=PROPOSAL_PLANNER_TASK,
                label="Задача в план",
                title=f"Проверить подозрительное сообщение: {safe_subject[:80]}",
                summary="В метаданных обнаружена подозрительная инструкция; проверьте оригинал перед ответом.",
                fields={
                    "title": f"Проверить диалог #{thread_id}: {safe_subject[:80]}",
                    "priority": "high",
                    "thread_id": thread_id,
                    "contact_id": contact_id,
                },
                target_service="workspace.planner",
                target_route="/app/tasks",
            ),
        )

    proposals: list[TriageProposal] = []

    if category in (TRIAGE_NEEDS_REPLY, TRIAGE_UNCERTAIN):
        suggested_body = (
            f"Здравствуйте{', ' + counterparty if contact_display_name else ''}!\n\n"
            f"Спасибо за сообщение по теме «{safe_subject}». "
            "Мы изучили ваш запрос и подготовили ответ. Пожалуйста, уточните, если потребуются дополнительные детали."
        )
        proposals.append(
            TriageProposal(
                proposal_id=f"proposal:reply_draft:{thread_id}",
                kind=PROPOSAL_REPLY_DRAFT,
                label="Черновик ответа",
                title=f"Черновик ответа: {reply_subject[:100]}",
                summary="Попадает в Outbox (P15-T05) как обычный черновик; отправка только после отдельного одобрения.",
                fields={
                    "thread_id": thread_id,
                    "inbound_message_id": inbound_message_id,
                    "contact_id": contact_id,
                    "channel": channel,
                    "subject": reply_subject,
                    "body": suggested_body,
                    "purpose": "transactional",
                },
                target_service="communications.reply.create_reply_draft",
                target_route="/app/communications/reply",
            )
        )

    due_default = (_utc_now() + timedelta(days=1)).date().isoformat()
    proposals.append(
        TriageProposal(
            proposal_id=f"proposal:planner_task:{thread_id}",
            kind=PROPOSAL_PLANNER_TASK,
            label="Задача в план",
            title=f"Обработать обращение: {safe_subject[:90]}",
            summary="Создать редактируемую задачу в плане со ссылкой на диалог.",
            fields={
                "title": f"Ответить по диалогу: {safe_subject[:90]}",
                "due_date": due_default,
                "priority": "high" if category == TRIAGE_NEEDS_REPLY else "medium",
                "thread_id": thread_id,
                "contact_id": contact_id,
            },
            target_service="workspace.planner",
            target_route="/app/tasks",
        )
    )

    followup_preview = create_followup_item(
        item_id=f"fup-thread-{thread_id}",
        counterparty=counterparty,
        commitment_text=f"Проконтролировать ответ по теме «{safe_subject[:80]}»",
        source_citation=f"communication_thread:{thread_id}",
        due_at=due_default,
    )
    proposals.append(
        TriageProposal(
            proposal_id=f"proposal:followup:{thread_id}",
            kind=PROPOSAL_FOLLOWUP,
            label="Напоминание (follow-up)",
            title=f"Напоминание по диалогу с {counterparty}",
            summary="Поставить напоминание на контроль с учётом тихих часов (21:00–08:00).",
            fields=followup_preview,
            target_service="communications.followups.create_followup_item",
            target_route="/app/followups",
        )
    )

    proposals.append(
        TriageProposal(
            proposal_id=f"proposal:document_draft:{thread_id}",
            kind=PROPOSAL_DOCUMENT_DRAFT,
            label="Черновик документа",
            title=f"Черновик документа по теме «{safe_subject[:80]}»",
            summary="Подготовить черновик документа/КП в редакторе документов (требует согласования P19-T04).",
            fields={
                "title": f"Документ: {safe_subject[:80]}",
                "doc_type": "commercial_offer",
                "thread_id": thread_id,
                "contact_id": contact_id,
            },
            target_service="documents.draft",
            target_route="/app/documents",
        )
    )

    return tuple(proposals)


def record_user_triage_override(
    *,
    tenant_id: int,
    user_id: int,
    thread_id: int,
    category: str,
    reason: str | None = None,
) -> dict[str, Any]:
    """Record a user correction as an explicit user decision, never as a model fact."""
    if not all(_positive_int(v) for v in (tenant_id, user_id, thread_id)):
        raise ValueError("invalid_override_scope")
    norm_cat = str(category or "").strip().lower()
    if norm_cat not in TRIAGE_CATEGORIES:
        raise ValueError("invalid_triage_category")
    clean_reason = sanitize_untrusted_text(
        reason or f"Категория изменена пользователем на «{TRIAGE_LABELS[norm_cat]}».",
        max_length=240,
    )
    decision = {
        "tenant_id": tenant_id,
        "user_id": user_id,
        "thread_id": thread_id,
        "category": norm_cat,
        "category_label": TRIAGE_LABELS[norm_cat],
        "reason": clean_reason,
        "basis": "user_override",
        "source": "user_decision",
        "is_model_fact": False,
        "recorded_by_user_id": user_id,
        "updated_at": _utc_now().isoformat(),
    }
    _USER_TRIAGE_DECISIONS[(tenant_id, user_id, thread_id)] = decision
    return dict(decision)


def get_user_triage_override(tenant_id: int, user_id: int | None, thread_id: int) -> dict[str, Any] | None:
    """Return a recorded user triage correction for `(tenant_id, user_id, thread_id)` if present."""
    if not _positive_int(tenant_id) or not _positive_int(user_id) or not _positive_int(thread_id):
        return None
    found = _USER_TRIAGE_DECISIONS.get((tenant_id, int(user_id), thread_id))
    return dict(found) if isinstance(found, dict) else None


def clear_user_triage_overrides() -> None:
    """Reset in-memory overrides (for isolated unit testing)."""
    _USER_TRIAGE_DECISIONS.clear()
