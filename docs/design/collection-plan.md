# Collection plan: task-owned typed collection

Status: design accepted by the owner on 2026-10-21; implementation is tracked
by [P5-T14](../phases/p5-ai-boundaries.md). Stage A delivered the payload
parser, schema and migration 0108. Stage B delivered the `Page[T]` contract,
planner, VK/Telegram adapters and no-DB tests. Stage C delivered collection-time
selection/staging, `seen`/lane evidence and thread-level selection. Collection
lanes are not wired to runtime yet; comments/engagement, UI and task-owned
content types remain future stages. Until P5-T14 is `READY`/`DONE`, runtime
behavior in [COLLECTION](../COLLECTION.md), [task selection](task-selection.md)
and [MODELS](../MODELS.md) still applies except for the stage-C selection path
described there. Stage B implementation plan:
[collection plan — stage B](collection-plan-stage-b.md).

## Problem

Collection currently mixes three different decisions:

- a source decides **where** we read (a wall or channel);
- a search source decides **what words** we search, but is stored and presented
  as an ordinary source;
- a scenario decides **which content types** are analysed, although the
  collector always collects posts.

The result is hard to explain: the task page shows sources, the phrase hides in
a source's name/settings, `content_types` is too late to influence collection,
and there is no place for a city, candidate discovery, comments, likes or an
explicit collection limit.

The goal is one task declaration that answers: **what to collect, where, with
which filters, and how much** — with several platform API calls under the hood,
normalized results, and explicit truncation.

## Decisions

1. **Content types belong to the task.** The task decides which objects to
   collect (`posts`, `comments` in v1) and which engagement objects to inspect
   (`likes`, `reposts`). A scenario keeps methodology: prompt,
   `analysis_types`, `output_schema` and model choices by media type. Mentions
   are post-search lanes, not engagement objects; videos/stories/reels are
   media variants of a post until a separate adapter primitive exists.
2. **Selection runs at collection.** Deterministic task selection (author,
   text scope) is applied before staging, so `collected_items` contains what
   analysis will actually see. Rejected text is not stored; counts and reasons
   live in the lane run.
3. **A task is a plan of lanes.** A lane is one typed operation with a
   dependency, limit and cursor. Multiple lanes run under one task; results are
   deduplicated and reported per lane.
4. **Discovered people/communities are candidates, not Sources.** A candidate
   is confirmed (manually by default, capped optionally) before it becomes a
   source. This resolves the old Q-3 decision.
5. **Query lanes are task-scoped Sources.** A saved query needs a stable
   collection cursor and a `source_id` for staged rows/analytics. It is stored
   as a `Source` with `owner_task_id`, but presented on the task as "Поиск по
   словам", never in the global source list, and is excluded from the
   empty-set/"all active sources" fallback.
6. **Limits and truncation are first-class.** Every lane returns `limit`,
   `limit_reached`, `next_cursor` and, when known, `total_available`. The user
   is told when a limit was reached; a silent cut is a defect.
7. **City is an attribute with confidence.** VK post search cannot filter by
   city. City comes from profiles/communities (or text), carries a source and
   confidence, and unknown values go to `needs_review` rather than being
   silently dropped.
8. **Platform parity is a contract, not a promise.** VK and Telegram implement
   the same primitives where the platform allows it; unsupported operations
   return an explicit reason.

## Task contract

`agent_tasks.payload["collection"]` holds intent. Sources stay in
`agent_task_sources`; they are not duplicated in the payload.

```json
{
  "collection": {
    "version": 1,
    "content_types": ["posts", "comments"],
    "engagement_types": ["likes"],
    "limit": 1000,
    "date_window": {"start": "2026-10-01", "end": null},
    "queries": ["Древиз", "Вревиз", "Кабанов", "Италия поездка"],
    "city": {"name": "Киров", "mode": "prefer"},
    "discovery": {
      "people": true,
      "communities": true,
      "queries": ["дизайн интерьера"]
    },
    "comments": {"max_per_post": 100, "total": 2000},
    "candidates": {"limit": 100, "confirm": "manual"}
  }
}
```

