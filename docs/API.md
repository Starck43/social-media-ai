# API Reference

FastAPI application exposes REST endpoints under `/api/v1` and the sqladmin
panel under `/admin`. Workspace data endpoints require an access Bearer JWT
and an active web membership. Token acquisition, registration and the signed-state
OAuth callback have the explicit exceptions listed below.

## Base URL

```
http://localhost:8000/api/v1
```

## Authentication

`ApiScopeMiddleware.PUBLIC_API_PATHS` exempts `/api/v1/auth/login`,
`/api/v1/auth/register`, `/api/v1/auth/refresh-token`, `/api/v1/health` and
`/api/v1/social/callback` from access-token/workspace middleware. Refresh still
requires a valid **refresh** Bearer token; OAuth callback validates signed state.
The root `/livez`, `/readyz` and `/health` probes are outside `/api/*`.

### Login

Get access + refresh token pair.

```
POST /api/v1/auth/login
Content-Type: application/x-www-form-urlencoded

username=<username_or_email>&password=<password>
```

**Response** `200 OK`:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "refresh_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_at": 1735689600
}
```

Failed attempts count into the shared login budget
([ADMIN](ADMIN.md#sessions-csrf-and-known-verification-limits)): an unknown
account, a wrong password, an inactive account and a locked budget all answer
`400` with the same generic detail.

### Refresh Token

Get a new access token using a refresh token.

```
POST /api/v1/auth/refresh-token
Authorization: Bearer <refresh_token>
```

**Response** `200 OK`:
```json
{
  "access_token": "eyJhbGciOiJIUzI1NiIs...",
  "token_type": "bearer",
  "expires_at": 1735689600
}
```

### Register

Create a new user.

```
POST /api/v1/auth/register
Content-Type: application/json

