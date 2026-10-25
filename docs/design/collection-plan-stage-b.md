# Collection plan — stage B implementation handoff

Status: stage B implementation is complete and committed as `c6bd826`; 49
no-DB tests passed through the primary OpenCode test-runner at that commit.
Stage A is `e8e8693`. Parent task: [P5-T14](../tasks/p5-ai-boundaries.md).
Target contract: [collection plan](collection-plan.md).

## Current state (verify before work)

- Branch `dev`; Stage A was verified at HEAD `6f9a130`.
- `app/services/collection/intent.py` parses `agent_tasks.payload["collection"]`;
  a missing block means `content_types=("posts",)`.
- Migration `0108` added `collected_items.content_type`,
  `parent_external_id`, `lane_id`, `selection_status`, `selection_reason`,
  `sources.owner_task_id`, `collection_seen`, `collection_candidates`,
  `collection_lane_runs`.
- `store_items` and `admit_items` already use the conflict key
  `(source_id, content_type, external_id)`.
- The shared `test_schema` is reconciled with 0108; backup schema
  `test_schema_backup_0108_20261021` exists. The working `public` schema is
  **not** migrated — do not run the runtime against it.
- Runtime collection still uses the old path. Stage B adds adapters and the
  planner; it does **not** wire them into `handle_collect`/`collector` yet.

## Goal

Turn one task intent into a deterministic, typed, bounded plan of lanes, and
provide provider primitives that return normalised pages. Live VK calls are
blocked by Q-5 (`1051` until credentials permit search methods), so stage B is
implemented and tested with mocks.

## File ownership (stay inside this lane)

New/owned:

- `app/services/collection/pages.py` — `Page[T]`, `CollectionCursor`,
  `ProviderCapabilities`, provider errors.
- `app/services/collection/planner.py` — `CollectionLane`, `CollectionPlan`,
  `CollectionPlanner`, query-source spec.
- `app/services/collection/adapters/vk.py` — VK implementation.
- `app/services/collection/adapters/telegram.py` — capability/unsupported
  implementation.
- `app/services/collection/adapters/base.py` — provider protocol.
- `tests/test_collection_planner.py`
- `tests/test_vk_collection_adapter.py`
- `tests/test_collection_pages.py`
- `tests/test_telegram_collection_adapter.py`
- `docs/design/collection-plan-stage-b.md` (this file; update evidence only)
- `docs/tasks/p5-ai-boundaries.md` (Stage B evidence only)

Reuse without changing behaviour:

- `app/services/social/vk_client.py` — existing `search_groups`,
  `search_users`, `get_cities`, `_search_response`, `_build_params`,
  `_normalize_response`, `collect_data`. Add page-oriented methods here only if
  reuse is not possible; preserve tabs/legacy style.
- `app/services/social/base.py` — `BaseClient`, existing limit/date logic.
- `app/models/collection/*` — schema from Stage A.

Do **not** edit in stage B:

- `app/services/monitoring/selection.py` (stage C runtime, P5-T08 semantics);
- `app/jobs/handlers.py`, `app/services/monitoring/collector.py` (runtime
  wiring is stage C/D; no P5-T05 ownership remains);
- `app/services/ai/scenario*`, admin/web templates (stage E);
- `AgentScenario.content_types` semantics;
- migration files or the working `public` schema.

## Provider contract

`app/services/collection/pages.py`:

```python
@dataclass(frozen=True)
class Page(Generic[T]):
    items: tuple[T, ...] = ()
    next_cursor: str | None = None
    limit_reached: bool = False
    total_available: int | None = None
    warnings: tuple[str, ...] = ()
    unsupported_reason: str | None = None
```

Rules:

- `limit_reached=True` means the lane returned its bound and more may exist.
- An unsupported lane returns `unsupported_reason`, never a normal empty page.
- A cursor is opaque to callers; the adapter interprets it. Stage B uses a
  plain offset string for VK; do not trust offsets across days without dedupe.