- `content_types` drives collection lanes and the stored `content_type`. v1
  supports `posts` and `comments`; a type without an implemented lane is
  rejected at save or reported `unsupported`, never silently ignored.
- `engagement_types` produces candidates (who liked/reposted), not text items.
- `queries` feeds mention lanes; a query is a saved task-scoped Source with an
  auto-generated name/slug.
- `date_window` is mandatory for collect/analyze, as before.
- `limit` defaults to 1000 and is capped by `SOCIAL_COLLECT_HARD_LIMIT`.
- `city.mode` is `prefer` (rank, keep unknown for review), `require` (strict)
  or `review` (unknown only in review).
- `discovery` is independent of `queries`: it finds people/communities by
  topic and city; mention lanes find posts by words.

The payload stores intent, not the resolved lane JSON. `CollectionPlanner`
derives lanes deterministically; `collection_lane_runs` records the plan that
actually ran. This prevents task settings and executed lanes from drifting.

## Data model

### `collected_items`

Only `selected` and `needs_review` rows are written. Add:

| Field | Meaning |
| --- | --- |
| `content_type` | `post` / `comment` (default `post` for migration) |
| `parent_external_id` | parent post id for a comment, otherwise NULL |
| `lane_id` | which lane produced the row |
| `selection_status` | `selected` / `needs_review` |
| `selection_reason` | e.g. `text_matched`, `city_unknown`, `city_matched` |

Unique key becomes `(source_id, content_type, external_id)`. A comment's
`external_id` is composed as `post_id:comment_id` so it cannot collide with a
post id. `source_id` stays non-null: a query lane owns a task-scoped Source.

Rejected rows are not stored. `collection_lane_runs` keeps their count and
reason; bounded previews may appear in the job result for diagnostics. Analysis
reads only `selection_status = 'selected'`; `needs_review` is an operator-facing
holding state and is not sent to a paid model until confirmed.

### `collection_seen`

A compact dedupe/new-item ledger so a rejected item does not reappear as "new"
on every run:

`tenant_id`, `source_id`, `content_type`, `external_id`, `content_hash`,
`first_seen_at`, `last_seen_at`, `status`. Unique
(`tenant_id`, `source_id`, `content_type`, `external_id`). It prevents
duplicate rows and inflated "new" counts; it is not a permanent rejection. If
the task's filters change, the item is re-evaluated and its status updated.

### `collection_candidates`

Discovered people/communities and engagement actors:

`platform`, `kind` (`person` / `community`), `external_id`, `name`, `url`,
`city`, `city_source`, `city_confidence`, `metrics`, `evidence`, `status`
(`new` / `confirmed` / `rejected`), `source_id` after confirmation,
`discovered_by` (lane/query).

Unique `(tenant_id, platform, kind, external_id)`. Confirmation links or creates
a Source; automatic confirmation is capped and off by default.

### `collection_lane_runs`

Per-job, per-lane evidence:

`job_id`, `task_id`, `lane_id`, `lane_type`, `query`, `fetched`, `selected`,
`needs_review`, `rejected`, `limit_count`, `limit_reached`, `next_cursor`,
`total_available`, `error_code`, `unsupported_reason`, timing. `lane_type` and
`limit_count` are the SQL-safe column names for the public `type`/`limit`;
API/UI can expose the shorter names.

This is the operator answer to "what did this run actually do?".

### `sources.owner_task_id`

Nullable FK to `agent_tasks`. Set only for task-scoped query lanes. Such a row
is hidden from the global source list/detail creation flow, excluded from the
"empty set means all active" fallback, and deleted with its task. Only the
owning task may link it; a cross-task link is rejected. Known walls and channels
keep `owner_task_id = NULL`.

## Lane types (v1)

