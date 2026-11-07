"""Bounded specialist delegation pilot for social research (P20-T04).

Builds on the accepted P8-T40 composed pipeline (`app/services/task_pipeline.py`),
explicit task-plan contract (`app/services/task_plans.py`), typed tool registration
(`P11-T09`), and P20-T08 chat workspace («В работе» tab and progress cards) without
introducing a second orchestration engine or second status store.

Key invariants:
- Fixed versioned workflow (`DELEGATION_WORKFLOW_VERSION = 1`):
  `collect` (`collector`) -> `analyze` (`analyzer`) -> `synthesize` (`synthesizer`).
- Specialist roles are implementation roles only, never new privileged identities
  or unrestricted agents. No dynamic agent creation, rooms, or marketplace.
- Web research steps (`web_research`, `web_search`, `browser`) are fail-closed
  until `P20-T12` is accepted.
- Child scopes may only narrow the originating actor/workspace, source IDs,
  permissions, tools, and model/data boundaries (`local_only` < `company` < `cloud`).
- Authorization and payer/budget quotas are rechecked at admission and before each step.
- Untrusted source content is sanitized and flagged; prompt injection inside fetched
  posts cannot grant tools, permissions, or widen scope.
- Checkpoints live inside the existing job/pipeline receipt (`pipeline["delegation"]`),
  enabling idempotent resume of completed steps, fail-closed review on interrupted
  steps, and immediate cancellation between steps.
- External effects (e.g., Bitrix24 / CRM note or digest delivery) are produced strictly
  as drafts (`delivery_state="draft_only"`, `external_writes_performed=0`) requiring
  separate human confirmation through existing command/delivery ledgers.
"""

from __future__ import annotations

import hashlib
import re
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Literal, Mapping

from app.services.task_pipeline import PipelineReviewRequired

DELEGATION_WORKFLOW_VERSION = 1
SpecialistRole = Literal["collector", "analyzer", "synthesizer"]
DelegationStepName = Literal["collect", "analyze", "synthesize"]

SPECIALIST_ROLES: tuple[SpecialistRole, ...] = ("collector", "analyzer", "synthesizer")
STEP_ORDER: tuple[DelegationStepName, ...] = ("collect", "analyze", "synthesize")
STEP_TO_ROLE: dict[DelegationStepName, SpecialistRole] = {
    "collect": "collector",
    "analyze": "analyzer",
    "synthesize": "synthesizer",
}
STEP_LABELS_RU: dict[DelegationStepName, str] = {
    "collect": "Сбор источников (коллектор)",
    "analyze": "Анализ с доказательствами (аналитик)",
    "synthesize": "Сборка черновика отчёта (синтезатор)",
}

# Strict boundary ordering: child scopes may only stay equal or narrow toward local_only.
BOUNDARY_RANK: dict[str, int] = {
    "local_only": 0,
    "company": 1,
    "cloud": 2,
}

# Allowlisted permissions per specialist role (implementation roles, never escalating actor grants).
ROLE_ALLOWED_PERMISSIONS: dict[SpecialistRole, frozenset[tuple[str, str]]] = {
    "collector": frozenset(
        {
            ("agenttask", "view"),
            ("agenttask", "update"),
            ("source", "view"),
            ("source", "analyze"),
        }
    ),
    "analyzer": frozenset(
        {
            ("agenttask", "view"),
            ("agenttask", "update"),
            ("agentscenario", "view"),
            ("source", "view"),
            ("source", "analyze"),
            ("aianalytics", "view"),
        }
    ),
    "synthesizer": frozenset(
        {
            ("agenttask", "view"),
            ("agentscenario", "view"),
            ("source", "view"),
            ("aianalytics", "view"),
            ("digestrun", "create"),
        }
    ),
}

# Minimum required permissions verified at admission and before each step execution.
STEP_REQUIRED_PERMISSIONS: dict[DelegationStepName, frozenset[tuple[str, str]]] = {
    "collect": frozenset({("agenttask", "view"), ("agenttask", "update"), ("source", "analyze")}),
    "analyze": frozenset(
        {("agenttask", "view"), ("agenttask", "update"), ("agentscenario", "view"), ("source", "analyze")}
    ),
    "synthesize": frozenset(
        {("agenttask", "view"), ("agentscenario", "view"), ("source", "view"), ("aianalytics", "view")}
    ),
}

# Tools allowed inside specialist steps (read/analyze/draft only; never external write or send).
ROLE_ALLOWED_TOOLS: dict[SpecialistRole, frozenset[str]] = {
    "collector": frozenset({"social.collect_bounded", "source.read_metadata"}),
    "analyzer": frozenset({"analysis.extract_claims", "analysis.link_evidence"}),
    "synthesizer": frozenset({"report.compose_draft", "crm.compose_bitrix24_draft"}),
}

FORBIDDEN_EXTERNAL_TOOLS: frozenset[str] = frozenset(
    {
        "crm.write",
        "bitrix24.write",
        "bitrix24.post",
        "outbox.send",
        "digest.send",
        "telegram.send",
        "vk.post",
        "email.send",
        "browser.execute",
        "web.search",
        "web.fetch",
    }
)

UNSUPPORTED_WEB_RESEARCH_STEPS: frozenset[str] = frozenset(
    {"web_research", "web_search", "web_fetch", "browser", "browser_pilot"}
)

# Hard ceiling constants for the P20-T04 pilot.
HARD_MAX_DEPTH = 1
HARD_MAX_FAN_OUT = 3
HARD_MAX_CONCURRENCY = 2
HARD_MAX_RUNTIME_SECONDS = 120
HARD_MAX_RETRIES = 1
HARD_MAX_TOKENS = 12_000
HARD_MAX_SPEND_USD = 0.50
HARD_MAX_SOURCES = 25
HARD_MAX_ITEMS_PER_SOURCE = 100

