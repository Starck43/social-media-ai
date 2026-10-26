"""Population step: the task's own search, resolved into task-owned lanes.

The task intent declares *what* to find — discovery phrases, the kinds
(communities/people) and the city. This module searches the provider for those
entities and turns every found entity into a task-owned wall lane by reusing
the reviewed lane-import rules (`plan_lane_import` + `apply_lane_import`), so
there is exactly one rule set for identity reuse and refusal.

A blocked search is a NAMED result, never "nothing found": a provider error
(e.g. VK error 1051 — the method is not available for this app profile) is
translated into a bounded :class:`PopulationError` code an operator can act
on. In a dry run nothing is written — the result only reports what WOULD be
created.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from app.services.collection.intent import MATCH_ALL, MATCH_ANY, CollectionIntent
from app.services.collection.lane_import import (
    CREATE,
    KIND_COMMUNITY,
    KIND_PERSON,
    REUSE,
    SKIP,
    ResolvedTarget,
    apply_lane_import,
    plan_lane_import,
)
from app.services.collection.pages import ProviderCapabilities
from app.services.social.credentials import AuthorizationRequired

if TYPE_CHECKING:
    from app.services.collection.intent import DiscoveryIntent

# The lane_type this step reports under in `collection_lane_runs`.
CANDIDATE_SEARCH_LANE_TYPE = "candidate_search"

# How far one search page goes: the task's own candidate bound.
MAX_SEARCH_PAGE = 1000

# Bounded, operator-facing reasons. A code (api_<n> or population_*) plus one
# of these hints is everything an operator report carries — never raw provider
# or exception text.
POPULATION_HINTS = {
    "population_city_unknown": "город поиска не найден в справочнике платформы",
    "population_search_unsupported": "платформа не поддерживает поиск людей и сообществ",
    "population_auth_required": "нужен личный токен платформы, чтобы искать",
    "5": "токен недействителен",
    "6": "слишком много запросов в секунду",
    "9": "flood control",
    "29": "исчерпан лимит метода",
    "100": "неверные параметры запроса",
    "1051": "метод поиска недоступен для типа приложения",
}

_API_ERROR_RE = re.compile(r"Ошибка API \((\d+)\)")

# Bounded transport categories, mirroring the jobs handler's classifier.
_EXPECTED_ERROR_KINDS = (
    (TimeoutError, "timeout"),
    (ConnectionError, "connection_error"),
    (OSError, "io_error"),
    (ValueError, "value_error"),
    (RuntimeError, "runtime_error"),
)

# Warnings stay a bounded, human-auditable list.
MAX_WARNINGS = 20
MAX_TEXT = 160


class PopulationError(RuntimeError):
    """A blocked or failed population step with a machine-readable code.

    Codes: `api_<vk code>` for a provider answer (e.g. `api_1051`),
    `population_city_unknown`, `population_search_unsupported`,
    `population_auth_required`. The hint is a bounded operator text, never the
    raw error. `query` names the phrase the failure happened on, if any.
    """

    def __init__(self, code: str, *, hint: str | None = None) -> None:
        self.code = code
        self.hint = hint or self._default_hint(code)
        self.query: str | None = None
        super().__init__(code)

    @staticmethod
    def _default_hint(code: str) -> str:
        if code in POPULATION_HINTS:
            return POPULATION_HINTS[code]
        return POPULATION_HINTS.get(code.removeprefix("api_"), "ошибка поиска")


@dataclass(frozen=True)
class PopulationEntity:
    """One entity the search found: the lane identity it would become."""

    kind: str  # community | person
    external_id: str  # the canonical lane identity (negative for a community)
    numeric_id: int
    name: str | None = None
    screen_name: str | None = None

    @property
    def identity(self) -> tuple[str, int]:
        return (self.kind, self.numeric_id)


@dataclass(frozen=True)
class PopulationPhrase:
    """What one discovery phrase alone produced."""

    query: str
    communities: int = 0
    people: int = 0


@dataclass(frozen=True)
class PopulationResult:
    """What the population step found and (outside a dry run) wrote."""

    platform: str
    dry_run: bool
    ran: bool = False  # a task without a population declaration is a no-op
    city_name: str | None = None
    city_id: int | None = None
    queries: tuple[str, ...] = ()
    entities: tuple[PopulationEntity, ...] = ()
    per_phrase: tuple[PopulationPhrase, ...] = ()
    created_source_ids: tuple[int, ...] = ()
    reused_source_ids: tuple[int, ...] = ()
    linked_source_ids: tuple[int, ...] = ()
    refused: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()
    # Dry run only: the entities a real run would turn into NEW lanes.
    would_create: tuple[PopulationEntity, ...] = ()


def _normalise_platform(value: str) -> str:
    return str(value or "").strip().lower()


def _brief(text: Any, limit: int = MAX_TEXT) -> str:
    return " ".join(str(text or "").split())[:limit]


def _error_kind(error: BaseException) -> str:
    for expected, name in _EXPECTED_ERROR_KINDS:
        if isinstance(error, expected):
            return name
    return "unexpected_error"


def _translate(error: BaseException, *, query: str | None = None, hint_prefix: str | None = None) -> PopulationError:
    """Translate a provider failure into a bounded, coded PopulationError."""
    if isinstance(error, PopulationError):
        return error
    if isinstance(error, AuthorizationRequired):
        translated = PopulationError("population_auth_required", hint=_brief(getattr(error, "hint", None), 120) or None)
    else:
        match = _API_ERROR_RE.search(str(error))
        if match:
            digits = match.group(1)
            translated = PopulationError(
                f"api_{digits}",
                hint=POPULATION_HINTS.get(digits, "ошибка платформы"),
            )
        else:
            translated = PopulationError(_error_kind(error))
    if hint_prefix:
        translated.hint = f"{hint_prefix}: {translated.hint}"[:MAX_TEXT]
    translated.query = query
    return translated


def _capabilities_for(platform: str) -> ProviderCapabilities:
    """What the platform adapter can search; unknown platforms can nothing."""
    key = _normalise_platform(platform)
    if key == "vk":
        from app.services.collection.adapters.vk import VK_CAPABILITIES

        return VK_CAPABILITIES
    if key == "telegram":
        from app.services.collection.adapters.telegram import TELEGRAM_CAPABILITIES

        return TELEGRAM_CAPABILITIES
    return ProviderCapabilities()


def _requested_kinds(discovery: "DiscoveryIntent", platform: str) -> tuple[list[str], list[str]]:
    """(kinds to search, unsupported-kind warnings) for the declared kinds."""
    caps = _capabilities_for(platform)
    requested: list[str] = []
    warnings: list[str] = []
    if discovery.communities:
        if caps.community_search:
            requested.append(KIND_COMMUNITY)
        else:
            warnings.append(f"community_search_unsupported:{platform}")
    if discovery.people:
        if caps.people_search:
            requested.append(KIND_PERSON)
        else:
            warnings.append(f"people_search_unsupported:{platform}")
    return requested, warnings


async def _resolve_city_id(
    client: Any,
    discovery: "DiscoveryIntent",
    *,
    owner_user_id: int | None,
) -> tuple[int | None, str | None]:
    """The city id the search filters by: declared, else resolved by name.

    A declared id is used as-is. A bare name is resolved through the provider
    once; a provider refusal keeps its api code (the method, not the name, is
    what failed) and an empty answer is `population_city_unknown`. Either way
    the step fails closed: searching without the declared city would gather a
    population the task never asked for.
    """
    city = discovery.city
    if city is None:
        return None, None
    if city.id is not None:
        return int(city.id), city.name
    try:
        cities = await client.get_cities(city.name, owner_user_id=owner_user_id, count=5)
    except Exception as error:
        raise _translate(error, hint_prefix="город не удалось определить") from error
    best = cities[0] if cities else None
    if not isinstance(best, dict) or best.get("id") is None:
        raise PopulationError("population_city_unknown", hint=f"город «{_brief(city.name, 60)}» не найден")
    return int(best["id"]), city.name


async def _search_kind(
    client: Any,
    kind: str,
    query: str,
    *,
    owner_user_id: int | None,
    city_id: int | None,
    count: int,
) -> list[dict]:
    """One provider page for one phrase and kind; errors stay named."""
    try:
        if kind == KIND_COMMUNITY:
            rows = await client.search_groups(query, owner_user_id=owner_user_id, city_id=city_id, count=count)
        else:
            rows = await client.search_users(query, owner_user_id=owner_user_id, city_id=city_id, count=count)
    except AuthorizationRequired as error:
        raise _translate(error, query=query) from error
    except Exception as error:
        raise _translate(error, query=query) from error
    return [row for row in (rows or []) if isinstance(row, dict)]


def _entities_from_rows(rows: list[dict], kind: str) -> list[PopulationEntity]:
    entities: list[PopulationEntity] = []
    for row in rows:
        raw = row.get("external_id") or row.get("id")
        digits = str(raw or "").strip().lstrip("-")
        if not digits.isdigit() or int(digits) <= 0:
            continue
        number = int(digits)
        number = -number if kind == KIND_COMMUNITY else number
        entities.append(
            PopulationEntity(
                kind=kind,
                external_id=str(number),
                numeric_id=number,
                name=_brief(row.get("name"), 150) or None,
                screen_name=_brief(row.get("screen_name"), 100) or None,
            )
        )
    return entities


def _combine(per_phrase: list[list[PopulationEntity]], *, mode: str) -> list[PopulationEntity]:
    """`any` = the union of the per-phrase results, `all` = the intersection."""
    if not per_phrase:
        return []
    if mode == MATCH_ALL:
        surviving = {entity.identity for entity in per_phrase[0]}
        for other in per_phrase[1:]:
            surviving &= {entity.identity for entity in other}
            if not surviving:
                break
        return [entity for entity in per_phrase[0] if entity.identity in surviving]
    if mode != MATCH_ANY:
        # The parser only lets `any`/`all` through; anything else is a contract
        # break and must not silently widen into "all results".
        raise PopulationError("population_queries_match_invalid")
    seen: set[tuple[str, int]] = set()
    merged: list[PopulationEntity] = []
    for entities in per_phrase:
        for entity in entities:
            if entity.identity in seen:
                continue
            seen.add(entity.identity)
            merged.append(entity)
    return merged


def _target(entity: PopulationEntity) -> ResolvedTarget:
    """The lane-import view of a found entity — the import rules stay shared."""
    return ResolvedTarget(
        raw=entity.external_id,
        kind=entity.kind,
        external_id=entity.external_id,
        numeric_id=entity.numeric_id,
        screen_name=entity.screen_name,
        name=entity.name or entity.external_id,
    )


def _collect_warnings(*groups: list[str]) -> tuple[str, ...]:
    warnings: list[str] = []
    for group in groups:
        for text in group:
            bounded = _brief(text)
            if bounded and bounded not in warnings and len(warnings) < MAX_WARNINGS:
                warnings.append(bounded)
    return tuple(warnings)


async def resolve_population(
    *,
    task: Any,
    intent: CollectionIntent,
    platform: str,
    dry_run: bool,
    job_id: int | None,
    owner_user_id: int | None = None,
) -> PopulationResult:
    """Find the task's declared population and turn it into task-owned lanes.

    A task declaring no discovery queries (or neither kind) is a no-op: no
    provider call, nothing written. Otherwise every declared phrase is one
    provider call per declared kind; the per-phrase results combine by the
    intent's `queries_match`; every found entity goes through the shared
    lane-import rules. In a dry run nothing is written and the result reports
    what WOULD be created. A blocked or failed search raises
    :class:`PopulationError` with a machine-readable code — never an empty
    "nothing found" answer.
    """
    discovery = intent.discovery
    platform_key = _normalise_platform(platform)
    if not discovery.queries or not (discovery.communities or discovery.people):
        return PopulationResult(platform=platform_key, dry_run=dry_run, ran=False)

    requested, _unsupported = _requested_kinds(discovery, platform_key)
    if not requested:
        raise PopulationError(
            "population_search_unsupported",
            hint=f"платформа «{_brief(platform_key, 30)}» не умеет искать заявленные типы",
        )

    from app.models import Platform

    platform_row = await Platform.objects.filter(platform_type=platform_key).first()
    if platform_row is None:
        raise PopulationError(
            "population_search_unsupported",
            hint=f"платформа «{_brief(platform_key, 30)}» не настроена",
        )

    from app.services.social.factory import get_social_client
    from app.services.social.owner import resolve_source_owner

    client = get_social_client(platform_row)
    # New explicit plans pass their trusted initiator/payer. Validate that
    # identity through the same workspace membership gate used by source
    # collection. Legacy calls retain the workspace-owner fallback.
    owner_source = SimpleNamespace(
        tenant_id=int(task.tenant_id),
        params={"token_owner": owner_user_id} if owner_user_id is not None else {},
    )
    resolved_owner = await resolve_source_owner(owner_source)
    if owner_user_id is not None and resolved_owner != owner_user_id:
        raise PopulationError(
            "population_connection_owner_not_workspace_member",
            hint="Личный аккаунт запуска больше не состоит в рабочем пространстве.",
        )
    owner_user_id = resolved_owner

    city_id, city_name = await _resolve_city_id(client, discovery, owner_user_id=owner_user_id)

    # One call per discovery phrase and kind, bounded by the task's own
    # candidate limit — the population step is a page, not an infinite crawl.
    count = max(1, min(int(intent.candidates.limit), MAX_SEARCH_PAGE))
    per_phrase: list[PopulationPhrase] = []
    per_phrase_entities: list[list[PopulationEntity]] = []
    for query in discovery.queries:
        found: list[PopulationEntity] = []
        communities = people = 0
        for kind in requested:
            entities = _entities_from_rows(
                await _search_kind(
                    client,
                    kind,
                    query,
                    owner_user_id=owner_user_id,
                    city_id=city_id,
                    count=count,
                ),
                kind,
            )
            found.extend(entities)
            if kind == KIND_COMMUNITY:
                communities += len(entities)
            else:
                people += len(entities)
        per_phrase_entities.append(found)
        per_phrase.append(PopulationPhrase(query=query, communities=communities, people=people))

    merged = _combine(per_phrase_entities, mode=discovery.queries_match)
    if not merged:
        # An honest empty search: nothing found and nothing to import.
        return PopulationResult(
            platform=platform_key,
            dry_run=dry_run,
            ran=True,
            city_name=city_name,
            city_id=city_id,
            queries=tuple(discovery.queries),
            per_phrase=tuple(per_phrase),
            warnings=_collect_warnings(_unsupported),
        )

    from app.models import Source

    workspace_sources = list(
        await Source.objects.filter(
            tenant_id=int(task.tenant_id),
            platform_id=int(platform_row.id),
        ).select_related("platform")
    )
    plan = plan_lane_import(
        task,
        [_target(entity) for entity in merged],
        platform=platform_key,
        workspace_sources=workspace_sources,
    )
    plan_skips = [
        f"{_brief(decision.raw)}: {decision.reason}"
        for decision in plan.decisions
        if decision.action == SKIP and decision.reason
    ]
    warnings = _collect_warnings(_unsupported, plan_skips)
    would_create = tuple(entity for entity, decision in zip(merged, plan.decisions) if decision.action == CREATE)

    if dry_run:
        # Nothing is written: only what a real run would create.
        return PopulationResult(
            platform=platform_key,
            dry_run=True,
            ran=True,
            city_name=city_name,
            city_id=city_id,
            queries=tuple(discovery.queries),
            entities=tuple(merged),
            per_phrase=tuple(per_phrase),
            warnings=warnings,
            would_create=would_create,
        )

    result = await apply_lane_import(plan)
    return PopulationResult(
        platform=platform_key,
        dry_run=False,
        ran=True,
        city_name=city_name,
        city_id=city_id,
        queries=tuple(discovery.queries),
        entities=tuple(merged),
        per_phrase=tuple(per_phrase),
        created_source_ids=tuple(result.created_source_ids),
        reused_source_ids=tuple(result.reused_source_ids),
        linked_source_ids=tuple(result.linked_source_ids),
        refused=tuple(_brief(text) for text in result.refused[:MAX_WARNINGS]),
        warnings=warnings,
    )
