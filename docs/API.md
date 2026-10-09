# API Reference

FastAPI application exposes REST endpoints under `/api/v1` and the sqladmin
panel under `/admin`. All API endpoints (except health check) require
authentication via Bearer JWT token.

## Base URL

```
http://localhost:8000/api/v1
```

## Authentication

All endpoints (except `GET /api/v1/health`) require a JWT access token.

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
  "password": "strongpassword123",
  "is_active": true,
  "is_superuser": false
}
```

**Response** `201 Created`: same as login response.

**Errors:**
- `400` — Username or email already registered.

---

## Authorization

Authentication answers *who* is calling; authorization answers *what they may
do*. They are separate layers, and every surface uses the same two:

| Surface | Authentication | Rights |
|---|---|---|
| `/api/*` | bearer JWT (`ApiScopeMiddleware`) | per-endpoint: `require_model_perm` (model rights) and `require_platform_role` (role ladder); most write endpoints and many read endpoints declare model rights |
| `/app/*` | web session (`TenantUIMiddleware`) | workspace membership is the boundary; no per-model right in the client UI |
| `/admin/*` | admin backend | per-model rights, Django-style (`docs/ADMIN.md`) |
| CLI | none (developer tool) | runs with the tenant guard off; `--tenant` picks a workspace |

### Workspace selection

Every `/api/*` request runs inside one workspace, resolved from the caller's
`tenant_users` membership and never from the request body:

- unauthenticated → `401`;
- `X-Tenant-Id` / `X-Tenant-Slug` *select* among the caller's own workspaces, so
  a workspace they are not a member of is `403`, never honoured;
- data access goes through `BaseManager`, so every SELECT is filtered to that
  workspace — a foreign id reads as "not found", not as a leak.

### Rights

The rights of a caller are the rights of their **platform role**
(`users.role_id`), read from `role_permission` → `permissions`:

```python
require_model_perm("source", ActionType.CREATE)
```

means "this role may create sources", checked against
`permissions.model_type_id` + `action_type` — never against the `codename`
column, because several seeded codenames are malformed. A superuser
(`users.is_superuser` or the `SUPERUSER` role) passes everything.

`User.model_permissions()` is the canonical predicate for all three surfaces, so
the API, the console and the role editor cannot drift apart. See
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
| `POST /monitoring/collect/{source,platform,monitored}`, `GET /monitoring/analytics/source/{id}` | `source` | `UPDATE` / `VIEW` |
| `GET/POST/PATCH/DELETE /sources` | `source` | `VIEW` / `CREATE` / `UPDATE` / `DELETE` |
| `GET/POST/PATCH/DELETE /tasks`, `PATCH /tasks/{id}/pause`, `POST /tasks/{id,run,run}` | `agenttask` | `VIEW` / `CREATE` / `UPDATE` / `DELETE` / `UPDATE` / `UPDATE` / `UPDATE` |
| `GET/PATCH/POST/POST/GET /credentials`, `POST /credentials/login`, `POST /credentials/oauth` | `credential` | `VIEW` / `UPDATE` / `CONFIGURE` / `CONFIGURE` / `VIEW` |
| `GET list/detail, POST, PATCH, DELETE /llm/llm-providers` | `llmprovider` | `VIEW` / `CREATE` / `UPDATE` / `DELETE` |
| `GET list/detail, POST, PATCH, DELETE /llm/llm-models` | `llmmodel` | `VIEW` / `CREATE` / `UPDATE` / `DELETE` |
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

No authentication required.

---

## Users

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
    "is_superuser": true,
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

Admins can update any user, users can update themselves.

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

### Collect Monitored Users

```
POST /api/v1/monitoring/collect/monitored
Authorization: Bearer <token>
Content-Type: application/json

{
  "source_id": 1,
  "analyze": true
}
```

**Permissions:** `source.update`

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

### Create Provider

```
POST /api/v1/llm/llm-providers/
Authorization: Bearer <token>
Content-Type: application/json

{
  "name": "OpenAI",
  "description": "OpenAI GPT models",
  "api_format": "openai",
  "base_url": "https://api.openai.com/v1",
  "auth_header": "Bearer",
  "is_active": true,
  "is_default": true
}
```

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
    "job_type": "collect_and_analyze",
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
  "job_type": "collect_and_analyze",
  "payload": {},
  "agent_scenario_id": 1,
  "is_active": true,
  "source_ids": [1, 2]
}
```

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
PATCH /api/v1/credentials/{credential_id}/disable
Authorization: Bearer <token>
```

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

### Password Reset

```
GET /admin/reset-password
POST /admin/reset-password (form: email, csrf_token)
```

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
  "name": "OpenAI",
  "description": "string",
  "api_format": "openai",
  "base_url": "https://api.openai.com/v1",
  "auth_header": "Bearer",
  "is_active": true,
  "is_default": true,
  "created_at": "ISO datetime",
  "updated_at": "ISO datetime"
}
```

Removed grouping values `sentiment` and `content_type` return 400, listing:
themes, sources, entities, intent, topic_chains. Invalid sentiment/media filter
values return 422. Filtering precedes grouping, counts and date breakdowns.