- `Page` is immutable; no ORM objects inside. Normalise to plain dicts with
  `external_id`, `text`, `published_at`, `author`, `metrics`, `permalink`,
  `content_type`, `parent_external_id` (comments only).

`app/services/collection/adapters/base.py`:

```python
class CollectionProvider(Protocol):
    platform: str
    capabilities: ProviderCapabilities

    async def resolve_city(self, name: str) -> CityRef | None: ...
    async def search_people(self, query: str, city: CityRef | None, limit: int, cursor: str | None) -> Page[Candidate]: ...
    async def search_communities(self, query: str, city: CityRef | None, limit: int, cursor: str | None) -> Page[Candidate]: ...
    async def search_posts(self, query: str, since, until, limit: int, cursor: str | None, owner_ids=None) -> Page[Item]: ...
    async def fetch_wall(self, source, since, until, limit: int, cursor: str | None) -> Page[Item]: ...
    async def fetch_comments(self, post, limit: int, cursor: str | None) -> Page[Item]: ...
    async def fetch_likes(self, post, limit: int, cursor: str | None) -> Page[Candidate]: ...
    async def fetch_reposts(self, post, limit: int, cursor: str | None) -> Page[Candidate]: ...
    async def fetch_profiles(self, ids: Sequence[str]) -> list[Profile]: ...
```

`ProviderCapabilities` is a frozen dataclass of booleans:
`city_lookup`, `people_search`, `community_search`, `post_search`, `wall`,
`comments`, `likes`, `reposts`, `profiles`.

## VK adapter

Use the existing `VKClient` and its token/layer rules; do not invent a second
HTTP path or bypass `resolve_token`.

| Method | VK API | Notes |
| --- | --- | --- |
| `resolve_city` | `database.getCities` | Existing `get_cities`; service token first, user token fallback. |
| `search_people` | `users.search` | Existing `search_users`; city via `city` id; `fields` already set. |
| `search_communities` | `groups.search` | Existing `search_groups`; city id/country id. |
| `search_posts` | `newsfeed.search` | `q`, `count`, `offset`, `start_time`, `end_time`, `extended=1`; owner filter is client-side in `_normalize_response`. |
| `fetch_wall` | `wall.get` | Existing `_fetch_page`/`_normalize_response`; equivalent wall params; the date window is applied client-side because VK ignores `start_time`/`end_time` for `wall.get`. |
| `fetch_comments` | `wall.getComments` | **New:** pass `owner_id` and `post_id`; `extended=1`; normalise comment + author; `content_type="comment"`, `parent_external_id=<post id>`, composite `external_id`. |
| `fetch_likes` | `likes.getList` | `type=post`, `owner_id`, `item_id`, `count`, `offset`, `extended=1`, `fields`; map to `Candidate(kind="person")`. |
| `fetch_reposts` | `wall.getReposts` | If unavailable for a profile, return `unsupported_reason`; do not fake empty. |
| `fetch_profiles` | `users.get`/`groups.getById` | For author city/enrichment; batch ids. |

Implementation notes:

- Add public page methods to `VKClient` or a thin `VKCollectionAdapter` that
  wraps the existing public methods. Do not duplicate token resolution.
- `search_posts` should reuse `_search_response` and `_normalize_response`;
  reset `_profile_names`/`_group_names` per call so extended maps do not leak.
- VK `newsfeed.search` serves at most 1000 by `offset`; `next_cursor` must stop
  at 1000 or split by date/query. Return `limit_reached=True` when the bound is
  hit.
- Map VK errors to bounded codes (`credentials`, `access_denied`,
  `rate_limited`, `unsupported`, `provider_error`). `1051` becomes an explicit
  credentials/unsupported warning, never a silent empty page.
- Dry-run is a planner/executor concern, not an adapter side effect. The
  adapter never writes DB rows.

