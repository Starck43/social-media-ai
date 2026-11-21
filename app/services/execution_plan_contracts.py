"""Versioned data contract for canonical task execution plans."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from app.integrations.catalog.task_capabilities import CAPABILITIES, Direction, ResourceKind
from app.integrations.task_resources import ResourceSelection, TelegramGrantPin, WorkspaceAccountPin
from app.services.social.vk_access import parse_vk_selection
from app.services.task_plans import _parse_trigger, parse_task_plan, task_plan_block
from app.tasks.cron import validate_cron
from app.types import AgentActionType, BotTriggerType

MAX_BINDINGS = 500
MAX_STEPS = 12
Name = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_]{0,63}$")]
PositiveID = Annotated[int, Field(gt=0, le=2147483647)]
Port = Literal["posts", "analysis", "mail", "groups"]


class ExecutionPlanError(ValueError):
    """Fixed error code for consumers and preview surfaces."""


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True, hide_input_in_errors=True)


class Binding(Contract):
    name: Name
    capability: str = Field(max_length=80)
    resource_id: PositiveID
    path: str | None = Field(default=None, max_length=1024)
    vk_access: dict[str, JsonValue] | None = None
    telegram_grant: tuple[PositiveID, PositiveID] | None = None
    workspace_account: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")

    def selection(self) -> ResourceSelection:
        capability = CAPABILITIES.get(self.capability)
        if capability is None or capability.kind not in {ResourceKind.SOCIAL_SOURCE, ResourceKind.MAIL_FOLDER}:
            raise ExecutionPlanError("unsupported_capability")
        return ResourceSelection(
            kind=capability.kind,
            resource_id=self.resource_id,
            path=self.path,
            vk_access=parse_vk_selection(self.vk_access) if self.vk_access is not None else None,
            telegram_grant=TelegramGrantPin(*self.telegram_grant) if self.telegram_grant is not None else None,
            workspace_account=(
                WorkspaceAccountPin(self.workspace_account) if self.workspace_account is not None else None
            ),
        )


class Trigger(Contract):
    type: Literal["once", "schedule"] = "once"
    cron: str | None = Field(default=None, max_length=100)
    timezone: str | None = Field(default=None, max_length=100)

    @model_validator(mode="after")
    def check_schedule(self):
        parsed = _parse_trigger(self.model_dump())
        if parsed.cron != self.cron or parsed.timezone != self.timezone:
            raise ValueError("invalid_schedule")
        if self.type == "schedule" and (not self.timezone or not validate_cron(self.cron)):
            raise ValueError("invalid_schedule")
        if self.type == "once" and (self.cron is not None or self.timezone is not None):
            raise ValueError("invalid_schedule")
        return self


class LegacyEffects(Contract):
    trigger_type: Literal[*tuple(item.name for item in BotTriggerType)] | None = None
    trigger_config: dict[str, JsonValue] = Field(default_factory=dict)
    action_type: Literal[*tuple(item.name for item in AgentActionType)] | None = None
    rate_limit_per_hour: int | None = Field(default=None, ge=0, le=2147483647)
    cooldown_seconds: int | None = Field(default=None, ge=0, le=2147483647)


@dataclass(frozen=True)
class Operation:
    output: Port | None
    input_port: Port | None = None
    resource_kind: ResourceKind | None = None
    llm: bool = False
    partial: bool = False


OPERATIONS = MappingProxyType(
    {
        "social.collect": Operation("posts", resource_kind=ResourceKind.SOCIAL_SOURCE),
        "social.cached": Operation("posts", resource_kind=ResourceKind.SOCIAL_SOURCE),
        "social.analyze": Operation("analysis", "posts", llm=True),
        "social.digest": Operation(None, "analysis", llm=True),
        "mail.read_headers": Operation("mail", resource_kind=ResourceKind.MAIL_FOLDER, partial=True),
        "mail.group_by_sender": Operation("groups", "mail", partial=True),
        "messages.store_groups": Operation(None, "groups"),
    }
)


class ReadWindow(Contract):
    type: Literal["fixed", "rolling"]
    start: date | None = None
    end: date | None = None
    days: int | None = Field(default=None, gt=0, le=365)

    @model_validator(mode="after")
    def check_window(self):
        if self.type == "rolling":
            if self.days is None or self.start is not None or self.end is not None:
                raise ValueError("invalid_read_window")
        elif (
            self.days is not None
            or self.start is None
            or self.end is None
            or not 0 <= (self.end - self.start).days < 365
        ):
            raise ValueError("invalid_read_window")
        return self


class Step(Contract):
    name: Name
    operation: str = Field(max_length=64)
    inputs: dict[Port, tuple[Name, ...]] = Field(default_factory=dict)
    bindings: tuple[Name, ...] = Field(default=(), max_length=MAX_BINDINGS)
    scenario_id: PositiveID | None = None
    on_failure: Literal["stop", "continue_partial"] = "stop"
    max_items: int | None = Field(default=None, gt=0, le=1000)
    window: ReadWindow | None = None


class ExecutionPlan(Contract):
    version: Literal[1] = Field(default=1)
    steps: tuple[Step, ...] = Field(min_length=1, max_length=MAX_STEPS)
    bindings: tuple[Binding, ...] = Field(default=(), max_length=MAX_BINDINGS)
    trigger: Trigger = Field(default_factory=Trigger)
    dry_run: bool = False
    # Only the legacy adapter constructs this block. It is also used to
    # reconstruct the exact compatibility projection for a later backfill.
    social_plan: dict[str, JsonValue] | None = None
    legacy_effects: LegacyEffects | None = None

    @field_validator("version", mode="before")
    @classmethod
    def check_version(cls, version):
        if type(version) is not int or version != 1:
            raise ValueError("unsupported_plan_version")
        return version

    @model_validator(mode="after")
    def check_graph(self):
        bindings = {binding.name: binding for binding in self.bindings}
        if len(bindings) != len(self.bindings):
            raise ExecutionPlanError("duplicate_binding")
        social = parse_task_plan(self.social_plan) if self.social_plan is not None else None
        if social is not None:
            stages = (
                social.pipeline.stages
                if social.pipeline
                else (
                    ("collect", "analyze")
                    if len(self.steps) == 2 and self.steps[0].operation == "social.collect"
                    else ("analyze",) if len(self.steps) == 2 else ("collect",)
                )
            )
            for binding in self.bindings:
                selection = binding.selection()
                if (
                    selection.kind != ResourceKind.SOCIAL_SOURCE
                    or CAPABILITIES[binding.capability].provider not in social.platforms
                    or (binding.capability == "vk.posts.read" and selection.vk_access != social.vk_access)
                ):
                    raise ExecutionPlanError("legacy_selection_mismatch")
            if sorted(binding.resource_id for binding in self.bindings) != sorted(social.source_ids):
                raise ExecutionPlanError("legacy_selection_mismatch")
            if self.steps != _social_steps(stages, self.bindings, social.scenario_id):
                raise ExecutionPlanError("invalid_social_route")
            if (
                self.trigger.type != social.trigger.type
                or self.trigger.cron != social.trigger.cron
                or (social.trigger.timezone is not None and self.trigger.timezone != social.trigger.timezone)
            ):
                raise ExecutionPlanError("invalid_social_trigger")
        producers = {}
        used = set()
        for step in self.steps:
            operation = OPERATIONS.get(step.operation)
            if operation is None or step.name in producers:
                raise ExecutionPlanError("invalid_operation")
            if bool(operation.llm) != (step.scenario_id is not None):
                raise ExecutionPlanError("invalid_scenario_binding")
            if step.on_failure == "continue_partial" and not operation.partial:
                raise ExecutionPlanError("invalid_failure_policy")
            if step.operation.startswith("social.") != (social is not None):
                raise ExecutionPlanError("invalid_social_route")
            expected = {operation.input_port} if operation.input_port else set()
            if step.inputs.keys() != expected:
                raise ExecutionPlanError("invalid_step_ports")
            for port, upstream in step.inputs.items():
                if not upstream or len(upstream) > MAX_STEPS or len(set(upstream)) != len(upstream):
                    raise ExecutionPlanError("invalid_step_ports")
                if any(producers.get(name) != port for name in upstream):
                    raise ExecutionPlanError("invalid_step_ports")
            if step.operation == "mail.read_headers":
                if step.max_items is None or len(step.bindings) != 1:
                    raise ExecutionPlanError("invalid_mail_read")
            elif step.max_items is not None or step.window is not None:
                raise ExecutionPlanError("invalid_step_parameters")
            allows_dynamic = (
                operation.resource_kind == ResourceKind.SOCIAL_SOURCE
                and social is not None
                and social.source_scope != "selected"
            )
            if (step.bindings and (operation.resource_kind is None or allows_dynamic)) or (
                not step.bindings and operation.resource_kind is not None and not allows_dynamic
            ):
                raise ExecutionPlanError("invalid_step_bindings")
            if len(set(step.bindings)) != len(step.bindings):
                raise ExecutionPlanError("invalid_step_bindings")
            for name in step.bindings:
                binding = bindings.get(name)
                if binding is None:
                    raise ExecutionPlanError("invalid_step_bindings")
                selection = binding.selection()
                capability = CAPABILITIES[binding.capability]
                if selection.kind != operation.resource_kind or capability.direction != Direction.READ:
                    raise ExecutionPlanError("invalid_resource_direction")
                used.add(name)
            producers[step.name] = operation.output
        if used != bindings.keys():
            raise ExecutionPlanError("unused_binding")
        if social is None and self.legacy_effects is not None:
            raise ExecutionPlanError("invalid_legacy_effects")
        return self


def parse_execution_plan(raw_json: str) -> ExecutionPlan:
    """Canonical JSON parser for transports and for immutable revision lookup."""
    if not isinstance(raw_json, str) or len(raw_json.encode("utf-8")) > 256 * 1024:
        raise ExecutionPlanError("invalid_execution_plan")
    try:
        return ExecutionPlan.model_validate_json(raw_json)
    except ValueError:
        raise ExecutionPlanError("invalid_execution_plan") from None


def map_social_plan(
    payload: dict,
    *,
    job_type: Literal["collect", "analyze"],
    bindings: tuple[Binding, ...] = (),
    dry_run: bool = False,
    effects: LegacyEffects | None = None,
    timezone: str = "UTC",
) -> ExecutionPlan | None:
    """Bridge a saved versioned plan. Legacy no-plan rows keep their own semantics."""
    if "task_plan" not in payload:
        return None
    if job_type not in {"collect", "analyze"}:
        raise ExecutionPlanError("unsupported_legacy_job")
    plan = parse_task_plan(payload["task_plan"])
    if plan.pipeline and job_type != "analyze":
        raise ExecutionPlanError("unsupported_legacy_job")
    if sorted(binding.resource_id for binding in bindings) != sorted(plan.source_ids):
        raise ExecutionPlanError("legacy_selection_mismatch")
    stages = (
        plan.pipeline.stages
        if plan.pipeline
        else (
            ("collect", "analyze")
            if job_type == "analyze" and payload.get("collect_before_analyze") is True
            else (job_type,)
        )
    )
    trigger = Trigger(
        type=plan.trigger.type,
        cron=plan.trigger.cron,
        timezone=(plan.trigger.timezone or timezone) if plan.trigger.type == "schedule" else None,
    )
    return ExecutionPlan(
        steps=_social_steps(stages, bindings, plan.scenario_id),
        bindings=bindings,
        trigger=trigger,
        dry_run=dry_run,
        social_plan=task_plan_block(plan),
        legacy_effects=effects,
    )


def _social_steps(stages, bindings, scenario_id) -> tuple[Step, ...]:
    reads = "social.collect" if "collect" in stages else "social.cached"
    steps = [Step(name="read_posts", operation=reads, bindings=tuple(binding.name for binding in bindings))]
    if "analyze" in stages:
        steps.append(
            Step(name="analyze", operation="social.analyze", inputs={"posts": ("read_posts",)}, scenario_id=scenario_id)
        )
    if "digest" in stages:
        steps.append(
            Step(name="digest", operation="social.digest", inputs={"analysis": ("analyze",)}, scenario_id=scenario_id)
        )
    return tuple(steps)
