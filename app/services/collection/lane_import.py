"""Import a pasted list of VK communities and people as a task's wall lanes.

VK's global search answers error 1051 for this deployment's app profile, so
the operator picks the relevant communities in the vk.ru web UI and feeds that
list into a task here: the import resolves every entry with the methods that
still work (`groups.getById`, `users.get` — read-only) and turns each hit into
a task-owned wall lane, so the task collects the wall without any search.

The module keeps the lane-migration split:

* `parse_lane_entry` and `resolve_lane_targets` normalise the pasted text and
  resolve identities (read-only VK calls, batch continues past unknown ones);
* `plan_lane_import` is pure: it matches the resolved targets against the
  sources the workspace already has and returns the decisions;
* `apply_lane_import` is the only writer: it creates the missing task-owned
  lane rows and links the task through the shared manager.

Nothing is ever deleted and another task's lane is never touched: a source
owned by another task is refused and reported, never relinked or edited.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

from sqlalchemy.exc import DBAPIError, ProgrammingError

from app.services.collection.lane_migration import LaneMigrationError, _schema_error
from app.services.collection.planner import SourceRef

KIND_COMMUNITY = "community"
KIND_PERSON = "person"

# Actions of a plan decision.
REUSE = "reuse"
CREATE = "create"
SKIP = "skip"

# Source.params of a created lane: `auto` keeps the L1->L2 token fallback.
LANE_PARAMS = {"mode": "auto"}

# Short vk.com prefixes (club113, public113, event123, id123) bind the kind;
# they are real only when the rest is all digits, so a screen name like
# "idea_club" is never mistaken for a prefixed id.
_COMMUNITY_PREFIXES = ("club", "public", "event")
_PERSON_PREFIXES = ("id",)


@dataclass(frozen=True)
class ParsedEntry:
    """One pasted line, normalised: what to resolve and which kind it binds."""

    raw: str
    # The text to hand to the VK resolver: a screen name, or an id without the
    # minus (VK wants a positive `group_id`).
    value: str
    # community | person | None — None means "resolve as a community first,
    # then as a person".
    kind: str | None
    # The numeric id the entry itself carried, if any (always positive here;
    # the kind, not the sign, says community or person).
    literal_id: int | None


@dataclass(frozen=True)
class ResolvedTarget:
    """One entry resolved against VK, or reported as unresolvable."""

    raw: str
    kind: str
    # The canonical lane identity in our model: negative for a community.
    external_id: str
    numeric_id: int
    screen_name: str | None
    name: str | None
    parsed_value: str = ""
    error: str | None = None


@dataclass(frozen=True)
class LaneImportDecision:
    """What would happen to one resolved target. Nothing is written yet."""

    raw: str
    source_type: str  # "GROUP" | "USER" (empty for an unresolvable target)
    external_id: str  # the canonical identity: -113 for a community
    kind: str  # community | person
    name: str  # the resolved VK name, the requested text as fallback
    action: str  # reuse | create | skip
    source_id: int | None = None  # the reused source
    reason: str | None = None  # why a target is skipped


@dataclass(frozen=True)
class LaneImportPlan:
    """What the import would do. Nothing is written while a plan exists."""

    task_id: int
    task_name: str
    tenant_id: int
    platform: str
    decisions: list[LaneImportDecision] = field(default_factory=list)
    # The sources already linked to the task plus the reused ones; the new
    # lanes join them at apply time.
    linked_source_ids: list[int] = field(default_factory=list)


@dataclass(frozen=True)
class LaneImportResult:
    """What the import actually changed."""

    task_id: int
    created_source_ids: list[int] = field(default_factory=list)
    reused_source_ids: list[int] = field(default_factory=list)
    linked_source_ids: list[int] = field(default_factory=list)
    refused: list[str] = field(default_factory=list)


def parse_lane_entry(raw: str, kind: str | None = None) -> ParsedEntry:
    """Normalise one pasted entry; raises :class:`LaneMigrationError` on nonsense.

    Accepted: a screen name (`dreviz2000`), a signed numeric id (`-113075242`
    is a community), the short vk.com prefixes (`club113`, `public113`,
    `event123`, `id12345`) and a vk.com URL with or without a scheme and with
    query/fragment. A `club`/`public`/`event` prefix implies a community, `id`
    implies a person; a plain screen name binds no kind and resolves as a
    community first, then as a person. A negative id is always a community —
    forcing `person` on one (or on a `club` URL) is refused, not silently
    reinterpreted.
    """
    if kind not in (None, KIND_COMMUNITY, KIND_PERSON):
        raise LaneMigrationError(f"unknown kind {kind!r}: expected community or person")
    text = " ".join(str(raw or "").split())
    if not text:
        raise LaneMigrationError("empty entry: a lane needs a community or a person")

    # A pasted URL (vk.com/..., m.vk.com/...): keep the last path segment, so
    # the scheme, host, query and fragment fall away.
    parts = urlsplit(text if "://" in text else "//" + text)
    segments = [segment for segment in parts.path.split("/") if segment]
    value = segments[-1] if segments else parts.netloc
    if not value:
        raise LaneMigrationError(f"cannot read an identifier from {raw!r}")

    entry_kind = kind
    literal_id: int | None = None
    prefix_kind: str | None = None
    lowered = value.lower()
    for prefix in _PERSON_PREFIXES + _COMMUNITY_PREFIXES:
        if lowered.startswith(prefix) and value[len(prefix):].isdigit():
            literal_id = int(value[len(prefix):])
            value = value[len(prefix):]
            prefix_kind = KIND_PERSON if prefix in _PERSON_PREFIXES else KIND_COMMUNITY
            break
    if prefix_kind is not None:
        if kind is not None and kind != prefix_kind:
            raise LaneMigrationError(f"{raw!r} is a {prefix_kind}, not a {kind}")
        entry_kind = prefix_kind
    elif re.fullmatch(r"-?\d+", value):
        number = int(value)
        literal_id = abs(number)
        value = str(literal_id)
        if number < 0:
            # Our model stores a community wall as owner_id=-<id>: the sign,
            # not the --person flag, decides the kind here.
            if kind == KIND_PERSON:
                raise LaneMigrationError(f"{raw!r} is a community id (negative), not a person")
            entry_kind = KIND_COMMUNITY
    return ParsedEntry(raw=str(raw), value=value, kind=entry_kind, literal_id=literal_id)


async def resolve_lane_targets(
    entries: Sequence[ParsedEntry],
    *,
    platform: str,
    kind: str | None = None,
) -> list[ResolvedTarget]:
    """Resolve every entry through the social client's read-only resolvers.

    `kind` (community | person) is the default for entries that bind no kind
    of their own. A plain screen name resolves as a community first, then as a
    person: `users.get` answers "not a person" for a community nick anyway.
    An entry VK does not know is reported with an `error` and the batch
    continues; a token, authorization or transport error propagates and stops
    the import — a half-credential must never read as "nothing found".
    """
    from app.models import Platform
    from app.services.social.factory import get_social_client

    if kind not in (None, KIND_COMMUNITY, KIND_PERSON):
        raise LaneMigrationError(f"unknown kind {kind!r}: expected community or person")
    row = await Platform.objects.filter(platform_type=platform).first()
    if row is None:
        raise LaneMigrationError(f"platform '{platform}' is not configured")
    client = get_social_client(row)
    resolve_group = getattr(client, "resolve_group", None)
    resolve_user = getattr(client, "resolve_user", None)
    if resolve_group is None or resolve_user is None:
        raise LaneMigrationError(f"platform '{platform}' cannot import walls by identifier")

    targets: list[ResolvedTarget] = []
    seen: set[tuple[str, str]] = set()
    for entry in entries:
        entry_kind = entry.kind or kind
        key = (entry_kind or "", entry.value)
        if key in seen:
            continue
        seen.add(key)

        info: dict | None = None
        used_kind = KIND_COMMUNITY
        if entry_kind in (None, KIND_COMMUNITY):
            info = await resolve_group(entry.value)
        if info is None and entry_kind in (None, KIND_PERSON):
            info = await resolve_user(entry.value)
            used_kind = KIND_PERSON
        if info is None:
            if entry_kind == KIND_COMMUNITY:
                known = "community"
            elif entry_kind == KIND_PERSON:
                known = "person"
            else:
                known = "community or person"
            targets.append(
                ResolvedTarget(
                    raw=entry.raw,
                    kind="",
                    external_id="",
                    numeric_id=0,
                    screen_name=None,
                    name=None,
                    parsed_value=entry.value,
                    error=f"vk has no such {known}",
                )
            )
            continue

        targets.append(
            ResolvedTarget(
                raw=entry.raw,
                kind=used_kind,
                external_id=str(info["id"]),
                numeric_id=int(info["id"]),
                screen_name=info.get("screen_name"),
                name=info.get("name"),
                parsed_value=entry.value,
            )
        )
    return targets


def plan_lane_import(
    task: Any,
    targets: Sequence[ResolvedTarget],
    *,
    platform: str,
    workspace_sources: Sequence[Any] = (),
) -> LaneImportPlan:
    """Match the targets against the sources the task may touch. Pure.

    `workspace_sources` are the rows the workspace already has for the
    platform (the CLI prefetches them; the task's linked sources are a subset).
    Decisions per target: a source owned by another task is refused and
    reported — a lane is never stolen from its task; an unowned workspace
    source (or one owned by this task) with the same screen name or numeric id
    is reused as-is, never retyped; the rest are created task-owned.
    """
    platform_key = " ".join(str(platform or "").split()).lower()
    if not platform_key:
        raise LaneMigrationError("no platform given: pass --platform")

    linked = {int(source.id) for source in (getattr(task, "sources", None) or [])}
    decisions: list[LaneImportDecision] = []
    for target in targets:
        name = target.name or target.raw
        kind = target.kind or ""
        common = {
            "raw": target.raw,
            "source_type": source_type_for(kind),
            "external_id": target.external_id,
            "kind": kind,
            "name": name,
        }
        if target.error is not None:
            decisions.append(LaneImportDecision(**common, action=SKIP, reason=target.error))
            continue

        owner, source_id, reason = _match_workspace_source(workspace_sources, target, platform_key)
        if reason is not None:
            decisions.append(LaneImportDecision(**common, action=SKIP, reason=reason))
            continue
        if owner is not None and owner != int(task.id):
            # Not ours: linking it here would collect the same wall twice —
            # once in each task. The import reports and moves on.
            decisions.append(
                LaneImportDecision(
                    **common, action=SKIP, source_id=source_id,
                    reason=f"source {source_id} belongs to task {owner}",
                )
            )
            continue
        if source_id is not None:
            decisions.append(LaneImportDecision(**common, action=REUSE, source_id=source_id))
            continue
        decisions.append(LaneImportDecision(**common, action=CREATE))

    reused = {decision.source_id for decision in decisions if decision.action == REUSE and decision.source_id}
    return LaneImportPlan(
        task_id=int(task.id),
        task_name=str(getattr(task, "name", "") or ""),
        tenant_id=int(task.tenant_id),
        platform=platform_key,
        decisions=decisions,
        linked_source_ids=sorted(linked | reused),
    )


async def apply_lane_import(plan: LaneImportPlan) -> LaneImportResult:
    """Create the missing lanes and link the task. The only writer.

    Idempotent: every create is looked up by (workspace, platform,
    external_id) first, so a second run creates nothing and changes no links.
    Nothing is ever deleted, and a source another task owns is refused, never
    relinked or edited — the plan that asked for it is re-checked against the
    database, which is the authority at write time. A schema without
    `sources.owner_task_id` fails closed: the apply raises instead of writing
    half a lane.
    """
    from app.models import AgentTask, Platform, Source
    from app.models.managers.agent_task_manager import AgentTaskManager
    from app.types import SourceType

    try:
        task = await AgentTask.objects.get(id=plan.task_id)
        if task is None:
            raise LaneMigrationError(f"task {plan.task_id} not found in this workspace")
        if task.tenant_id != plan.tenant_id:
            raise LaneMigrationError(f"task {plan.task_id} is not in workspace {plan.tenant_id}")

        platform = await Platform.objects.filter(platform_type=plan.platform).first()
        if platform is None:
            raise LaneMigrationError(f"platform '{plan.platform}' is not configured")

        external_ids = [decision.external_id for decision in plan.decisions if decision.external_id]
        by_external: dict[str, Any] = {}
        if external_ids:
            rows = await Source.objects.filter(
                tenant_id=plan.tenant_id,
                platform_id=platform.id,
                external_id__in=external_ids,
            )
            by_external = {row.external_id: row for row in rows}
        by_id: dict[int, Any] = {}
        reuse_ids = [
            decision.source_id
            for decision in plan.decisions
            if decision.action == REUSE and decision.source_id
        ]
        if reuse_ids:
            rows = await Source.objects.filter(id__in=reuse_ids, tenant_id=plan.tenant_id)
            by_id = {int(row.id): row for row in rows}

        created: list[int] = []
        reused: list[int] = []
        refused: list[str] = []
        lane_ids: list[int] = []
        for decision in plan.decisions:
            if decision.action == SKIP:
                continue
            row = by_id.get(int(decision.source_id)) if decision.source_id is not None else None
            if decision.action == REUSE:
                if row is None or row.platform_id != platform.id:
                    # The plan's row vanished or moved platform under the
                    # plan: refuse the target, never relink blindly.
                    refused.append(f"{decision.raw}: source {decision.source_id} changed; re-run the plan")
                    continue
            else:
                row = by_external.get(decision.external_id)
            if row is not None:
                owner = getattr(row, "owner_task_id", None)
                if owner is not None and int(owner) != plan.task_id:
                    refused.append(f"{decision.raw}: source {row.id} belongs to task {int(owner)}")
                    continue
                reused.append(row.id)
                lane_ids.append(row.id)
                continue
            row = await Source.objects.create(
                platform_id=platform.id,
                name=decision.name,
                source_type=SourceType[decision.source_type],
                external_id=decision.external_id,
                params=dict(LANE_PARAMS),
                owner_task_id=plan.task_id,
                is_active=True,
            )
            created.append(row.id)
            lane_ids.append(row.id)

        # The single write path used by the web form, the CLI and the agent
        # tools: it keeps the links that already exist and adds the new ones.
        await AgentTaskManager().add_sources(plan.task_id, list(dict.fromkeys(lane_ids)))
        linked = await AgentTaskManager().get_sources(plan.task_id)

        return LaneImportResult(
            task_id=plan.task_id,
            created_source_ids=created,
            reused_source_ids=reused,
            linked_source_ids=linked,
            refused=refused,
        )
    except LaneMigrationError:
        raise
    except (ProgrammingError, DBAPIError) as error:
        guarded = _schema_error(error)
        if guarded is not None:
            raise guarded from error
        raise


def source_type_for(kind: str) -> str:
    """`SourceType` name of a lane: GROUP for a community, USER for a person."""
    if kind == KIND_COMMUNITY:
        return "GROUP"
    if kind == KIND_PERSON:
        return "USER"
    return ""


def _match_workspace_source(
    workspace_sources: Sequence[Any],
    target: ResolvedTarget,
    platform: str,
) -> tuple[int | None, int | None, str | None]:
    """The source that already holds this identity as (owner, source_id, reason).

    One candidate decides by its owner; several candidates are refused with a
    reason — the (tenant, platform, external_id) unique key stops duplicate
    ids, but a legacy screen-name row can sit beside a numeric one, and
    guessing between them would link an unknown lane.
    """
    by_id: list[tuple[int, int | None]] = []
    by_name: list[tuple[int, int | None]] = []
    for source in workspace_sources:
        ref = SourceRef.from_source(source)
        if ref.platform != platform:
            continue
        owner_value = getattr(source, "owner_task_id", None)
        owner = int(owner_value) if owner_value is not None else None
        if _numeric_identity(ref) == target.numeric_id:
            by_id.append((int(source.id), owner))
        elif target.screen_name and ref.external_id.strip().casefold() == target.screen_name.strip().casefold():
            by_name.append((int(source.id), owner))
    candidates = by_id if by_id else by_name
    if not candidates:
        return None, None, None
    if len(candidates) > 1:
        matched = ", ".join(str(source_id) for source_id, _ in candidates)
        return None, None, f"several sources match ({matched}); link one manually"
    source_id, owner = candidates[0]
    return owner, source_id, None


_COMMUNITY_SOURCE_TYPES = frozenset({"group", "channel", "public", "page", "event", "market"})


def _numeric_identity(ref: SourceRef) -> int | None:
    """Canonical signed id behind a source identity, vk.com prefixes stripped.

    Legacy rows may carry `club113`/`public113`-style ids and sloppy wall rows
    a bare positive number — the row's own type says community or person when
    the prefix does not. The canonical form is signed (a community wall is
    negative, a person positive), so "113" never matches a community lane.
    """
    text = str(ref.external_id or "").strip()
    negative: bool | None = None
    for prefix in _PERSON_PREFIXES + _COMMUNITY_PREFIXES:
        if text.lower().startswith(prefix) and text[len(prefix):].isdigit():
            text = text[len(prefix):]
            negative = prefix != "id"
            break
    if not re.fullmatch(r"-?\d+", text):
        return None
    if negative is None:
        source_type = (ref.source_type or "").lower()
        if source_type in _COMMUNITY_SOURCE_TYPES:
            negative = True
        elif source_type in ("user", "bot"):
            negative = False
    number = int(text)
    if negative is None:
        # A bare number of an unknown type matches by the sign it carries.
        return number
    return -abs(number) if negative else abs(number)