{
  "username": "johndoe",
  "email": "johndoe@example.com",
  "password": "strongpassword123"
}
```

**Response** `200 OK`: same token-pair shape as login. Only username, email
and password are accepted. Extra privilege/state keys (`role_id`, `is_superuser`,
`is_active`) are ignored; the server assigns active, non-superuser and the Viewer
platform role ([PR #165](https://github.com/Starck43/social-media-ai/pull/165)).
Registration does not create a workspace/web membership, unlike `/app/register`;
a new API-only account cannot use workspace data endpoints until a membership exists.

**Errors:**
- `400` — Username or email already registered.

---

## Authorization

Authentication answers *who* is calling; authorization answers *what they may
do*. They are separate layers, and every surface uses the same two:

| Surface | Authentication | Rights |
|---|---|---|
| `/api/*` | bearer JWT (`ApiScopeMiddleware`) | per-endpoint: `require_model_perm` (model rights) and `require_platform_role` (role ladder); most write endpoints and many read endpoints declare model rights |
| `/app/*` | web session (`TenantUIMiddleware`) | active membership plus effective model/action rights (`WebPerms`, `guard_web`); hidden controls do not replace server checks |
| `/admin/*` | admin backend | per-model rights, Django-style (`docs/ADMIN.md`) |
| CLI | none (developer tool) | runs with the tenant guard off; `--tenant` picks a workspace |

### Workspace selection

Workspace-data `/api/*` requests run inside one workspace, resolved from the
caller's `tenant_users` membership and never from the request body. Personal
account and the 0109 personal LLM catalog routes below are explicit exceptions:

- unauthenticated → `401`;
- `X-Tenant-Id` / `X-Tenant-Slug` *select* among the caller's own workspaces, so
  a workspace they are not a member of is `403`, never honoured;
- data access goes through `BaseManager`, so every SELECT is filtered to that
  workspace — a foreign id reads as "not found", not as a leak.

### Rights

For ordinary workspace-scoped models, a non-owner member uses the **active
membership role**, not the account platform role. Global models/admin and the
platform-role ladder use `users.role_id`; owner and operator overrides remain
explicit and bounded. Both role matrices read `role_permission` → `permissions`:

```python
require_model_perm("source", ActionType.CREATE)
```

means "this role may create sources", checked against
`permissions.model_type_id` + `action_type`; a permission's three-part codename
is derived from its stable resource key. Workspace calls use explicit membership
presets without platform fallback. Operator APIs (`/users` except `/users/me`,
`/users/roles`) require active platform operator/admin entry plus their
route-specific action checks. Personal `/users/me`, `/auth/change-password`,
`/llm/llm-providers` and `/llm/llm-models` (including numeric-ID subpaths) require
active authentication without workspace membership. In the 0109 candidate,
LLM self-service is owner-scoped, not gated by operator/admin entry. The namespace
is `/api/v1/llm/llm-*`, not `/api/v1/llm-*`; unrelated prefixes are not exempt.

`member_decision()` selects the workspace-role answer when applicable; otherwise
`User.has_perm_for()` supplies platform model/action rights. SQLAdmin is a global
platform console, not the active dashboard workspace. See
`docs/ADMIN.md` for the console's mapping of these rights onto buttons, and
`docs/TENANCY.md` for how the workspace itself is resolved.

### Permission matrix by endpoint

| Endpoint group | Model | Actions |
|---|---|---|
| `GET /users` | — | `require_platform_role(ADMIN)` |
| `PUT/DELETE /users/{id}`, `POST /users/{id}/change-password` | `user` | `UPDATE` / `DELETE` / `UPDATE` |
| `GET /users/me` | — | authenticated only |
| `GET/POST/PUT/DELETE /ai/scenarios` | `agentscenario` | `VIEW` / `CREATE` / `UPDATE` / `DELETE` |
| `GET/POST/DELETE /notifications`, `POST /notifications/{id}/mark-read`, `POST /notifications/mark-all-read`, `POST /notifications/cleanup` | `notification` | `VIEW` / `CREATE` / `DELETE` / `UPDATE` / `UPDATE` / `DELETE` |
| `POST /monitoring/collect/{source,platform}`, `GET /monitoring/analytics/source/{id}` | `source` | `UPDATE` / `VIEW` |
| `GET/POST/PATCH/DELETE /sources` | `source` | `VIEW` / `CREATE` / `UPDATE` / `DELETE` |
| `GET/POST/PATCH/DELETE /tasks`, `PATCH /tasks/{id}/pause`, `POST /tasks/{id,run,run}` | `agenttask` | `VIEW` / `CREATE` / `UPDATE` / `DELETE` / `UPDATE` / `UPDATE` / `UPDATE` |
| `GET/PATCH/POST/POST/GET /credentials`, `POST /credentials/login`, `POST /credentials/oauth` | `credential` | `VIEW` / `UPDATE` / `CONFIGURE` / `CONFIGURE` / `VIEW` |
| `GET list/detail, POST, PATCH, DELETE /llm/llm-providers` | `llmprovider` | Active-user personal self-service for these actions, owner-filtered (0109 candidate) |
| `GET list/detail, POST, PATCH, DELETE /llm/llm-models` | `llmmodel` | Active-user personal self-service for these actions, owner-filtered (0109 candidate) |
| Dashboard endpoints | `source` / `aianalytics` / `notification` | per-endpoint |

---

## Health

```
GET /api/v1/health
```

**Response** `200 OK`:
```json
{
  "status": "ok",
  "database": "connected"
}
```

No authentication required. This legacy probe bounds its ORM read to two seconds
and returns HTTP 500 with `{"detail": "Database connection error"}` for DB
errors and timeouts, never the internal exception or DSN. Cancellation propagates;
the timeout assumes a cooperative async driver, not a hard wall-clock deadline
for blocking or cancellation-suppressing code.

The hidden `GET /api/v1/test-db` probe keeps its Bearer authentication dependency
and successful `{"database": "connected", "permissions": <count>}` contract,
including zero counts. It uses the same bounded, generic HTTP 500 failure.
`/api/v1/test-auth` and all existing router auth dependencies are unchanged.

Do not use these legacy probes as the production readiness contract: root
`/readyz` and `/health` retain generic 200/503 responses with a two-second DB
probe; `/livez` never queries the DB. `app/core/health.py` is unchanged.
See [isolated verification and acceptance handoff](audit/legacy-api-db-probes.md)
for exact source revisions, commands and the remaining real-environment checks.

---

## Users

**Public response contract:** list, `GET/PUT /users/me`, profile update and
platform-role update handlers use the existing hash-free `UserInDB` response
schema. Profiles expose only `id`, `username`, `email`, `is_active`, `created_at`
and `updated_at`; password/hash fields and role/privilege attributes from the
returned object are not serialized or included in response OpenAPI schemas.
The response projection does not grant or revoke authority: `/me` keeps its
identity dependency, operator routes keep their existing model-action or
platform-admin guards, and manager authorization remains unchanged.

The runtime schema fix is already present in `eb6cad6`; the bounded
[HTTP/OpenAPI verification](audit/http-user-response-privacy.md) adds
isolated router tests with explicit identity/storage doubles. Owner integration
acceptance at PR source `987796aa` additionally exercised the full application
OpenAPI and real ASGI/JWT/middleware/ORM list, me and profile routes: 2 passed,
including hash-free responses and preserved authorization. See the
[attributed integration evidence](https://github.com/Starck43/social-media-ai/pull/227#issuecomment-6300278265).
The list request selected its synthetic record by the real skip/limit manager
page. Existing legacy rows with invalid email strings still make an unbounded
default page fail response validation; those rows are outside this response
privacy acceptance. Historical privilege-update notes below are not P10-T03
acceptance. Never copy real hashes into reports or logs.

### List Users

```
GET /api/v1/users?skip=0&limit=100
Authorization: Bearer <token>
```

**Query params:**
| Param | Type | Default | Max | Description |
|---|---|---|---|---|
| `skip` | int | 0 | — | Records to skip |
| `limit` | int | 100 | 100 | Max records |

**Response** `200 OK`:
```json
[
  {
    "id": 1,
    "username": "admin",
    "email": "admin@example.com",
    "is_active": true,
    "created_at": "2025-01-01T00:00:00",
    "updated_at": "2025-01-01T00:00:00"
  }
]
```

**Permissions:** `require_platform_role(ADMIN)`

### Get Current User

```
GET /api/v1/users/me
Authorization: Bearer <token>
```

**Response** `200 OK`: same shape as list items.

### Update User

```
PUT /api/v1/users/{user_id}
Authorization: Bearer <token>
Content-Type: application/json

{
  "username": "newname",
  "email": "new@example.com",
  "is_active": true
}
```

**Permissions:** `user.update`

The endpoint and `UserManager.update_user` permit another target only when
`current_user.is_superuser` is true; an ADMIN-role account without that flag is
limited to itself despite `user.update`. `UserUpdate` still accepts `is_active`
and `is_superuser`, and the self-update path forwards them without a separate
privilege-field gate. This is an open escalation finding for an account holding
`user.update`, not authority for an ordinary Viewer and not fixed by the
registration-only protection in PR #165.

### Delete User

```
DELETE /api/v1/users/{user_id}
Authorization: Bearer <token>
```

**Response** `204 No Content`.

**Permissions:** `user.delete`

### Change Password

```
POST /api/v1/users/{user_id}/change-password
Authorization: Bearer <token>
Content-Type: application/json

{
  "current_password": "oldPass123",
  "new_password": "newSecurePass123"
}
```

**Response** `200 OK`:
```json
{
  "detail": "Password updated successfully"
}
```

**Permissions:** `user.update`

**Validation:** new password must be ≥8 chars, contain uppercase, lowercase, digit.

---

## Roles & Permissions

### List Roles

```
GET /api/v1/users/roles/
Authorization: Bearer <token>
```

**Response** `200 OK` (paginated):
```json
{
  "items": [
    {
      "id": 1,
      "name": "admin",
      "permissions": [
        {"id": 1, "codename": "add_user", "name": "Can add user"}
      ]
    }
  ],
  "total": 5,
  "page": 1,
  "pages": 1
}
```

### Get Role

```
GET /api/v1/users/roles/{role_name}
Authorization: Bearer <token>
```

### Update Role Permissions

```
PUT /api/v1/users/roles/{role_name}/permissions
Authorization: Bearer <token>
Content-Type: application/json

{
  "permissions": ["add_user", "change_user", "delete_user"],
  "strategy": "replace"
}
```

**Strategies:**
| Strategy | Behavior |
|---|---|
| `replace` | Replace all permissions with the new list |
| `merge` | Add new permissions, keep existing |
| `synchronize` | Add new, remove those not in the list |
| `update_actions` | Update actions for the same tables |

**Response** `200 OK`:
```json
{
  "message": "Permissions updated successfully",
  "role": { ... },
  "changes": {
    "added": ["add_user"],
    "removed": [],
    "updated": [],
    "unchanged": ["change_user"]
  }
}
```

---

## Monitoring

All monitoring endpoints require authentication and run collection in
background tasks.

### Collect from Source

```
POST /api/v1/monitoring/collect/source
Authorization: Bearer <token>
Content-Type: application/json

{
  "source_id": 1,
  "content_type": "posts",
  "analyze": true
}
```

**Response** `200 OK`:
```json
{
  "status": "started",
  "source_id": 1,
  "message": "Content collection started in background"
}
```

**Permissions:** `source.update`

### Collect from Platform

```
POST /api/v1/monitoring/collect/platform
Authorization: Bearer <token>
Content-Type: application/json

{
  "platform_id": 1,
  "source_types": ["GROUP", "CHANNEL"],
  "analyze": true
}
```

**Permissions:** `source.update`

### Collect Monitored Users — removed

`POST /api/v1/monitoring/collect/monitored` no longer exists. It collected
"monitored users" of a source, replacing that source's own wall and requiring a
separate `USER` source per person. A task's `tracked_users` selection over the
task's sources replaces it ([task selection](COLLECTION.md#task-selection-contract)); author
selection is not a collection endpoint.

### Get Source Analytics

```
GET /api/v1/monitoring/analytics/source/{source_id}
Authorization: Bearer <token>
```

**Permissions:** `source.view`

**Response** `200 OK`:
```json
{
  "source_id": 1,
  "source_name": "My VK Group",
  "analytics": [
    {
      "id": 42,
      "analysis_date": "2025-10-15",
      "period_type": "DAY",
      "topic_chain_id": "chain-abc",
      "llm_model": "gpt-4o-mini",
      "summary_data": { "sentiment_score": 0.75, ... },
      "created_at": "2025-10-15T10:00:00"
    }
  ]
}
```

---

## AI Scenarios

Bot scenarios define how AI analyzes content from sources.

### Create Scenario

```
POST /api/v1/ai/scenarios
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "Sentiment Monitoring",
  "description": "Track customer sentiment",
  "analysis_types": ["sentiment", "keywords"],
  "content_types": ["posts", "comments"],
  "scope": {
    "sentiment_config": {
      "categories": ["positive", "negative", "neutral"]
    }
  },
  "base_prompt": "Analyze sentiment: {content}",
  "media_overrides": {"image": "Describe this image", "video": "Summarize this video"},
  "summary_prompt": "Create a unified summary from all analyses",
  "analysis_types": ["sentiment", "keywords", "topics"],
  "content_types": ["posts", "comments"],
  "is_active": true,
  "max_tokens": 2048
}
```

**Response** `201 Created`: `ScenarioResponse` object.

**Permissions:** `agentscenario.create`

### List Scenarios

```
GET /api/v1/ai/scenarios?is_active=true
Authorization: Bearer <token>
```

**Query params:**
| Param | Type | Description |
|---|---|---|
| `is_active` | bool | Filter: `true` / `false` / omit for all |

**Permissions:** `agentscenario.view`

### Get Scenario

```
GET /api/v1/ai/scenarios/{scenario_id}
Authorization: Bearer <token>
```

**Permissions:** `agentscenario.view`

### Update Scenario

```
PUT /api/v1/ai/scenarios/{scenario_id}
Authorization: Bearer <token>
Content-Type: application/json

{
  "analysis_types": ["sentiment", "keywords", "topics"],
  "scope": {
    "topics_config": {"max_topics": 10}
  }
}
```

All fields are optional (partial update).

**Permissions:** `agentscenario.update`

### Delete Scenario

```
DELETE /api/v1/ai/scenarios/{scenario_id}
Authorization: Bearer <token>
```

**Permissions:** `agentscenario.delete`

A scenario is not owned by sources — a source does not carry a
scenario (the scenario belongs to the task). Tasks referencing the scenario
keep the FK with `ondelete=SET NULL` and fall back to the workspace default.

---

## Notifications

### List Notifications

```
GET /api/v1/notifications?is_read=false&notification_type=alert&since=2025-01-01&limit=50&offset=0
Authorization: Bearer <token>
```

**Query params:**
| Param | Type | Description |
|---|---|---|
| `is_read` | bool | Filter by read status |
| `notification_type` | string | Filter by type |
| `since` | datetime | Show notifications after this date |
| `limit` | int | Max 100 |
| `offset` | int | Pagination offset |

**Permissions:** `notification.view`

### Get Notification Stats

```
GET /api/v1/notifications/stats?since=2025-01-01
Authorization: Bearer <token>
```

**Permissions:** `notification.view`

**Response:**
```json
{
  "total": 42,
  "unread": 5,
  "by_type": {"alert": 20, "info": 22}
}
```

### Get Notification

```
GET /api/v1/notifications/{notification_id}
Authorization: Bearer <token>
```

**Permissions:** `notification.view`

### Create Notification

```
POST /api/v1/notifications
Authorization: Bearer <token>
Content-Type: application/json

{
  "title": "System Alert",
  "message": "High toxicity detected in source #3",
  "notification_type": "alert",
  "related_entity_type": "source",
  "related_entity_id": 3
}
```

**Permissions:** `notification.create`

### Mark as Read

```
POST /api/v1/notifications/{notification_id}/mark-read
Authorization: Bearer <token>
```

**Permissions:** `notification.update`

### Mark All as Read

```
POST /api/v1/notifications/mark-all-read
Authorization: Bearer <token>
```

**Permissions:** `notification.update`

**Response:**
```json
{
  "status": "success",
  "marked_count": 5
}
```

### Delete Notification

```
DELETE /api/v1/notifications/{notification_id}
Authorization: Bearer <token>
```

**Permissions:** `notification.delete`

### Cleanup Old Notifications

```
POST /api/v1/notifications/cleanup?days=30
Authorization: Bearer <token>
```

Deletes read notifications older than `days` (1–365).

**Permissions:** `notification.delete`

---

## Dashboard

### Web home overview

`GET /app/` is a compact read-only overview, not the machine dashboard below.
The merged cleanup removes KPI/aggregation cards, quick commands, analytical
sidebar and inline task execution. It shows up to five permitted analysis cards,
three recent task summaries, one conditional setup step and a chat entry.
Analysis/task loading requires the effective aianalytics.view / agenttask.view;
source labels additionally require source.view. The operator workspace filter
is retained. The merged shell lets home use the available width: result/task cards share a
row from 1200px and stack below it; results fill the width when tasks are denied.
Metadata wraps with bounded title/summary previews. Guest-workspace chat entry
and direct web chat endpoints are denied, including for visiting operators.
No home-request KPI aggregation, new LLM call or migration.

Attention is implemented in the unified bell, not a second home block (see
[NOTIFICATIONS](NOTIFICATIONS.md#current-web-bell-and-display-policy)). Mixed
analysis/digest feed and truly upcoming schedules remain deferred in [the phase tasks](phases/p8-first-value-ui.md).
The current task preview is recent-by-creation, not a nearest-run claim. This
source change is not evidence of deployment or completed runtime validation.
The chat-primary home/entry journey is a product target tracked in
[P8-T37](tasks/p8-t37.md); the overview description here records the current
route behavior.

### Get Dashboard Stats

```
GET /api/v1/dashboard/stats?platform_id=1&source_type=GROUP&since=2025-01-01
Authorization: Bearer <token>
```

**Query params:**
| Param | Type | Description |
|---|---|---|
| `platform_id` | int | Filter by platform |
| `source_type` | string | Filter by source type |
| `since` | date | Stats since this date |

**Permissions:** `source.view`

**Response:**
```json
{
  "total_sources": 10,
  "active_sources": 8,
  "total_platforms": 3,
  "active_platforms": 3,
  "total_analytics": 150,
  "total_topics": 25,
  "unread_notifications": 5,
  "sources_by_platform": {"VK": 7, "Telegram": 3},
  "sources_by_type": {"GROUP": 5, "CHANNEL": 3, "USER": 2},
  "analytics_by_period": {"DAY": 100, "WEEK": 50}
}
```

### Get Sources Summary

```
GET /api/v1/dashboard/sources?platform_id=1&source_type=GROUP&is_active=true&limit=50&offset=0
Authorization: Bearer <token>
```

**Permissions:** `source.view`

**Response:**
```json
[
  {
    "id": 1,
    "name": "My VK Group",
    "platform_name": "VK",
    "source_type": "GROUP",
    "is_active": true,
    "last_checked": "2025-10-15T10:00:00",
    "analytics_count": 30
  }
]
```

### Get Analytics Summary

```
GET /api/v1/dashboard/analytics?source_id=1&period_type=DAY&since=2025-01-01&limit=50&offset=0
Authorization: Bearer <token>
```

**Permissions:** `aianalytics.view`

### Get Source Trends

```
GET /api/v1/dashboard/trends/{source_id}?days=30&metric=sentiment
Authorization: Bearer <token>
```

**Query params:**
| Param | Type | Default | Description |
|---|---|---|---|
| `days` | int | 30 | Days to analyze (1–365) |
| `metric` | string | sentiment | `sentiment`, `activity`, `engagement` |

**Permissions:** `source.view`

**Response:**
```json
[
  {
    "date": "2025-10-01",
    "value": 0.75,
    "label": "positive"
  }
]
```

### Get Recent Notifications

```
GET /api/v1/dashboard/notifications/recent?limit=10
Authorization: Bearer <token>
```

**Permissions:** `notification.view`

### Get Topic Chains

```
GET /api/v1/dashboard/topic-chains?source_id=1&limit=50
Authorization: Bearer <token>
```

**Permissions:** `aianalytics.view`

### Get Topic Chain Details

```
GET /api/v1/dashboard/topic-chains/{chain_id}
Authorization: Bearer <token>
```

**Permissions:** `aianalytics.view`

### Get Topic Chain Evolution

```
GET /api/v1/dashboard/topic-chains/{chain_id}/evolution
Authorization: Bearer <token>
```

**Permissions:** `aianalytics.view`

---

## Analytics Aggregation

All aggregation endpoints use `ReportAggregator` and require authentication.

### Sentiment Trends

```
GET /api/v1/dashboard/analytics/aggregate/sentiment-trends?source_id=1&days=7&group_by=day
Authorization: Bearer <token>
```

**Permissions:** `aianalytics.view`

**Response:**
```json
{
  "trends": [
    {
      "date": "2025-10-15",
      "avg_sentiment_score": 0.75,
      "total_analyses": 5,
      "distribution": {
        "positive": 3,
        "neutral": 1,
        "negative": 1
      }
    }
  ],
  "period_days": 7,
  "group_by": "day"
}
```

### Top Topics

```
GET /api/v1/dashboard/analytics/aggregate/top-topics?source_id=1&days=7&limit=10
Authorization: Bearer <token>
```

**Permissions:** `aianalytics.view`

**Response:**
```json
{
  "topics": [
    {
      "topic": "AI Technologies",
      "count": 12,
      "avg_sentiment": 0.8,
      "examples": ["Example 1...", "Example 2..."]
    }
  ],
  "period_days": 7,
  "total_topics": 10
}
```

### LLM Stats

```
GET /api/v1/dashboard/analytics/aggregate/llm-stats?source_id=1&days=30
Authorization: Bearer <token>
```

**Permissions:** `aianalytics.view`

**Response:**
```json
{
  "providers": {
    "openai": {
      "requests": 150,
      "total_tokens": 45000,
      "request_tokens": 30000,
      "response_tokens": 15000,
      "estimated_cost_usd": 0.45,
      "avg_tokens_per_request": 300.0,
      "models": {
        "gpt-4o-mini": 120,
        "gpt-4o": 30
      }
    }
  },
  "summary": {
    "total_requests": 200,
    "total_cost_usd": 0.55,
    "period_days": 30
  }
}
```

### Content Mix

```
GET /api/v1/dashboard/analytics/aggregate/content-mix?source_id=1&days=7
Authorization: Bearer <token>
```

**Permissions:** `aianalytics.view`

### Engagement Metrics

```
GET /api/v1/dashboard/analytics/aggregate/engagement?source_id=1&days=7
Authorization: Bearer <token>
```

**Permissions:** `aianalytics.view`

### Grouped Analytics

```
GET /api/v1/dashboard/analytics/aggregate/grouped?group_by=themes&time_breakdown=false&entity_type=brand&source_id=1&days=7
Authorization: Bearer <token>
```

**Query params:**
| Param | Type | Required | Description |
|---|---|---|---|
| `group_by` | string | No | Default `themes`; `themes`, `sources`, `entities`, `intent`, `topic_chains`. `days` is web-only and returns 400 here. |
| `time_breakdown` | bool | No | Include per-date breakdown inside each group |
| `entity_type` | string | No | Filter entities by type (`person`, `brand`, `org`); only for `group_by=entities` |
| `sentiment` | string | No | Cross-axis filter: `positive`, `neutral`, `negative` |
| `media` | string | No | Cross-axis media_types containment filter: `text`, `image`, `video` |
| `source_id` | int | No | Filter by source |
| `scenario_id` | int | No | Filter by scenario |
| `days` | int | No | Look-back window |

**Permissions:** `aianalytics.view`

---

## LLM Providers

### Personal catalog (PR #229 integrated; migration 0109 defined)

Both `/api/v1/llm/llm-providers` and `/api/v1/llm/llm-models` use the
active authenticated user's personal catalog, without workspace membership or
an operator/admin gate. `require_model_perm` remains the endpoint guard and
recognizes active-user self-service for these resources. IDs are owner-filtered;
foreign IDs are not editable. `owner_user_id` is not client-assignable. These
routes are integrated in dev. Owner-local ASGI evidence is recorded at its
original source SHA; it is not a test rerun on final dev and does not imply a
database migration or deployment.

Provider `uid` is required at creation, mutable and unique per owner; `name` is
a display label and numeric ID is immutable. HTTPS base URLs and bounded field
values are validated. `api_key` is write-only: an absent/empty key preserves
existing encrypted storage. Read responses never contain encrypted/plaintext keys;
`auth_header` is redacted to null because historical values may contain credentials.

The web UI uses CSRF-checked `POST /app/settings/models/save` for one atomic
provider+N models bundle (provider-only save supported), and
`POST /app/settings/models/default` for the same owner-locked default service.
Sparse edits preserve unspecified fields/models. Defaults are per-user and
capability, not global. Chat settings persist integer `agent_model_id`, with the
same owner/provider/activity/tier eligibility for options, save, probe and runtime;
image, video and audio resolution use the same gate, and the analyzer's failover
queue never offers another owner's model;
a chosen foreign/ineligible FK is rejected rather than resolved by name.

See [evidence and outstanding real acceptance](audit/personal-llm-ownership.md).

### Create Provider

```
POST /api/v1/llm/llm-providers/
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "OpenAI",
  "uid": "openai",
  "description": "OpenAI GPT models",
  "api_format": "openai",
  "base_url": "https://api.openai.com/v1",
  "auth_header": "Bearer",
  "is_active": true
}
```

Providers do not have an `is_default` field. Default selection belongs to
`LLMModel.is_default`; migration 0091 removed the obsolete provider flag.

**Permissions:** `llmprovider.create`

### List Providers

```
GET /api/v1/llm/llm-providers/?is_active=true
Authorization: Bearer <token>
```

**Permissions:** `llmprovider.view`

### Get Provider

```
GET /api/v1/llm/llm-providers/{provider_id}
Authorization: Bearer <token>
```

**Permissions:** `llmprovider.view`

### Update Provider

```
PATCH /api/v1/llm/llm-providers/{provider_id}
Authorization: Bearer <token>
Content-Type: application/json

{
  "is_active": false
}
```

**Permissions:** `llmprovider.update`

### Delete Provider

```
DELETE /api/v1/llm/llm-providers/{provider_id}
Authorization: Bearer <token>
```

**Permissions:** `llmprovider.delete`

---

## LLM Models

### Create Model

```
POST /api/v1/llm/llm-models/
Authorization: Bearer <token>
Content-Type: application/json

{
  "provider_id": 1,
  "name": "GPT-4 Turbo",
  "model_id": "gpt-4-turbo",
  "model_type": "text",
  "description": "OpenAI GPT-4 Turbo",
  "custom_endpoint_path": null,
  "input_cost_per_1k": 0.01,
  "output_cost_per_1k": 0.03,
  "max_tokens": 128000,
  "default_temperature": 0.3,
  "is_active": true,
  "is_default": false
}
```

**Permissions:** `llmmodel.create`

`model_type` accepts `text`, `image`, `embedding`, `decision`.
`custom_endpoint_path` is optional (migration 0086), used for `api_format=custom`.
List/detail responses include the endpoint path and usage/health counters.

### List Models

```
GET /api/v1/llm/llm-models/?is_active=true&provider_id=1&model_type=text
Authorization: Bearer <token>
```

**Permissions:** `llmmodel.view`

### Get Model

```
GET /api/v1/llm/llm-models/{model_id}
Authorization: Bearer <token>
```

**Permissions:** `llmmodel.view`

### Update Model

```
PATCH /api/v1/llm/llm-models/{model_id}
Authorization: Bearer <token>
Content-Type: application/json

{
  "is_default": true,
  "max_tokens": 8192
}
```

**Permissions:** `llmmodel.update`

### Delete Model

```
DELETE /api/v1/llm/llm-models/{model_id}
Authorization: Bearer <token>
```

**Response** `204 No Content` (default reassignment handled automatically)

**Permissions:** `llmmodel.delete`

---

## Sources

### List Sources

```
GET /api/v1/sources
Authorization: Bearer <token>
```

**Response** `200 OK`:
```json
[
  {
    "id": 1,
    "name": "My VK Group",
    "platform_id": 1,
    "source_type": "GROUP",
    "external_id": "group_123456",
    "params": {},
    "is_active": true,
    "last_checked": "2025-10-15T10:00:00",
    "last_item_id": 999,
    "created_at": "2025-01-01T00:00:00",
    "updated_at": "2025-01-01T00:00:00"
  }
]
```

**Permissions:** `source.view`

### Get Source

```
GET /api/v1/sources/{source_id}
Authorization: Bearer <token>
```

**Permissions:** `source.view`

### Create Source

```
POST /api/v1/sources
Authorization: Bearer <token>
Content-Type: application/json

{
  "platform_id": 1,
  "name": "My VK Group",
  "source_type": "GROUP",
  "external_id": "group_123456",
  "params": {}
}
```

**Response** `201 Created`.

**Permissions:** `source.create`

### Update Source

```
PATCH /api/v1/sources/{source_id}
Authorization: Bearer <token>
Content-Type: application/json

{
  "is_active": false
}
```

**Permissions:** `source.update`

### Delete Source

```
DELETE /api/v1/sources/{source_id}
Authorization: Bearer <token>
```

**Response** `204 No Content`.

**Permissions:** `source.delete`

**Refused** `409 Conflict` while an active task still reads the source:

```json
{
  "detail": {
    "error": "source_in_active_task",
    "task_ids": [2027]
  }
}
```

A source an active task uses is removed inside that task, where its scope is
visible; the web page refuses the same way. A paused task does not block the
delete, and a task that never named sources (all-active) is untouched.

Deleting a source also deletes its analytics rows.

## Tasks

### List Tasks

```
GET /api/v1/tasks
Authorization: Bearer <token>
```

**Response** `200 OK`:
```json
[
  {
    "id": 1,
    "name": "Daily Sentiment",
    "cron_expr": "0 9 * * *",
    "job_type": "analyze",
    "payload": {},
    "is_active": true,
    "next_run_at": "2025-10-16T09:00:00",
    "last_run_at": "2025-10-15T09:00:00",
    "last_status": "success",
    "last_error": null,
    "agent_scenario_id": 1,
    "created_at": "2025-01-01T00:00:00",
    "updated_at": "2025-01-01T00:00:00"
  }
]
```

**Permissions:** `agenttask.view`

### Get Task

```
GET /api/v1/tasks/{task_id}
Authorization: Bearer <token>
```

**Permissions:** `agenttask.view`

### Create Task

```
POST /api/v1/tasks
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "Daily Sentiment",
  "cron_expr": "0 9 * * *",
  "job_type": "analyze",
  "payload": {"period": "week"},
  "collection": {
    "content_types": ["posts", "comments"],
    "engagement_types": ["likes"],
    "limit": 250,
    "queries": ["доставка"],
    "city": {"name": "Киров", "mode": "prefer"},
    "comments": {"max_per_post": 5, "total": 40},
    "candidates": {"limit": 25, "confirm": "auto"}
  },
  "agent_scenario_id": 1,
  "is_active": true,
  "source_ids": [1, 2]
}
```

`collection` is task-owned collection intent. It is validated by the same parser
as the web editor and CLI, stored under `payload.collection` in canonical form,
and kept only for `collect`/`analyze`; `digest`, `prune`, `learn` and `reflect`
drop it. A task without the block keeps the posts-only default.

**Response** `201 Created`.

**Permissions:** `agenttask.create`

### Update Task

```
PATCH /api/v1/tasks/{task_id}
Authorization: Bearer <token>
Content-Type: application/json

{
  "is_active": false
}
```

The update is a partial patch. `collection` follows three explicit cases:
omitted keeps the stored block, `null` clears it, an object replaces it and is
validated (a malformed block returns `400`, the stored value is unchanged).
A `job_type` change to a non-collecting type also drops the block.

**Permissions:** `agenttask.update`

### Delete Task

```
DELETE /api/v1/tasks/{task_id}
Authorization: Bearer <token>
```

**Response** `204 No Content`.

**Permissions:** `agenttask.delete`

### Pause/Resume Task

```
PATCH /api/v1/tasks/{task_id}/pause?resume=true
Authorization: Bearer <token>
```

Set `resume=true` to resume, `resume=false` (default) to pause.

**Permissions:** `agenttask.update`

### Run Task (by ID)

```
POST /api/v1/tasks/{task_id}/run
Authorization: Bearer <token>
Content-Type: application/json

{
  "payload": {"extra_param": "value"}
}
```

**Response** `200 OK`:
```json
{
  "job_id": 42,
  "task_id": 1,
  "status": "success",
  "result": {...},
  "error": null
}
```

**Permissions:** `agenttask.update`

### Run One-off Task

```
POST /api/v1/tasks/run
Authorization: Bearer <token>
Content-Type: application/json

{
  "job_type": "collect_and_analyze",
  "payload": {},
  "source_ids": [1],
  "scenario_id": 1
}
```

Creates a temporary `@once` task and runs it immediately.

**Permissions:** `agenttask.update`

## Credentials

### List Credentials

```
GET /api/v1/credentials
Authorization: Bearer <token>
```

Returns the current user's personal credentials (secrets are encrypted, never returned).

**Permissions:** `credential.view`

### Disable Credential

```
PATCH /api/v1/credentials/credentials/{credential_id}/disable
Authorization: Bearer <token>
```

**Implementation discrepancy:** `entry.py` mounts the credentials router under
`/credentials`, and this endpoint also declares `/credentials/{credential_id}/disable`.
The composed route therefore repeats the segment. The single-segment path
`/api/v1/credentials/{credential_id}/disable` is not registered by the current source.

Deactivates a credential (row kept for audit).

**Permissions:** `credential.update`

### Login Credential (interactive)

```
POST /api/v1/credentials/login
Authorization: Bearer <token>
Content-Type: application/json

{
  "platform": "telegram"
}
```

Initiates interactive login for a platform (e.g. telegram MTProto). Only `telegram` is supported.

**Response** `201 Created`: `CredentialResponse` object.

**Permissions:** `credential.configure`

### OAuth Credential

```
POST /api/v1/credentials/oauth
Authorization: Bearer <token>
Content-Type: application/json

{
  "platform": "vk"
}
```

Initiates OAuth flow for a platform (e.g. VK PKCE). Only `vk` is supported.

**Response** `200 OK`:
```json
{
  "authorize_url": "https://vk.com/...",
  "tenant_id": 1
}
```

**Permissions:** `credential.configure`

### Test Credentials

```
GET /api/v1/credentials/test
Authorization: Bearer <token>
```

Tests all platform credentials and reports status.

**Response** `200 OK`:
```json
[
  {"platform": "vk", "source": "api_hash", "status": "ok"},
  {"platform": "telegram", "source": "api_hash", "status": "ok"},
  {"platform": "telegram", "source": "mtproto", "status": "ok (bot @...)}
]
```

**Permissions:** `credential.view`

## Error Responses

| Code | Meaning |
|---|---|
| `400` | Bad request (invalid credentials, validation error) |
| `401` | Unauthorized (missing or invalid token) |
| `403` | Forbidden (insufficient permissions) |
| `404` | Resource not found |
| `422` | Validation error (Pydantic) |
| `500` | Internal server error |

All error responses follow this shape:
```json
{
  "detail": "Error message"
}
```

---

## Admin Panel

The sqladmin panel provides a web UI for managing all database entities.

**Base URL:** `http://localhost:8000/admin`

**Pages:** User, Role, Permission, Platform, Source, AgentScenario, BotAction,
AIAnalytics, Notification, LLMProvider, LLMModel, UserCredential.

### Password Administration

There is no anonymous password recovery route. Password changes and resets are
authenticated platform-operator actions; see
[ADMIN password routes](ADMIN.md#password-routes-outside-sqladmin).

### Change Password

```
GET /admin/user/change-password/{user_id}
POST /admin/user/change-password/{user_id} (form: current_password, new_password, confirm_password, csrf_token)
```

### Analytics Dashboard

```
GET /dashboard
GET /dashboard/topic-chains
```

---

## Schemas Reference

### User

```json
{
  "id": 1,
  "username": "string (3-50 chars)",
  "email": "valid email",
  "is_active": true,
  "is_superuser": false,
  "created_at": "ISO datetime",
  "updated_at": "ISO datetime"
}
```

### Token

```json
{
  "access_token": "JWT string",
  "refresh_token": "JWT string (login/register only)",
  "token_type": "bearer",
  "expires_at": 1735689600
}
```

### ScenarioResponse

```json
{
  "id": 1,
  "name": "string",
  "description": "string",
  "analysis_types": ["sentiment", "keywords"],
  "content_types": ["posts", "comments"],
  "scope": {},
  "base_prompt": "string",
  "media_overrides": {},
  "summary_prompt": "string",
  "is_active": true,
  "collection_interval_hours": 24,
  "created_at": "ISO datetime",
  "updated_at": "ISO datetime"
}
```

### LLMProviderResponse

```json
{
  "id": 1,
  "owner_user_id": 2,
  "uid": "openai",
  "name": "OpenAI",
  "description": "string",
  "api_format": "openai",
  "base_url": "https://api.openai.com/v1",
  "auth_header": null,
  "is_active": true,
  "created_at": "ISO datetime",
  "updated_at": "ISO datetime"
}
```

Removed grouping values `sentiment` and `content_type` return 400, listing:
themes, sources, entities, intent, topic_chains. Invalid sentiment/media filter
values return 422. Filtering precedes grouping, counts and date breakdowns.

### Split role catalogs and personal account operations

The target schema requires migration 0096 (prepared, not automatically applied).
Platform roles are `user`, `operator`, `admin`; workspace roles are independent
`owner`, `admin`, `manager`, `analyst`, `viewer`. Role DTOs expose `codename`,
`label`, text `description`, and a read-only `name` alias for one release.
Only platform admins may update operator grants, using exact registered
`domain.resource.action` keys. Role codenames are not freely editable.

`PUT /api/v1/users/{id}/platform-role` accepts `{"role": "operator"}` (or another
platform code) and reloads the platform admin before writing. Profile payloads
accept only username/email and never change authority. HTTP profiles never expose
password hashes. `POST /api/v1/auth/change-password` changes the caller's password
using the current password and does not require a workspace.

Workspace team AI access uses CSRF-protected
`POST /app/settings/members/{id}/ai`, with `enabled=1` or `0`. It is limited to the
current workspace and the same delegation restrictions as member-role changes.

### Operator financial exposure

`GET /app/billing/exposure` is a read-only platform-operator page (the same
`guard_superuser` rule as the queue page) pinned to the active workspace. It
renders three independent projections and shows an explicit unavailable state
instead of a fabricated zero when a read fails:

- today's payer-bound dispatches for the workspace from the personal billing
  ledger: settled, reserved, provider-reported and receipt-tariff reconstructed
  USD, open reservations and unknown-price attempts. `complete` is false while
  any of the last two is non-zero;
- provider/model pairs with `cost_certainty=undefined` dispatched attempts in the
  last 7 days, counted once per dispatch so a reconciliation does not inflate
  exposure;
- active catalog models whose ordinary tariff is 0/0 — an explicit free tariff
  and an unconfigured default are indistinguishable there, so this is a review
  list, not a claim of unknown pricing.

No payer identity, prompt, credential or provider payload is rendered. Legacy
attempts without payer attribution stay outside the ledger projection. The page
performs no writes and creates no billing account or reservation.

### Global platform catalog in web Settings

`GET /app/settings?tab=platforms` selects the actual Settings section (not an
admin-console redirect). Only an active authenticated platform administrator
receives this tab/data; workspace ownership and operator grants do not open it.
The section is available without a workspace. Other workspace tabs retain their
membership checks and do not inherit platform authority.

`POST /app/settings/platforms` rechecks the platform-admin role and `_csrf` before
any manager call. Fields: `platform_id` (0/omitted creates, positive edits), `name`
(nonempty, max 50), `base_url` (HTTP(S), max 255, no URL username/password),
`enabled=on` (omitted means inactive); create additionally requires
`platform_type=vk|telegram`. Existing platform type and params are not changed.
Uses the existing Platform manager. Handler denial is 403; missing edit target is
404; successful save or validation/uniqueness feedback redirects to the tab with
a flash message. No delete, sync, live API probe, personal credentials or global
provider/model editing is exposed by this section.
See [tenancy boundaries](TENANCY.md#web-settings-and-ownedgranted-workspace-context)
and [deletion policy](MODEL_DELETION.md).

## Universal social task parity (P8-T29)

Current chat and task endpoints are part of the shared implementation described
in [P8-T29](tasks/p8-t29.md); this documents code surfaces, not completion of the
product journey. The target is the chat as the primary workspace for
clarification, preview, execution, progress, results and follow-up, while the
dedicated management pages remain directly usable. Completing that interaction
contract is tracked in [P8-T37](tasks/p8-t37.md). Keep recurring-task creation
and digest sending confirmation-gated.

API and web/CLI/admin/agent tools preserve identical source-scope, scenario and
data-window semantics. Preview is read-only; execution performs fresh
authorization and budget admission; existing task routes remain compatible.