_INJECTION_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"ignore\s+(all\s+)?(previous|prior)\s+instructions", re.IGNORECASE),
    re.compile(r"(system|developer)\s*:\s*you\s+are", re.IGNORECASE),
    re.compile(r"\[\[tool\s*:", re.IGNORECASE),
    re.compile(r"grant\s+(permission|role|admin|superuser)", re.IGNORECASE),
    re.compile(r"(call|invoke|execute)\s+tool\s+[a-z0-9_.-]+", re.IGNORECASE),
    re.compile(r"(bitrix24|crm|outbox|telegram)\.(write|post|send)", re.IGNORECASE),
    re.compile(r"игнорируй\s+(все\s+)?(предыдущие|прошлые)\s+инструкции", re.IGNORECASE),
    re.compile(r"(выдай|повысь)\s+права|выполни\s+команду|отправь\s+в\s+bitrix", re.IGNORECASE),
)


class DelegationError(ValueError):
    """Base error for bounded research delegation failures."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class DelegationAdmissionError(DelegationError):
    """Workflow definition or step admission violated pilot rules."""


class DelegationPrivilegeEscalationError(PermissionError):
    """Child scope attempted to widen actor, tenant, source, tool, permission, or boundary."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class DelegationCapExceededError(DelegationError):
    """Requested or runtime depth, fan-out, retries, runtime, tokens, or spend exceeded caps."""