## Telegram adapter

- Implement `ProviderCapabilities` honestly: no global post search, no city
  lookup; channel/chat wall and comments only where the existing Telegram path
  already supports them.
- Methods not supported return
  `Page(unsupported_reason="telegram_<lane>_unsupported")`.
- Do not add a second Telegram client; reuse the existing factory/client.
- Stage B only needs the capability matrix and explicit unsupported pages; do
  not wire actual Telegram collection.

## Planner contract

`app/services/collection/planner.py`:

```python
@dataclass(frozen=True)
class CollectionLane:
    lane_id: str
    lane_type: str          # wall_posts | post_search | candidate_search |
                            # comments | likes | reposts | profile_enrichment
    platform: str
    output_type: str        # items | candidates | profiles
    limit: int | None = None
    depends_on: tuple[str, ...] = ()
    source_id: int | None = None
    query: str | None = None
    params: Mapping[str, Any] = field(default_factory=dict)

@dataclass(frozen=True)
class CollectionPlan:
    task_id: int | None
    tenant_id: int
    lanes: tuple[CollectionLane, ...]
    query_sources: tuple[QuerySourceSpec, ...] = ()
    total_limit: int = 0
    warnings: tuple[str, ...] = ()
```

`plan_collection(task, intent, sources, provider_capabilities)`:

1. Validate intent (already parsed) and provider capabilities.
2. Add one `wall_posts` lane per known source; a `SEARCH` source becomes
   `post_search` with its phrase.
3. Add one `post_search` lane per `intent.queries`; create/reuse a task-scoped
   query source spec (no DB write in the planner).
4. Add `candidate_search` lanes for enabled discovery (`people`,
   `communities`) and their queries.
5. If `comments` is in `content_types`, add `comments` lanes depending on every
   post-producing lane. The executor fetches comments before thread selection;
   the planner only expresses the dependency.
6. Add `likes`/`reposts` lanes for `engagement_types`, depending on post lanes;
   output candidates, not text items.
7. Add `profile_enrichment` when a city/discovery decision needs it; depend on
   its producers.
8. Topologically sort; reject cycles with a bounded planner error.
9. Deduplicate lanes with the same type/query/source. Keep a stable lane id
   (`wall:<source_id>`, `search:<slug>`, `comments:<parent_lane>`). The pure
   `dedupe_items()`/`dedupe_candidates()` helpers define cross-lane identity:
   `(platform, content_type, external_id)` and
   `(platform, kind, external_id)`. Stage C persists the deduplicated set.
10. Enforce budgets: `intent.limit` for items; `comments.max_per_post` and
    `comments.total`; `candidates.limit`. A bound hit sets `limit_reached` on
    the lane result, never silently truncates.
11. Return `plan.warnings` for unsupported lanes; do not drop them silently.

Query-source spec:

- `source_type=SEARCH`, `external_id` unique per task/query (e.g.
  `task-<task_id>-<slug>`), `name` human-readable, `params={"query": query,
  "mode": "auto"}`, `owner_task_id=task.id`.
- The executor may create it only outside dry-run and only in the owning
  tenant. Stage B exposes `query_source_spec()` and tests uniqueness; actual
  persistence can wait for the executor/Stage C.
- Known walls remain ordinary Sources; never create a Source for a discovered
  candidate (candidates stay in `collection_candidates`).

Dry-run / no-write rule:

- Planning is pure.
- An adapter may fetch (or use a mock); it writes nothing.
- Do not create query sources, `collection_lane_runs`, `collection_seen`,
  `collection_candidates` or `collected_items` in stage B.
- Do not advance `Source.last_checked` or cursors.

## Tests (stage B)

No live VK/Telegram/LLM. Mock HTTP/`VKClient`/provider methods.

`tests/test_collection_pages.py`:

- `Page` defaults, immutability, unsupported-vs-empty distinction.
- `limit_reached` and cursor pass-through.