| Lane | Depends on | Output |
| --- | --- | --- |
| `wall_posts` | — | posts |
| `post_search` | — | posts |
| `candidate_search` | — | candidates |
| `comments` | posts/thread | comments |
| `likes` | posts | candidates |
| `reposts` | posts | candidates |
| `profile_enrichment` | candidates/authors | attributes (city, description, url) |

Each lane has `platform`, `type`, `params`, `depends_on`, `limit`, `cursor`,
`filters` and `output_type`. v1 implements posts and comments; videos, stories
and reels remain media variants of a post until a dedicated adapter primitive
exists. They must not be silently mapped to `post_search`.

## Adapter contract

```python
class CollectionProvider(Protocol):
    platform: str
    capabilities: ProviderCapabilities

    async def resolve_city(self, name: str) -> CityRef | None: ...
    async def search_people(self, query, city, limit, cursor) -> Page[Candidate]: ...
    async def search_communities(self, query, city, limit, cursor) -> Page[Candidate]: ...
    async def search_posts(self, query, since, until, limit, cursor, owner_ids=None) -> Page[Item]: ...
    async def fetch_wall(self, source, since, until, limit, cursor) -> Page[Item]: ...
    async def fetch_comments(self, post, limit, cursor) -> Page[Item]: ...
    async def fetch_likes(self, post, limit, cursor) -> Page[Candidate]: ...
    async def fetch_profiles(self, ids, fields) -> list[Profile]: ...
```

`Page[T]` carries `items`, `next_cursor`, `limit_reached`, `total_available`
and `warnings`. A provider never decides selection or analysis.

Capabilities:

| Capability | VK | Telegram |
| --- | --- | --- |
| City lookup | yes | no |
| People/community search | yes (1051 until credentials allow) | limited |
| Global post search | yes (1051 until credentials allow) | no |
| Wall/channel posts | yes | yes |
| Comments | yes | yes |
| Likes/reposts | yes | limited |
| Profile enrichment | yes | limited |

An unsupported lane returns `unsupported` with a bounded reason, never an empty
page that looks like "nothing found".

## Orchestration and filters

`CollectionPlanner`:

1. validates the task intent against provider capabilities;
2. expands stages into lanes and topologically sorts by dependency;
3. enforces per-lane and total budgets;
4. deduplicates items by `(platform, external_id)` and candidates by
   `(platform, kind, external_id)`;
5. applies filters at the correct stage;
6. writes `collection_lane_runs` and returns a summary.

Filter stages:

- **collection** — date, owner, cheap text checks;
- **thread** — post plus comments together. A post whose own text lacks the
  phrase but whose comments contain it must survive; filtering the post before
  fetching comments is a defect for a comment-enabled task;
- **author** — city, kind, metrics after enrichment;
- **manual** — `needs_review` for unknown city or ambiguous identity.

The scenario is not a filter. It analyses rows that already passed selection.

## Limits and pagination

- `collection.limit` defaults to 1000; per-lane override is allowed below the
  hard ceiling.
- `comments.max_per_post` and `comments.total` bound comment expansion.
- `candidates.limit` bounds discovery; confirmation is capped.
- `total_items` is an overall guard.

`newsfeed.search` pages by `offset` but VK serves at most 1000 results; beyond
that, split by date, query or owner. A cursor is stored per lane
(`query + owner + date + last_id`) for recurring tasks; one-off tasks make a
single pass. Offset across days is unstable, so dedupe is mandatory.

Every result reports `fetched`, `selected`, `needs_review`, `rejected`, `limit`,
`limit_reached`, `next_cursor`, `total_available` and a bounded `error_code`.
The UI/API/CLI must surface `limit_reached` as: "Достигнут лимит N; выборка
может быть неполной."

## Scenario boundary

- The task owns collection scope (`content_types`, engagement types, queries,
  city, dates, limits).