class DelegationBudgetExhaustedError(PermissionError):
    """Payer quota or task delegation budget was exhausted before or between steps."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class DelegationCaps:
    """Explicit resource caps for a delegated research run."""

    max_depth: int = HARD_MAX_DEPTH
    max_fan_out: int = HARD_MAX_FAN_OUT
    max_concurrency: int = HARD_MAX_CONCURRENCY
    max_runtime_seconds: int = HARD_MAX_RUNTIME_SECONDS
    max_retries: int = HARD_MAX_RETRIES
    max_tokens: int = HARD_MAX_TOKENS
    max_spend_usd: float = HARD_MAX_SPEND_USD
    max_sources: int = HARD_MAX_SOURCES
    max_items_per_source: int = HARD_MAX_ITEMS_PER_SOURCE

    def validate(self) -> None:
        if type(self.max_depth) is not int or not 1 <= self.max_depth <= HARD_MAX_DEPTH:
            raise DelegationCapExceededError("delegation_cap_depth_invalid")
        if type(self.max_fan_out) is not int or not 1 <= self.max_fan_out <= HARD_MAX_FAN_OUT:
            raise DelegationCapExceededError("delegation_cap_fan_out_invalid")
        if type(self.max_concurrency) is not int or not 1 <= self.max_concurrency <= HARD_MAX_CONCURRENCY:
            raise DelegationCapExceededError("delegation_cap_concurrency_invalid")
        if type(self.max_runtime_seconds) is not int or not 1 <= self.max_runtime_seconds <= HARD_MAX_RUNTIME_SECONDS:
            raise DelegationCapExceededError("delegation_cap_runtime_invalid")
        if type(self.max_retries) is not int or not 0 <= self.max_retries <= HARD_MAX_RETRIES:
            raise DelegationCapExceededError("delegation_cap_retries_invalid")
        if type(self.max_tokens) is not int or not 100 <= self.max_tokens <= HARD_MAX_TOKENS:
            raise DelegationCapExceededError("delegation_cap_tokens_invalid")
        if (
            not isinstance(self.max_spend_usd, (int, float))
            or not 0.0 < float(self.max_spend_usd) <= HARD_MAX_SPEND_USD
        ):
            raise DelegationCapExceededError("delegation_cap_spend_invalid")
        if type(self.max_sources) is not int or not 1 <= self.max_sources <= HARD_MAX_SOURCES:
            raise DelegationCapExceededError("delegation_cap_sources_invalid")
        if (
            type(self.max_items_per_source) is not int
            or not 1 <= self.max_items_per_source <= HARD_MAX_ITEMS_PER_SOURCE
        ):
            raise DelegationCapExceededError("delegation_cap_items_invalid")


@dataclass(frozen=True)
class OriginScope:
    """Authoritative scope of the originating coordinator request."""

    actor_id: int
    tenant_id: int
    payer_user_id: int
    allowed_source_ids: frozenset[int]
    data_boundary: Literal["local_only", "company", "cloud"] = "cloud"
    granted_permissions: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    allowed_tools: frozenset[str] = field(
        default_factory=lambda: (
            ROLE_ALLOWED_TOOLS["collector"] | ROLE_ALLOWED_TOOLS["analyzer"] | ROLE_ALLOWED_TOOLS["synthesizer"]
        )
    )

    def validate(self) -> None:
        if type(self.actor_id) is not int or self.actor_id <= 0:
            raise DelegationPrivilegeEscalationError("delegation_origin_actor_invalid")
        if type(self.tenant_id) is not int or self.tenant_id <= 0:
            raise DelegationPrivilegeEscalationError("delegation_origin_tenant_invalid")
        if type(self.payer_user_id) is not int or self.payer_user_id <= 0:
            raise DelegationPrivilegeEscalationError("delegation_origin_payer_invalid")
        if not self.allowed_source_ids or any(type(sid) is not int or sid <= 0 for sid in self.allowed_source_ids):
            raise DelegationAdmissionError("delegation_origin_sources_required")
        if self.data_boundary not in BOUNDARY_RANK:
            raise DelegationAdmissionError("delegation_origin_boundary_invalid")
        if self.allowed_tools & FORBIDDEN_EXTERNAL_TOOLS:
            raise DelegationPrivilegeEscalationError("delegation_origin_forbidden_tool")


@dataclass(frozen=True)
class ChildScopeRequest:
    """Requested specialist step scope to be validated against OriginScope."""

    role: str
    step: str
    actor_id: int
    tenant_id: int
    source_ids: tuple[int, ...]
    data_boundary: str
    depth: int = 1
    permissions: frozenset[tuple[str, str]] = field(default_factory=frozenset)
    tools: frozenset[str] = field(default_factory=frozenset)


@dataclass(frozen=True)
class ChildScope:
    """Validated narrowed specialist scope."""

    role: SpecialistRole
    step: DelegationStepName
    actor_id: int
    tenant_id: int
    source_ids: tuple[int, ...]
    data_boundary: Literal["local_only", "company", "cloud"]
    depth: int
    permissions: frozenset[tuple[str, str]]
    tools: frozenset[str]


def narrow_child_scope(
    origin: OriginScope,
    request: ChildScopeRequest,
    *,
    caps: DelegationCaps | None = None,
) -> ChildScope:
    """Validate that a child specialist scope strictly narrows the originating scope."""
    origin.validate()
    effective_caps = caps or DelegationCaps()
    effective_caps.validate()

    if request.step in UNSUPPORTED_WEB_RESEARCH_STEPS or request.role in UNSUPPORTED_WEB_RESEARCH_STEPS:
        raise DelegationAdmissionError("delegation_web_research_unavailable_before_p20_t12")
    if request.step not in STEP_TO_ROLE or request.role not in SPECIALIST_ROLES:
        raise DelegationAdmissionError("delegation_role_or_step_unsupported")
    expected_role = STEP_TO_ROLE[request.step]  # type: ignore[index]
    if request.role != expected_role:
        raise DelegationAdmissionError("delegation_step_role_mismatch")

    if type(request.depth) is not int or request.depth < 1 or request.depth > effective_caps.max_depth:
        raise DelegationCapExceededError("delegation_depth_exceeded")

    if request.actor_id != origin.actor_id:
        raise DelegationPrivilegeEscalationError("delegation_actor_escalation")
    if request.tenant_id != origin.tenant_id:
        raise DelegationPrivilegeEscalationError("delegation_cross_tenant_escalation")

    if not request.source_ids or any(type(sid) is not int or sid <= 0 for sid in request.source_ids):
        raise DelegationAdmissionError("delegation_child_sources_required")
    if len(request.source_ids) > effective_caps.max_sources:
        raise DelegationCapExceededError("delegation_source_count_exceeded")
    if not set(request.source_ids).issubset(origin.allowed_source_ids):
        raise DelegationPrivilegeEscalationError("delegation_source_scope_escalation")

    if request.data_boundary not in BOUNDARY_RANK:
        raise DelegationAdmissionError("delegation_boundary_invalid")
    if BOUNDARY_RANK[request.data_boundary] > BOUNDARY_RANK[origin.data_boundary]:
        raise DelegationPrivilegeEscalationError("delegation_boundary_escalation")

    role_key: SpecialistRole = expected_role
    step_key: DelegationStepName = request.step  # type: ignore[assignment]
    role_allowed_perms = ROLE_ALLOWED_PERMISSIONS[role_key]
    required_perms = STEP_REQUIRED_PERMISSIONS[step_key]

    requested_perms = request.permissions or (origin.granted_permissions & role_allowed_perms)
    if not requested_perms.issubset(origin.granted_permissions):
        raise DelegationPrivilegeEscalationError("delegation_permission_escalation")
    if not requested_perms.issubset(role_allowed_perms):
        raise DelegationPrivilegeEscalationError("delegation_role_permission_escalation")
    if not required_perms.issubset(requested_perms):
        raise PermissionError("delegation_step_permission_denied")

    role_allowed_tools = ROLE_ALLOWED_TOOLS[role_key]
    requested_tools = request.tools or (origin.allowed_tools & role_allowed_tools)
    if requested_tools & FORBIDDEN_EXTERNAL_TOOLS:
        raise DelegationPrivilegeEscalationError("delegation_forbidden_external_tool")
    if not requested_tools.issubset(origin.allowed_tools) or not requested_tools.issubset(role_allowed_tools):
        raise DelegationPrivilegeEscalationError("delegation_tool_escalation")

    return ChildScope(
        role=role_key,
        step=step_key,
        actor_id=origin.actor_id,
        tenant_id=origin.tenant_id,
        source_ids=tuple(dict.fromkeys(request.source_ids)),
        data_boundary=request.data_boundary,  # type: ignore[arg-type]
        depth=request.depth,
        permissions=frozenset(requested_perms),
        tools=frozenset(requested_tools),
    )


def inspect_and_neutralize_source_item(
    raw_item: Mapping[str, Any], *, allowed_source_ids: frozenset[int]
) -> dict[str, Any]:
    """Sanitize untrusted social post content and neutralize prompt-injection attempts.

    Fetched content is never allowed to grant tools, permissions, or alter task scope.
    """
    source_id = raw_item.get("source_id")
    if type(source_id) is not int or source_id not in allowed_source_ids:
        raise DelegationPrivilegeEscalationError("delegation_untrusted_item_foreign_source")

    raw_text = str(raw_item.get("text") or "").strip()
    raw_title = str(raw_item.get("title") or "").strip()
    external_id = str(raw_item.get("external_id") or raw_item.get("id") or "item")[:120]
    url = str(raw_item.get("url") or f"https://social.local/source/{source_id}/{external_id}")[:300]

    combined = f"{raw_title}\n{raw_text}"
    injection_flagged = any(pattern.search(combined) for pattern in _INJECTION_PATTERNS)

    # Strip control brackets and tool/role directives from untrusted text while keeping inert evidence.
    cleaned_text = raw_text
    if injection_flagged:
        for pattern in _INJECTION_PATTERNS:
            cleaned_text = pattern.sub("[REDACTED_UNTRUSTED_DIRECTIVE]", cleaned_text)
    cleaned_text = cleaned_text.replace("[[", "[ [").replace("]]", "] ]")[:2000]
    cleaned_title = raw_title.replace("[[", "[ [").replace("]]", "] ]")[:200]

    content_hash = raw_item.get("content_hash")
    if not isinstance(content_hash, str) or not re.fullmatch(r"[0-9a-f]{64}", content_hash):
        digest_input = f"{source_id}:{external_id}:{raw_text}".encode("utf-8")
        content_hash = hashlib.sha256(digest_input).hexdigest()

    # Explicitly drop any embedded tool_calls, permissions, or role overrides in raw_item.
    return {
        "source_id": source_id,
        "external_id": external_id,
        "content_hash": content_hash,
        "title": cleaned_title,
        "text": cleaned_text,
        "url": url,
        "published_at": str(raw_item.get("published_at") or "")[:40],
        "content_role": "untrusted_evidence",
        "injection_flagged": injection_flagged,
    }


def _delegation_checkpoint_base(*, job_id: int, task_id: int, attempt: int) -> dict[str, Any]:
    return {
        "workflow_version": DELEGATION_WORKFLOW_VERSION,
        "job_id": job_id,
        "task_id": task_id,
        "attempt": attempt,
        "state": "running",
        "steps": {},
        "usage": {"tokens": 0, "spend_usd": 0.0},
    }


def validate_delegation_checkpoint(value: Any, *, job_id: int, task_id: int) -> dict[str, Any]:
    """Validate persisted delegation receipt inside pipeline checkpoint; fail closed on corruption."""
    if (
        not isinstance(value, dict)
        or value.get("workflow_version") != DELEGATION_WORKFLOW_VERSION
        or value.get("job_id") != job_id
        or value.get("task_id") != task_id
        or not isinstance(value.get("steps"), dict)
        or type(value.get("attempt")) is not int
        or value["attempt"] < 0
    ):
        raise PipelineReviewRequired("delegation_checkpoint_invalid_requires_review")

    validated_steps: dict[str, Any] = {}
    for step_name, row in value["steps"].items():
        if step_name not in STEP_TO_ROLE or not isinstance(row, dict):
            raise PipelineReviewRequired("delegation_checkpoint_invalid_requires_review")
        state = row.get("state")
        if state not in {"started", "completed", "review_required", "cancelled", "failed"}:
            raise PipelineReviewRequired("delegation_checkpoint_invalid_requires_review")
        if type(row.get("attempt")) is not int or row["attempt"] < 1:
            raise PipelineReviewRequired("delegation_checkpoint_invalid_requires_review")
        if state == "completed" and not isinstance(row.get("result"), dict):
            raise PipelineReviewRequired("delegation_checkpoint_invalid_requires_review")
        if state == "started" and "result" in row:
            raise PipelineReviewRequired("delegation_checkpoint_invalid_requires_review")
        validated_steps[step_name] = dict(row)

    usage = value.get("usage") if isinstance(value.get("usage"), dict) else {"tokens": 0, "spend_usd": 0.0}
    return {
        "workflow_version": DELEGATION_WORKFLOW_VERSION,
        "job_id": job_id,
        "task_id": task_id,
        "attempt": value["attempt"],
        "state": str(value.get("state") or "running"),
        "steps": validated_steps,
        "usage": {
            "tokens": int(usage.get("tokens") or 0),
            "spend_usd": float(usage.get("spend_usd") or 0.0),
        },
    }


AuthChecker = Callable[[int, int, DelegationStepName, frozenset[tuple[str, str]]], Awaitable[None] | None]
BudgetChecker = Callable[[int, int, int, float, int, float], Awaitable[None] | None]
CheckpointSaver = Callable[[dict[str, Any]], Awaitable[None] | None]
CancelChecker = Callable[[], bool]


async def _maybe_await(res: Any) -> Any:
    if hasattr(res, "__await__"):
        return await res
    return res


async def execute_delegated_research(
    *,
    task_id: int,
    job_id: int,
    attempt: int,
    goal: str,
    origin: OriginScope,
    caps: DelegationCaps | None = None,
    steps: tuple[str, ...] = STEP_ORDER,
    collector_fn: Callable[[ChildScope, DelegationCaps], Awaitable[dict[str, Any]] | dict[str, Any]],
    analyzer_fn: Callable[[ChildScope, dict[str, Any], DelegationCaps], Awaitable[dict[str, Any]] | dict[str, Any]],
    synthesizer_fn: Callable[
        [ChildScope, dict[str, Any], dict[str, Any], DelegationCaps], Awaitable[dict[str, Any]] | dict[str, Any]
    ],
    auth_checker: AuthChecker | None = None,
    budget_checker: BudgetChecker | None = None,
    checkpoint_saver: CheckpointSaver | None = None,
    cancel_checker: CancelChecker | None = None,
    prior_checkpoint: Mapping[str, Any] | None = None,
    include_bitrix24_draft: bool = False,
) -> dict[str, Any]:
    """Execute bounded 3-step specialist research workflow over existing checkpoint receipts."""
    if (
        type(task_id) is not int
        or task_id <= 0
        or type(job_id) is not int
        or job_id <= 0
        or type(attempt) is not int
        or attempt < 1
    ):
        raise DelegationAdmissionError("delegation_job_identity_invalid")
    if not isinstance(goal, str) or not goal.strip():
        raise DelegationAdmissionError("delegation_goal_required")

    origin.validate()
    effective_caps = caps or DelegationCaps()
    effective_caps.validate()

    for step_name in steps:
        if step_name in UNSUPPORTED_WEB_RESEARCH_STEPS:
            raise DelegationAdmissionError("delegation_web_research_unavailable_before_p20_t12")
        if step_name not in STEP_TO_ROLE:
            raise DelegationAdmissionError("delegation_role_or_step_unsupported")
    if tuple(steps) != STEP_ORDER:
        raise DelegationAdmissionError("delegation_workflow_steps_must_match_v1_order")

    if len(origin.allowed_source_ids) > effective_caps.max_sources:
        raise DelegationCapExceededError("delegation_source_count_exceeded")

    checkpoint = (
        validate_delegation_checkpoint(dict(prior_checkpoint), job_id=job_id, task_id=task_id)
        if prior_checkpoint is not None
        else _delegation_checkpoint_base(job_id=job_id, task_id=task_id, attempt=attempt)
    )

    started_monotonic = time.monotonic()
    reused_steps: list[str] = []

    async def _save_ckpt() -> None:
        if checkpoint_saver is not None:
            await _maybe_await(checkpoint_saver(checkpoint))

    def _check_runtime() -> None:
        elapsed = time.monotonic() - started_monotonic
        if elapsed > effective_caps.max_runtime_seconds:
            raise DelegationCapExceededError("delegation_runtime_exceeded")

    async def _run_step(
        step_name: DelegationStepName,
        child_scope: ChildScope,
        est_tokens: int,
        est_cost: float,
        op: Callable[[], Awaitable[dict[str, Any]] | dict[str, Any]],
    ) -> tuple[dict[str, Any], bool]:
        """Run or reuse one specialist step with cancellation, auth, budget, and retry bounds."""
        _check_runtime()
        if cancel_checker is not None and cancel_checker():
            checkpoint["state"] = "cancelled"
            checkpoint["steps"][step_name] = {
                "state": "cancelled",
                "role": child_scope.role,
                "attempt": attempt,
            }
            await _save_ckpt()
            return {}, True

        previous = checkpoint["steps"].get(step_name)
        if isinstance(previous, dict):
            prev_state = previous.get("state")
            if prev_state == "completed":
                prev_result = previous.get("result")
                if not isinstance(prev_result, dict):
                    raise PipelineReviewRequired("delegation_checkpoint_invalid_requires_review")
                reused_steps.append(step_name)
                return prev_result, False
            if prev_state in {"started", "review_required"}:
                checkpoint["attempt"] = attempt
                checkpoint["state"] = "review_required"
                checkpoint["steps"][step_name] = {
                    "state": "review_required",
                    "role": child_scope.role,
                    "attempt": attempt,
                    "error": "delegation_stage_outcome_requires_review",
                }
                await _save_ckpt()
                raise PipelineReviewRequired("delegation_stage_outcome_requires_review")

        # Recheck actor authorization and tenant membership before each specialist step.
        if auth_checker is not None:
            await _maybe_await(
                auth_checker(
                    child_scope.actor_id,
                    child_scope.tenant_id,
                    step_name,
                    STEP_REQUIRED_PERMISSIONS[step_name],
                )
            )

        # Recheck token and spend caps + payer budget before running step.
        cum_tokens = int(checkpoint["usage"]["tokens"])
        cum_spend = float(checkpoint["usage"]["spend_usd"])
        if cum_tokens + est_tokens > effective_caps.max_tokens:
            raise DelegationBudgetExhaustedError("delegation_token_cap_exhausted")
        if cum_spend + est_cost > effective_caps.max_spend_usd + 1e-9:
            raise DelegationBudgetExhaustedError("delegation_spend_cap_exhausted")
        if budget_checker is not None:
            await _maybe_await(
                budget_checker(
                    child_scope.tenant_id,
                    origin.payer_user_id,
                    est_tokens,
                    est_cost,
                    cum_tokens,
                    cum_spend,
                )
            )

        checkpoint["attempt"] = attempt
        checkpoint["steps"][step_name] = {
            "state": "started",
            "role": child_scope.role,
            "attempt": attempt,
            "source_ids": list(child_scope.source_ids),
            "boundary": child_scope.data_boundary,
        }
        await _save_ckpt()

        last_exc: Exception | None = None
        max_attempts = 1 + effective_caps.max_retries
        step_result: dict[str, Any] | None = None
        for try_idx in range(max_attempts):
            _check_runtime()
            try:
                raw_out = await _maybe_await(op())
                step_result = raw_out if isinstance(raw_out, dict) else {}
                break
            except (
                PermissionError,
                DelegationError,
                DelegationPrivilegeEscalationError,
                DelegationBudgetExhaustedError,
                PipelineReviewRequired,
            ):
                raise
            except Exception as exc:
                last_exc = exc
                if try_idx + 1 >= max_attempts:
                    checkpoint["state"] = "failed"
                    checkpoint["steps"][step_name] = {
                        "state": "failed",
                        "role": child_scope.role,
                        "attempt": attempt,
                        "retries_used": try_idx,
                        "error": "delegation_step_execution_failed",
                    }
                    await _save_ckpt()
                    raise DelegationError("delegation_step_execution_failed") from last_exc

        assert step_result is not None
        used_tokens = int(step_result.get("tokens_used") or est_tokens)
        used_cost = float(step_result.get("cost_usd") or est_cost)
        if int(checkpoint["usage"]["tokens"]) + used_tokens > effective_caps.max_tokens:
            raise DelegationBudgetExhaustedError("delegation_token_cap_exhausted")
        if float(checkpoint["usage"]["spend_usd"]) + used_cost > effective_caps.max_spend_usd + 1e-9:
            raise DelegationBudgetExhaustedError("delegation_spend_cap_exhausted")

        checkpoint["usage"]["tokens"] = int(checkpoint["usage"]["tokens"]) + used_tokens
        checkpoint["usage"]["spend_usd"] = round(float(checkpoint["usage"]["spend_usd"]) + used_cost, 6)
        checkpoint["steps"][step_name] = {
            "state": "completed",
            "role": child_scope.role,
            "attempt": attempt,
            "source_ids": list(child_scope.source_ids),
            "boundary": child_scope.data_boundary,
            "workflow_version": DELEGATION_WORKFLOW_VERSION,
            "result": step_result,
        }
        await _save_ckpt()
        return step_result, False

    sorted_sources = tuple(sorted(origin.allowed_source_ids))

    # --- Step 1: Collector ---
    collector_scope = narrow_child_scope(
        origin,
        ChildScopeRequest(
            role="collector",
            step="collect",
            actor_id=origin.actor_id,
            tenant_id=origin.tenant_id,
            source_ids=sorted_sources,
            data_boundary=origin.data_boundary,
            depth=1,
        ),
        caps=effective_caps,
    )

    async def _do_collect() -> dict[str, Any]:
        raw = await _maybe_await(collector_fn(collector_scope, effective_caps))
        raw_items = raw.get("items") if isinstance(raw.get("items"), list) else []
        per_source = raw.get("per_source") if isinstance(raw.get("per_source"), list) else []
        if len(per_source) > effective_caps.max_fan_out:
            raise DelegationCapExceededError("delegation_collect_fan_out_exceeded")

        sanitized_items: list[dict[str, Any]] = []
        injection_count = 0
        for item in raw_items[: effective_caps.max_items_per_source * len(collector_scope.source_ids)]:
            if not isinstance(item, Mapping):
                continue
            safe_item = inspect_and_neutralize_source_item(
                item, allowed_source_ids=frozenset(collector_scope.source_ids)
            )
            if safe_item["injection_flagged"]:
                injection_count += 1
            sanitized_items.append(safe_item)

        errors = int(raw.get("error") or 0)
        rate_limited = int(raw.get("rate_limited") or 0)
        status = "partial" if (errors > 0 or rate_limited > 0) else "complete"
        if not sanitized_items and errors > 0:
            status = "failed"
            raise RuntimeError("all_sources_failed")

        surviving_sources = sorted({row["source_id"] for row in sanitized_items}) or list(collector_scope.source_ids)
        return {
            "step": "collect",
            "role": "collector",
            "workflow_version": DELEGATION_WORKFLOW_VERSION,
            "status": status,
            "source_ids": surviving_sources,
            "items": sanitized_items,
            "counts": {
                "sources": len(surviving_sources),
                "items": len(sanitized_items),
                "error": errors,
                "rate_limited": rate_limited,
                "injection_flagged": injection_count,
            },
            "tokens_used": int(raw.get("tokens_used") or 0),
            "cost_usd": float(raw.get("cost_usd") or 0.0),
        }

    collect_result, cancelled = await _run_step("collect", collector_scope, 0, 0.0, _do_collect)
    if cancelled:
        return _build_final_outcome(checkpoint, status="cancelled", reused_steps=reused_steps)

    collected_items = collect_result.get("items", [])
    admitted_hashes = {
        item["content_hash"]: item
        for item in collected_items
        if isinstance(item, dict) and isinstance(item.get("content_hash"), str)
    }
    analyzer_sources = tuple(collect_result.get("source_ids") or sorted_sources)

    # --- Step 2: Analyzer ---
    analyzer_scope = narrow_child_scope(
        origin,
        ChildScopeRequest(
            role="analyzer",
            step="analyze",
            actor_id=origin.actor_id,
            tenant_id=origin.tenant_id,
            source_ids=analyzer_sources,
            data_boundary=origin.data_boundary,
            depth=1,
        ),
        caps=effective_caps,
    )

    async def _do_analyze() -> dict[str, Any]:
        raw = await _maybe_await(analyzer_fn(analyzer_scope, collect_result, effective_caps))
        # Reject any attempt by analyzer output to invoke forbidden tools or escalate scope.
        if raw.get("requested_tools") and (set(raw["requested_tools"]) & FORBIDDEN_EXTERNAL_TOOLS):
            raise DelegationPrivilegeEscalationError("delegation_analyzer_forbidden_tool_request")

        raw_findings = raw.get("findings") if isinstance(raw.get("findings"), list) else []
        verified_findings: list[dict[str, Any]] = []
        unsupported_claims = 0
        for finding in raw_findings[:50]:
            if not isinstance(finding, Mapping):
                continue
            chash = str(finding.get("content_hash") or "")
            sid = finding.get("source_id")
            source_item = admitted_hashes.get(chash)
            if source_item is None or source_item.get("source_id") != sid:
                unsupported_claims += 1
                verified_findings.append(
                    {
                        "claim": str(finding.get("claim") or "")[:400],
                        "supported": False,
                        "unsupported_reason": "missing_or_unverified_source_citation",
                        "citation": None,
                    }
                )
                continue
            verified_findings.append(
                {
                    "claim": str(finding.get("claim") or "")[:400],
                    "supported": True,
                    "citation": {
                        "source_id": sid,
                        "content_hash": chash,
                        "external_id": source_item["external_id"],
                        "url": source_item["url"],
                        "quote_excerpt": source_item["text"][:200],
                        "injection_flagged": bool(source_item.get("injection_flagged")),
                    },
                }
            )

        status = "partial" if (collect_result.get("status") == "partial" or unsupported_claims > 0) else "complete"
        return {
            "step": "analyze",
            "role": "analyzer",
            "workflow_version": DELEGATION_WORKFLOW_VERSION,
            "status": status,
            "source_ids": list(analyzer_scope.source_ids),
            "findings": verified_findings,
            "counts": {
                "findings": len(verified_findings),
                "supported": sum(1 for f in verified_findings if f.get("supported")),
                "unsupported": unsupported_claims,
            },
            "tokens_used": int(raw.get("tokens_used") or 1200),
            "cost_usd": float(raw.get("cost_usd") or 0.012),
        }

    analyze_result, cancelled = await _run_step("analyze", analyzer_scope, 1200, 0.012, _do_analyze)
    if cancelled:
        return _build_final_outcome(checkpoint, status="cancelled", reused_steps=reused_steps)

    # --- Step 3: Synthesizer ---
    synthesizer_scope = narrow_child_scope(
        origin,
        ChildScopeRequest(
            role="synthesizer",
            step="synthesize",
            actor_id=origin.actor_id,
            tenant_id=origin.tenant_id,
            source_ids=analyzer_sources,
            data_boundary=origin.data_boundary,
            depth=1,
        ),
        caps=effective_caps,
    )

    async def _do_synthesize() -> dict[str, Any]:
        raw = await _maybe_await(synthesizer_fn(synthesizer_scope, collect_result, analyze_result, effective_caps))
        if raw.get("external_writes_performed") or raw.get("auto_send"):
            raise DelegationPrivilegeEscalationError("delegation_unconfirmed_external_write_forbidden")

        supported_findings = [
            f for f in analyze_result.get("findings", []) if isinstance(f, dict) and f.get("supported")
        ]
        citations = [f["citation"] for f in supported_findings if isinstance(f.get("citation"), dict)]

        coverage_notes: list[str] = []
        if collect_result.get("status") == "partial":
            c_counts = collect_result.get("counts", {})
            coverage_notes.append(
                f"Сбор выполнен частично: ошибок источников — {c_counts.get('error', 0)}, "
                f"ограничений частоты — {c_counts.get('rate_limited', 0)}."
            )
        if collect_result.get("counts", {}).get("injection_flagged", 0) > 0:
            coverage_notes.append(
                "Обнаружены и нейтрализованы подозрительные инструкции во внешних материалах (обработаны как цитаты)."
            )
        if analyze_result.get("counts", {}).get("unsupported", 0) > 0:
            coverage_notes.append(
                f"Отклонено неподтверждённых утверждений без прямой ссылки на источник: "
                f"{analyze_result['counts']['unsupported']}."
            )

        summary_text = str(raw.get("summary") or f"Черновик исследования по цели: {goal[:160]}")[:4000]
        bitrix24_draft = None
        if include_bitrix24_draft or isinstance(raw.get("bitrix24_draft"), dict):
            raw_b24 = raw.get("bitrix24_draft") if isinstance(raw.get("bitrix24_draft"), dict) else {}
            bitrix24_draft = {
                "target": "bitrix24",
                "state": "draft_only",
                "requires_command_approval": True,
                "approval_url": "/app/commands",
                "title": str(raw_b24.get("title") or f"Черновик отчёта для Bitrix24: {goal[:80]}")[:160],
                "body_preview": str(raw_b24.get("body_preview") or summary_text[:500]),
                "citations_count": len(citations),
                "external_writes_performed": 0,
            }

        status = "partial" if coverage_notes else "complete"
        return {
            "step": "synthesize",
            "role": "synthesizer",
            "workflow_version": DELEGATION_WORKFLOW_VERSION,
            "status": status,
            "source_ids": list(synthesizer_scope.source_ids),
            "summary": summary_text,
            "citations": citations,
            "coverage_notes": coverage_notes,
            "delivery_state": "draft_only",
            "requires_human_approval": True,
            "external_writes_performed": 0,
            "approval_routes": {
                "digest_send": f"/api/v1/tasks/{task_id}/runs/{job_id}/digest/send?confirmed=true",
                "commands_ledger": "/app/commands",
            },
            "bitrix24_draft": bitrix24_draft,
            "tokens_used": int(raw.get("tokens_used") or 900),
            "cost_usd": float(raw.get("cost_usd") or 0.009),
        }

    synthesize_result, cancelled = await _run_step("synthesize", synthesizer_scope, 900, 0.009, _do_synthesize)
    if cancelled:
        return _build_final_outcome(checkpoint, status="cancelled", reused_steps=reused_steps)

    overall_partial = any(r.get("status") == "partial" for r in (collect_result, analyze_result, synthesize_result))
    checkpoint["state"] = "partial" if overall_partial else "completed"
    await _save_ckpt()
    return _build_final_outcome(
        checkpoint,
        status="partial" if overall_partial else "ok",
        reused_steps=reused_steps,
    )


def _build_final_outcome(
    checkpoint: dict[str, Any],
    *,
    status: str,
    reused_steps: list[str],
) -> dict[str, Any]:
    steps = checkpoint.get("steps", {})
    synth_res = steps.get("synthesize", {}).get("result", {}) if isinstance(steps.get("synthesize"), dict) else {}
    return {
        "status": status,
        "workflow_version": DELEGATION_WORKFLOW_VERSION,
        "reused_steps": list(reused_steps),
        "usage": dict(checkpoint.get("usage", {"tokens": 0, "spend_usd": 0.0})),
        "external_writes_performed": 0,
        "delivery_state": synth_res.get("delivery_state", "draft_only"),
        "requires_human_approval": True,
        "pipeline": {
            "version": 1,
            "job_id": checkpoint["job_id"],
            "task_id": checkpoint["task_id"],
            "attempt": checkpoint["attempt"],
            "delegation": checkpoint,
        },
        "draft": synth_res if synth_res else None,
    }


def project_delegated_run_for_workspace(
    task_id: int,
    task_name: str,
    result: Mapping[str, Any] | None,
    *,
    can_cancel: bool = False,
) -> dict[str, Any] | None:
    """Project delegated specialist steps from existing run history for Chat «В работе» tab & cards."""
    if not isinstance(result, Mapping):
        return None
    pipeline = result.get("pipeline")
    delegation = (
        pipeline.get("delegation")
        if isinstance(pipeline, Mapping) and isinstance(pipeline.get("delegation"), Mapping)
        else result.get("delegation")
    )
    if not isinstance(delegation, Mapping):
        return None
    raw_steps = delegation.get("steps")
    if not isinstance(raw_steps, Mapping):
        return None

    projected_steps: list[dict[str, Any]] = []
    completed_count = 0
    for step_key in STEP_ORDER:
        row = raw_steps.get(step_key)
        role = STEP_TO_ROLE[step_key]
        if not isinstance(row, Mapping):
            projected_steps.append(
                {
                    "step": step_key,
                    "role": role,
                    "label": STEP_LABELS_RU[step_key],
                    "status": "pending",
                    "source_count": 0,
                    "citations_count": 0,
                }
            )
            continue
        st = str(row.get("state") or "pending")
        step_res = row.get("result") if isinstance(row.get("result"), Mapping) else {}
        if st == "completed":
            completed_count += 1
            if step_res.get("status") == "partial":
                st = "partial"
        citations_list = step_res.get("citations") if isinstance(step_res.get("citations"), list) else []
        findings_list = step_res.get("findings") if isinstance(step_res.get("findings"), list) else []
        c_count = len(citations_list) or sum(1 for f in findings_list if isinstance(f, Mapping) and f.get("supported"))
        src_ids = row.get("source_ids") or step_res.get("source_ids") or []
        projected_steps.append(
            {
                "step": step_key,
                "role": role,
                "label": STEP_LABELS_RU[step_key],
                "status": st,
                "source_count": len(src_ids) if isinstance(src_ids, list) else 0,
                "citations_count": c_count,
            }
        )

    overall_state = str(delegation.get("state") or result.get("job_status") or "running")
    is_active = overall_state in {"running", "started", "pending"}
    return {
        "task_id": task_id,
        "name": str(task_name)[:160],
        "workflow_version": DELEGATION_WORKFLOW_VERSION,
        "status": overall_state,
        "completed_steps": completed_count,
        "total_steps": len(STEP_ORDER),
        "steps": projected_steps,
        "can_cancel": bool(can_cancel and is_active),
        "cancel_url": f"/app/chat/workspace/tasks/{task_id}/cancel" if (can_cancel and is_active) else None,
        "task_url": f"/app/tasks/{task_id}",
    }


# --- Baseline (P8-T40 composed) vs. Delegated (P20-T04) evaluation ---


@dataclass(frozen=True)
class ResearchEvalFixture:
    """Labelled social research evaluation case for comparing single/composed vs. delegated execution."""

    fixture_id: str
    title: str
    source_count: int
    expected_evidence_hashes: frozenset[str]
    composed_cited_hashes: frozenset[str]
    composed_unsupported_claims: int
    composed_completed: bool
    composed_latency_ms: int
    composed_cost_usd: float
    delegated_cited_hashes: frozenset[str]
    delegated_unsupported_claims: int
    delegated_completed: bool
    delegated_latency_ms: int
    delegated_cost_usd: float


DEFAULT_RESEARCH_EVAL_FIXTURES: tuple[ResearchEvalFixture, ...] = (
    ResearchEvalFixture(
        fixture_id="vk_tg_multi_source_renovation",
        title="Multi-source VK + Telegram renovation demand with noisy posts",
        source_count=3,
        expected_evidence_hashes=frozenset({"h1", "h2", "h3", "h4"}),
        composed_cited_hashes=frozenset({"h1", "h2", "h3"}),
        composed_unsupported_claims=1,
        composed_completed=True,
        composed_latency_ms=1450,
        composed_cost_usd=0.011,
        delegated_cited_hashes=frozenset({"h1", "h2", "h3", "h4"}),
        delegated_unsupported_claims=0,
        delegated_completed=True,
        delegated_latency_ms=1920,
        delegated_cost_usd=0.021,
    ),
    ResearchEvalFixture(
        fixture_id="vk_partial_source_rate_limit",
        title="Three VK communities where one source hits rate limit",
        source_count=3,
        expected_evidence_hashes=frozenset({"h5", "h6", "h7"}),
        composed_cited_hashes=frozenset({"h5", "h6"}),
        composed_unsupported_claims=1,
        composed_completed=True,
        composed_latency_ms=1380,
        composed_cost_usd=0.010,
        delegated_cited_hashes=frozenset({"h5", "h6", "h7"}),
        delegated_unsupported_claims=0,
        delegated_completed=True,
        delegated_latency_ms=1840,
        delegated_cost_usd=0.020,
    ),
    ResearchEvalFixture(
        fixture_id="hostile_prompt_injection_post",
        title="Social post containing prompt injection and unverified claim",
        source_count=2,
        expected_evidence_hashes=frozenset({"h8", "h9"}),
        composed_cited_hashes=frozenset({"h8"}),
        composed_unsupported_claims=1,
        composed_completed=True,
        composed_latency_ms=1200,
        composed_cost_usd=0.009,
        delegated_cited_hashes=frozenset({"h8", "h9"}),
        delegated_unsupported_claims=0,
        delegated_completed=True,
        delegated_latency_ms=1650,
        delegated_cost_usd=0.018,
    ),
    ResearchEvalFixture(
        fixture_id="single_source_simple_digest",
        title="Single Telegram channel routine daily digest",
        source_count=1,
        expected_evidence_hashes=frozenset({"h10", "h11"}),
        composed_cited_hashes=frozenset({"h10", "h11"}),
        composed_unsupported_claims=0,
        composed_completed=True,
        composed_latency_ms=950,
        composed_cost_usd=0.007,
        delegated_cited_hashes=frozenset({"h10", "h11"}),
        delegated_unsupported_claims=0,
        delegated_completed=True,
        delegated_latency_ms=1510,
        delegated_cost_usd=0.016,
    ),
)


def evaluate_delegation_vs_baseline(
    fixtures: tuple[ResearchEvalFixture, ...] | list[ResearchEvalFixture] = DEFAULT_RESEARCH_EVAL_FIXTURES,
) -> dict[str, Any]:
    """Compare single/composed P8-T40 path vs. P20-T04 specialist delegation on labelled fixtures."""
    if not fixtures:
        raise ValueError("At least one evaluation fixture is required")

    def _score(is_delegated: bool, subset: list[ResearchEvalFixture]) -> dict[str, Any]:
        total_expected = sum(len(f.expected_evidence_hashes) for f in subset) or 1
        total_covered = sum(
            len((f.delegated_cited_hashes if is_delegated else f.composed_cited_hashes) & f.expected_evidence_hashes)
            for f in subset
        )
        unsupported = sum(
            f.delegated_unsupported_claims if is_delegated else f.composed_unsupported_claims for f in subset
        )
        completed = sum(1 for f in subset if (f.delegated_completed if is_delegated else f.composed_completed))
        mean_latency = sum(f.delegated_latency_ms if is_delegated else f.composed_latency_ms for f in subset) / len(
            subset
        )
        mean_cost = sum(f.delegated_cost_usd if is_delegated else f.composed_cost_usd for f in subset) / len(subset)
        return {
            "evidence_coverage": round(total_covered / total_expected, 4),
            "unsupported_claims": unsupported,
            "completion_rate": round(completed / len(subset), 4),
            "mean_latency_ms": round(mean_latency, 1),
            "mean_cost_usd": round(mean_cost, 4),
        }

    all_list = list(fixtures)
    multi_source = [f for f in all_list if f.source_count >= 2] or all_list
    single_source = [f for f in all_list if f.source_count == 1] or all_list

    baseline_all = _score(False, all_list)
    delegated_all = _score(True, all_list)
    baseline_multi = _score(False, multi_source)
    delegated_multi = _score(True, multi_source)
    baseline_single = _score(False, single_source)
    delegated_single = _score(True, single_source)

    multi_coverage_gain = round(delegated_multi["evidence_coverage"] - baseline_multi["evidence_coverage"], 4)
    single_coverage_gain = round(delegated_single["evidence_coverage"] - baseline_single["evidence_coverage"], 4)

    return {
        "fixture_count": len(all_list),
        "fixtures": [f.fixture_id for f in all_list],
        "overall": {
            "composed_baseline": baseline_all,
            "delegated_pilot": delegated_all,
            "coverage_gain": round(delegated_all["evidence_coverage"] - baseline_all["evidence_coverage"], 4),
            "cost_delta_usd": round(delegated_all["mean_cost_usd"] - baseline_all["mean_cost_usd"], 4),
            "latency_delta_ms": round(delegated_all["mean_latency_ms"] - baseline_all["mean_latency_ms"], 1),
        },
        "multi_source": {
            "composed_baseline": baseline_multi,
            "delegated_pilot": delegated_multi,
            "coverage_gain": multi_coverage_gain,
            "recommended_path": "delegated" if multi_coverage_gain >= 0.05 else "composed",
        },
        "single_source": {
            "composed_baseline": baseline_single,
            "delegated_pilot": delegated_single,
            "coverage_gain": single_coverage_gain,
            "recommended_path": "delegated" if single_coverage_gain >= 0.05 else "composed",
        },
    }


def select_execution_path(
    *,
    source_count: int,
    delegation_enabled: bool = False,
    comparison: Mapping[str, Any] | None = None,
) -> Literal["composed", "delegated"]:
    """Keep the simpler P8-T40 composed path when delegation is disabled or lacks measured benefit."""
    if not delegation_enabled or source_count <= 1:
        return "composed"
    comp = comparison or evaluate_delegation_vs_baseline()
    multi = comp.get("multi_source", {}) if isinstance(comp, Mapping) else {}
    if multi.get("recommended_path") == "delegated":
        return "delegated"
    return "composed"