`tests/test_collection_planner.py`:

- Missing `collection` block: known sources only, posts only.
- Two walls + two queries: lanes, stable ids, topological order.
- Comments lane depends on each post lane; likes/reposts depend on posts.
- Discovery people/communities and profile enrichment order.
- Duplicate query/source dedupe.
- Cross-lane item/candidate identity: wall + search post dedupe; post vs
  comment remain distinct.
- Total/per-lane/comment/candidate budgets; `limit_reached` propagation.
- Telegram unsupported lanes produce warnings, not silent omission.
- Query source spec unique per task/query, `owner_task_id` set, never explains
  a discovered candidate as a Source.

`tests/test_vk_collection_adapter.py`:

- Mocked `database.getCities`, `users.search`, `groups.search`,
  `newsfeed.search`, `wall.get`, `wall.getComments`, `likes.getList`,
  `wall.getReposts`, `users.get`; assert exact params and page envelopes.
- `newsfeed.search` date/offset/1000 cap.
- Comment `external_id`/`parent_external_id`/`content_type`.
- Likes/reposts return candidates, not text items.
- `1051` produces an explicit credentials/unsupported warning; no fake empty
  page.
- No DB writes, no `last_checked` update, no model call.

`tests/test_telegram_collection_adapter.py`:

- Existing channel pull wraps into a page and reports its limit.
- People/community/post/comments/likes/reposts lanes return named unsupported
  reasons, never indistinguishable empty pages.

Test routing: primary OpenCode test-runner (`.agents/skills/test-runner/`);
shared DB only if a test needs it, sequential under R8. Stage B should be
mostly no-DB. Do not run a migration.

## Acceptance for stage B

- A two-wall + two-query task yields a deterministic ordered plan; one post
  found by wall and search has one identity.
- Comments/likes/reposts lanes depend on post lanes; likes/reposts output
  candidates.
- Every lane has a bound; hitting it reports `limit_reached`.
- Unsupported Telegram operations are explicit warnings, never empty results.
- Query-source spec is unique and task-owned; no candidate becomes a Source.
- No runtime handler/collector wiring, no DB writes in planner/adapters, no
  live provider calls, no LLM calls.
- Focused tests pass via the primary OpenCode test-runner.
- Phase file Stage B gets an evidence paragraph; the parent P5-T14 stays
  `IN_PROGRESS` until stages C–F are complete. Codex owns final acceptance.

## First actions in the new chat

1. `git rev-parse HEAD`, `git status --short`, `pgrep -fl pytest`; verify no
   conflicting active lane.
2. Read `AGENTS.md`, `docs/COLLABORATION.md`, `docs/COLLABORATION.md`,
   `docs/design/collection-plan.md`, this file, and P5-T14 in
   `docs/tasks/p5-ai-boundaries.md`.
3. Inspect `app/services/social/vk_client.py` search/cities methods and
   `app/services/collection/intent.py`; do not reread unrelated docs.
4. Claim stage B in the phase file/ROADMAP ownership if needed; keep P5-T14
   `IN_PROGRESS`.
5. Implement `pages.py` → `planner.py` → adapters → tests, in that order.
6. Run focused tests via the test-runner; update phase evidence; report to the
   owner/Codex. Do not apply migration 0108 to `public`.

## Known blockers and risks

- **Q-5:** VK search methods return `1051` for the current credentials. All
  live acceptance is deferred; mocks must cover params and error mapping.
- **Public schema:** not migrated. Do not start the runtime against `public`
  until the owner authorizes 0108 there.
- **Shared files:** Stage C owns selection/handler wiring; do not pre-empt it.
- **VK limits:** `newsfeed.search` caps at 1000 by offset; date/query splitting
  and dedupe are mandatory.
- **Task-scoped source uniqueness:** `sources` unique key does not include
  `owner_task_id`; generate a unique external id per task/query.