- The scenario owns analysis methodology (`base_prompt`, `analysis_types`,
  `output_schema`, model choices by media type).
- `AgentScenario.content_types` stops driving collection. It may remain as a
  `supported_content_types` hint for the wizard/validation, or be removed in a
  follow-up. Existing scenarios keep their values but no runtime collection
  decision reads them.
- The analyzer receives `content_type` from the item and may use
  `{content_type}` in the prompt. A scenario may declare supported media types;
  it must not decide what is collected.

## UI, admin and presets

Task editor:

- **Кого искать** — city, discovery query, people/communities, candidate cap.
- **Что искать** — mention queries, date window, content types, engagement
  types, limit.
- **Где читать** — known walls/channels; task-scoped query lanes are shown as
  search phrases, not as sources.
- **Что делать с кандидатами** — manual confirm or capped automatic confirm.

Source picker groups walls/channels and hides task-scoped query sources. The
source detail for a task-scoped query shows the phrase, platform, optional
owner scope and limit, not a generic "name".

Admin and templates: forms/help for the new payload, `owner_task_id`,
`collected_items` fields, lane runs and candidates. Scenario presets, the
wizard and detail pages lose the "collection scope" role but keep
prompts/schemas/media models.

CLI/API/chat tools must produce the same payload as the web form; no surface
owns a divergent schema.

## Migration

One Alembic revision (after the current head):

- `collected_items`: new fields, backfill `content_type='post'`, replace the
  unique index;
- `sources.owner_task_id` and index;
- `collection_seen`, `collection_candidates`, `collection_lane_runs`;
- no destructive rewrite of `agent_tasks.payload`; existing tasks receive
  `collection.content_types=["posts"]` on read/default and keep current
  behavior.

Data migration for task 2027:

- create task-scoped query lanes for `Древиз`, `Вревиз`, `Кабанов`,
  `Италия поездка`;
- `content_types=["posts","comments"]`, limit 1000, date from 2026-10-01,
  city `Киров` in `prefer` mode;
- deactivate/delete source 3174 after lane parity;
- fix scenario 1695's `output_schema` (the prompt currently asks for fields the
  generated schema does not contain); keep `topic_hint` if chains are retained;
- run a live dry-run only after Q-5 unblocks VK search methods.

## Acceptance

- One task with two query lanes and two walls collects both; a post found by
  both is stored once.
- A lane limit of 5 returns 5 rows and `limit_reached=true`; the warning is
  visible in web/API/CLI.
- `collected_items` contains only `selected`/`needs_review`, with the correct
  `content_type`; a comment carries `parent_external_id`.
- A post without the phrase but with a matching comment is selected at thread
  level.
- Discovery with a city returns candidates; unknown city goes to
  `needs_review`; confirmation is capped.
- `content_types` on the task drives collection; the scenario field does not.
- Source 3174 is gone and task 2027 runs through lanes.
- Telegram unsupported lanes return a named reason; known channels still work.
- Dry run writes no rows, calls no model, does not advance cursors and reports
  counts.
- Tenant scoping, permissions and secret hygiene are unchanged.
- Focused tests pass via the primary OpenCode test-runner; shared test DB runs
  are sequential.

## Non-goals

- A general visual query builder or arbitrary graph.
- Scraping or bypassing platform privacy/permissions.
- Global post-search parity on Telegram.
- Collecting private data beyond what the official API and the authorized
  account can access.

## Open decisions

1. `source_id` for query lanes: task-scoped `Source` (recommended, keeps
   `collected_items`/analytics stable) versus nullable `source_id`.
2. Store only `selected`/`needs_review` (recommended) versus raw rows with a
   selected flag. The first needs a re-fetch to change filters; the second
   keeps raw text longer.
3. Likes: lists of people (`likes.getList`, recommended for lead discovery)
   versus only counts (already in metrics).
4. City: `prefer` + review (recommended) versus strict `require`.
5. Default comment cap per post (proposed 100).
