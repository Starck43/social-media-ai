# Data Model Reference

Entity-relationship reference for all database tables in the configured schema (`settings.DB_SCHEMA`, default `public`).

## Base Classes

### `Base`

SQLAlchemy `DeclarativeBase` with:
- Default schema: `settings.DB_SCHEMA` (default `public`)
- Custom `save()` and `delete()` methods
- `objects` class attribute set to `BaseManager` after class definition

### `TimestampMixin`

Adds to any model:
| Column | Type | Default |
|---|---|---|
| `created_at` | `DateTime(timezone=True)` | `func.now()` |
| `updated_at` | `DateTime(timezone=True)` | `func.now()`, auto-updated |

### `TenantScopedMixin`

Adds to any model:
| Column | Type | Default |
|---|---|---|
| `tenant_id` | `Integer` (FK → `tenants.id`) | Required, CASCADE delete |

**Enforcement:** `BaseManager` filters all SELECT queries by `current_tenant_id()`, stamps `tenant_id` on `create()`, and re-fetches `update_by_id`/`delete_by_id` through the scoped queryset.

---

## Global Tables (not tenant-scoped)

These tables are shared across all tenants and are resolved before a tenant context exists.

### `platforms`

Social media platforms.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `name` | `String(100)` | Platform name (e.g., "VK", "Telegram") |
| `platform_type` | `Enum` | Platform type enum |
| `base_url` | `String` | Base URL for the platform |
| `is_active` | `Boolean` | Whether the platform is active |
| `params` | `JSON` | API request settings |
| `rate_limit_remaining` | `Integer` | Current rate limit remaining |
| `rate_limit_reset_at` | `DateTime` | When rate limit resets |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Relationships:** `sources` (one-to-many)

---

### `roles`

RBAC roles.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `name` | `String(100)` | Display name |
| `codename` | `String(50)` Unique | Machine-readable name |
| `description` | `Text` | Role description |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Relationships:** `users`, `permissions` (many-to-many via `permissions_roles`)

---

### `permissions`

Individual permission entries.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `model_type` | `String(50)` | App label (e.g., "social", "account") |
| `model_type_id` | `Integer` | Model type ID |
| `action_type` | `Enum` | Permission action (add, change, delete, view) |
| `codename` | `String(100)` Unique | Machine-readable codename |
| `name` | `String(100)` | Human-readable name |
| `description` | `Text` | Permission description |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Relationships:** `roles` (many-to-many via `permissions_roles`)

---

### `llm_providers`

LLM provider configuration.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `name` | `String(100)` | Provider name (e.g., "OpenAI") |
| `description` | `Text` | Description |
| `api_format` | `String(20)` | `openai` or `anthropic` |
| `base_url` | `String(255)` | API base URL |
| `auth_header` | `String(50)` | Auth header (e.g., "Bearer") |
| `encrypted_api_key` | `Text` | Fernet-encrypted API key |
| `is_active` | `Boolean` | Whether the provider is active |
| `is_default` | `Boolean` | Default provider flag |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Relationships:** `models` (one-to-many)

---

### `llm_models`

LLM model definitions per provider.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `provider_id` | `Integer` FK → `llm_providers` | Owning provider |
| `name` | `String(100)` | Human-readable name (e.g., "GPT-4o Mini") |
| `model_id` | `String(100)` | API model string (e.g., "gpt-4o-mini") |
| `model_type` | `Enum` | `text`, `image`, `embedding` |
| `capabilities` | `JSON` | Supported media types |
| `input_cost_per_1k` | `Float` | Cost per 1K input tokens (USD) |
| `output_cost_per_1k` | `Float` | Cost per 1K output tokens (USD) |
| `max_tokens` | `Integer` | Max output tokens |
| `default_temperature` | `Float` | Default temperature |
| `is_active` | `Boolean` | Whether the model is available |
| `is_default` | `Boolean` | Default for provider |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Relationships:** `provider`, `text_scenarios`, `image_scenarios`, `video_scenarios`

---

### `model_types`

Enum values for LLM model types.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `name` | `String(50)` Unique | Type name |
| `description` | `Text` | Description |

---

### `users`

Application users (admin panel operators).

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `username` | `String(50)` Unique | Login name |
| `email` | `String(100)` Unique | Email address |
| `hashed_password` | `String(255)` | Bcrypt-hashed password |
| `is_active` | `Boolean` | Account active |
| `is_superuser` | `Boolean` | Superuser flag |
| `role_id` | `Integer` FK → `roles` | Assigned role |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Relationships:** `role`, `notifications` (one-to-many)

---

### `tenant_invites`

Invite codes for bringing clients into workspaces.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `code_hash` | `String(64)` | SHA-256 hash of the plaintext code |
| `role` | `String(20)` | Role granted on redeem (default: "owner") |
| `expires_at` | `DateTime` | Code expiry (nullable) |
| `max_uses` | `Integer` | Max redemptions (default: 1) |
| `used_count` | `Integer` | Current redemption count |
| `is_active` | `Boolean` | Whether the code is valid |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Unique constraint:** `(code_hash)`

---

## Tenant Tables (tenant-scoped)

All these tables have `tenant_id` FK and are filtered by tenant context.

### `tenants`

Client workspaces.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | `Integer` PK | | |
| `name` | `String(100)` | | Workspace name |
| `slug` | `String(50)` | | Unique slug |
| `plan` | `String(30)` | `"personal"` | Subscription plan |
| `timezone` | `String(50)` | `"Europe/Moscow"` | Workspace timezone |
| `is_active` | `Boolean` | `true` | Active flag |
| `daily_cost_limit` | `Float` | `5.0` | USD daily cost cap |
| `max_sources` | `Integer` | `20` | Max sources allowed |
| `agent_style` | `JSON` | | Reply style: `{tone, length, language, quiet_hours}` |
| `created_at` | `DateTime` | | Auto |
| `updated_at` | `DateTime` | | Auto |

**Unique constraint:** `(slug)`

---

### `tenant_users`

Membership: which messenger identity belongs to which workspace.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `tenant_id` | `Integer` FK | |
| `channel` | `String(20)` | `'telegram'` or `'max'` |
| `external_user_id` | `String(100)` | Messenger user ID |
| `role` | `String(20)` | `'owner'` or `'member'` |
| `is_active` | `Boolean` | Membership active |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Unique constraint:** `(tenant_id, channel, external_user_id)`

---

### `tenant_channels`

Messenger chat → tenant binding.

| Column | Type | Default | Description |
|---|---|---|---|
| `id` | `Integer` PK | | |
| `tenant_id` | `Integer` FK | | |
| `channel` | `String(20)` | | `'telegram'` or `'max'` |
| `chat_id` | `String(100)` | | Messenger chat ID |
| `kind` | `String(20)` | `"private"` | `'private'` or `'channel'` |
| `is_digest_target` | `Boolean` | `false` | Receives scheduled digests |
| `is_active` | `Boolean` | `true` | |
| `created_at` | `DateTime` | | Auto |
| `updated_at` | `DateTime` | | Auto |

**Unique constraint:** `(channel, chat_id)`

---

### `user_credentials`

Personal (per-user) L2 secrets, keyed by `users.id` — deliberately not
tenant-scoped (a person's tokens are valid in every workspace they belong to).

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `user_id` | `Integer` FK → `users` | Owner of the secret |
| `platform` | `String(30)` | `'vk'`, `'telegram'` |
| `kind` | `String(30)` | `'user_token'`, `'session'`, `'api_id'`, `'api_hash'` |
| `label` | `String(100)` | Free-form note |
| `secret_encrypted` | `Text` | Fernet-encrypted secret |
| `expires_at` | `DateTime` | Credential expiry |
| `meta` | `JSON` | Additional metadata |
| `is_active` | `Boolean` | Active flag |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Method:** `reveal()` — decrypts and returns the plaintext secret.

---

### `sources`

Content sources (VK groups, Telegram channels, etc.).

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `tenant_id` | `Integer` FK | |
| `platform_id` | `Integer` FK → `platforms` | |
| `name` | `String(255)` | Source name |
| `source_type` | `Enum` | `GROUP`, `CHANNEL`, `USER` |
| `external_id` | `String(100)` | Platform-specific ID |
| `params` | `JSON` | Collection settings (`mode`, `incremental_mode`, etc.) |
| `is_active` | `Boolean` | Active flag |
| `last_checked` | `DateTime` | Last collection timestamp |
| `last_item_id` | `String(100)` | Watermark for push-based sources (Telegram) |
| `date_from` | `DateTime` | Collection start date |
| `date_to` | `DateTime` | Collection end date |
| `user_id` | `Integer` FK → `users` | Owner user (nullable) |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Unique constraint:** `(tenant_id, user_id, platform_id, external_id)`

**Relationships:** `user` (owner), `platform`, `analytics` (one-to-many)

> A source does not carry a scenario — the scenario lives on the task that
> drives it (`agent_tasks.agent_scenario_id`), with the workspace default as
> the fallback for taskless runs (push ingest, CLI, agent collect).

---

### `agent_scenarios`

AI analysis contracts per source.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `tenant_id` | `Integer` FK | |
| `name` | `String(255)` | Scenario name |
| `description` | `Text` | Description |
| `content_types` | `JSON` | What to collect: `["posts", "comments"]` |
| `analysis_types` | `JSON` | What to analyze: `["sentiment", "keywords"]` |
| `scope` | `JSON` | Config params for analysis types |
| `analyze_type` | `Enum` | Analysis mode: `themes`, `days`, `sources`, `monitored_users` |
| `text_prompt` | `Text` | Custom text analysis prompt |
| `image_prompt` | `Text` | Custom image analysis prompt |
| `video_prompt` | `Text` | Custom video analysis prompt |
| `audio_prompt` | `Text` | Custom audio analysis prompt |
| `unified_summary_prompt` | `Text` | Custom summary prompt |
| `trigger_type` | `Enum` | When to analyze: `KEYWORD_MATCH`, `SENTIMENT_THRESHOLD`, etc. |
| `trigger_config` | `JSON` | Trigger parameters |
| `action_type` | `Enum` | Post-analysis action: `NOTIFICATION`, `COMMENT`, etc. |
| `rate_limit_per_hour` | `Integer` | Max actions per hour |
| `cooldown_seconds` | `Integer` | Min seconds between actions |
| `requires_approval` | `Boolean` | Require owner approval |
| `blacklist` | `JSON` | Usernames/IDs to skip |
| `whitelist` | `JSON` | Only act on these users |
| `is_active` | `Boolean` | Active flag |
| `collection_interval_hours` | `Integer` | How often to collect (min 1) |
| `text_llm_model_id` | `Integer` FK → `llm_models` | Explicit model for text |
| `image_llm_model_id` | `Integer` FK → `llm_models` | Explicit model for images |
| `video_llm_model_id` | `Integer` FK → `llm_models` | Explicit model for video |
| `llm_strategy` | `Enum` | Auto-resolve strategy: `cost_efficient`, `quality`, `multimodal` |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Relationships:** `sources` (one-to-many), `text_llm_model`, `image_llm_model`, `video_llm_model`

---

### `ai_analytics`

AI analysis results.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `tenant_id` | `Integer` FK | |
| `source_id` | `Integer` FK → `sources` | |
| `period_type` | `Enum` | `day`, `week` |
| `analysis_date` | `DateTime` | Date of the analysis period |
| `summary_data` | `JSON` | AI analysis results (sentiment, topics, etc.) |
| `response_payload` | `JSON` | Raw LLM response |
| `topic_chain_id` | `String` | Linked topic chain |
| `llm_model` | `String(100)` | Model used for analysis |
| `request_tokens` | `Integer` | Input tokens used |
| `response_tokens` | `Integer` | Output tokens generated |
| `estimated_cost` | `Numeric(14,6)` | Cost in USD cents — sub-cent precision (1e-8 USD) |
| `provider_type` | `String(30)` | LLM provider: `openai`, `anthropic`, etc. |
| `media_types` | `JSON` | Types analyzed: `["text", "image"]` |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Indexes:** `provider_type`, `analysis_date`, `source_id`

---

### `agent_tasks`

Agent cron task definitions.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `tenant_id` | `Integer` FK | |
| `name` | `String(100)` | Unique task name |
| `cron_expr` | `String(50)` | Cron expression |
| `timezone` | `String(50)` | Task timezone |
| `job_type` | `String(20)` | `collect`, `digest`, `prune`, `analyze`, `learn`, `reflect` |
| `payload` | `JSON` | Task-specific parameters (flat keys: `period`, `monitored_users`, `excluded_users`, ...) |
| `agent_scenario_id` | `Integer` FK → `agent_scenarios` | Reusable scenario applied when the task runs (nullable, `SET NULL`) |
| `is_active` | `Boolean` | Active flag |
| `next_run_at` | `DateTime` | Next scheduled run (UTC) |
| `last_run_at` | `DateTime` | Last run timestamp |
| `last_status` | `String(50)` | `success`, `failed`, etc. |
| `last_error` | `Text` | Error message if failed |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Unique constraint:** `(tenant_id, name)`

**Relationships:** `tenant`, `agent_scenario`, `sources` (many-to-many via
`agent_task_sources`)

---

### `agent_task_sources`

Many-to-many join between `agent_tasks` and `sources`. A task's sources are
linked here (not in `payload`); an empty set means all active sources.

| Column | Type | Description |
|---|---|---|
| `agent_task_id` | `Integer` FK → `agent_tasks` (PK, CASCADE) | |
| `source_id` | `Integer` FK → `sources` (PK, CASCADE) | |

---

### `jobs`

Background job queue.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `tenant_id` | `Integer` FK | |
| `agent_task_id` | `Integer` FK → `agent_tasks` | Source agent task (nullable for manual) |
| `job_type` | `String(20)` | Same values as agent_tasks |
| `payload` | `JSON` | Job parameters |
| `status` | `Enum` | `pending`, `running`, `done`, `failed` |
| `run_at` | `DateTime` | When to run (UTC) |
| `locked_at` | `DateTime` | When claimed by worker |
| `attempts` | `Integer` | Current attempt count |
| `max_attempts` | `Integer` | Max retries |
| `result` | `JSON` | Job result (for `learn`/`reflect` includes priced `llm_cost`) |
| `error` | `Text` | Error message |
| `llm_cost` | `Float` | USD spent on the LLM call (NULL = none); feeds the daily cap |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Index:** `(status, run_at)` for `SKIP LOCKED` claiming

---

### `digest_runs`

Digest delivery history.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `tenant_id` | `Integer` FK | |
| `agent_task_id` | `Integer` FK → `agent_tasks` | |
| `period_start` | `DateTime` | Start of the digest period |
| `period_end` | `DateTime` | End of the digest period |
| `status` | `Enum` | `pending`, `sent`, `failed`, `skipped` |
| `results` | `JSON` | Per-channel delivery results |
| `llm_cost` | `Float` | USD spent on the LLM summary (NULL = none); feeds the daily cap |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Unique constraint:** `(agent_task_id, period_start, period_end)`

---

### `notifications`

System notifications.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `tenant_id` | `Integer` FK | |
| `title` | `String(255)` | Notification title |
| `message` | `Text` | Notification body |
| `notification_type` | `Enum` | `alert`, `info`, `digest`, etc. |
| `is_read` | `Boolean` | Read status |
| `related_entity_type` | `String(50)` | Related entity type |
| `related_entity_id` | `Integer` | Related entity ID |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

---

## Agent Tables (tenant-scoped)

### `agent_sessions`

One row per (channel, chat_id).

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `tenant_id` | `Integer` FK | |
| `channel` | `String(20)` | `'telegram'` or `'max'` |
| `chat_id` | `String(100)` | Messenger chat ID |
| `state` | `JSON` | Volatile: pending confirmations, etc. |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Unique constraint:** `(tenant_id, channel, chat_id)`

---

### `agent_messages`

Dialog transcript.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `tenant_id` | `Integer` FK | |
| `session_id` | `Integer` FK → `agent_sessions` | |
| `role` | `Enum` | `user`, `assistant`, `tool` |
| `content` | `Text` | Message content |
| `tool_calls` | `JSON` | Tool call data (for assistant) |
| `tool_outputs` | `JSON` | Tool output data (for tool) |
| `tokens` | `Integer` | Tokens used |
| `cost` | `Float` | Cost in USD (not cents) |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

---

### `agent_memory`

Durable fact storage.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `tenant_id` | `Integer` FK | |
| `scope` | `String(50)` | Scope category |
| `key` | `String(100)` | Fact key |
| `value` | `Text` | Fact value |
| `source` | `String(20)` | `manual`, `learn`, `reflect` |
| `confidence` | `Float` | 0.1–1.0 |
| `evidence_message_id` | `Integer` FK → `agent_messages` | SET NULL |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

**Unique constraint:** `(tenant_id, scope, key)`

---

### `agent_feedback`

Owner feedback on agent replies.

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `tenant_id` | `Integer` FK | |
| `session_id` | `Integer` FK → `agent_sessions` | |
| `message_id` | `Integer` FK → `agent_messages` | Message being rated |
| `vote` | `Enum` | `good`, `bad` |
| `note` | `Text` | Optional note (for /bad) |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

---

## Bot Action Tables

### `bot_actions`

Action ledger (audit trail for automated actions).

| Column | Type | Description |
|---|---|---|
| `id` | `Integer` PK | |
| `tenant_id` | `Integer` FK | |
| `source_id` | `Integer` FK → `sources` | Source that triggered the action |
| `agent_scenario_id` | `Integer` FK → `agent_scenarios` | Scenario that defined the action |
| `action_type` | `Enum` | Type of action performed |
| `payload` | `JSON` | Action payload |
| `status` | `Enum` | `pending`, `sent`, `failed`, `dry_run` |
| `error` | `Text` | Error message if failed |
| `approved_by` | `String(100)` | Who approved (if requires_approval) |
| `created_at` | `DateTime` | Auto |
| `updated_at` | `DateTime` | Auto |

---

## ER Diagram (simplified)

```
┌──────────┐     ┌──────────────┐     ┌──────────┐
│  users   │────<│   roles      │     │platforms │
└──────────┘     └──────┬───────┘     └────┬─────┘
                        │                  │
               ┌────────┴────────┐    ┌────┴─────┐
               │  permissions    │    │ sources  │──┐
               └─────────────────┘    └────┬─────┘  │
                                          │        │
               ┌──────────────────┐       │
               │  agent_scenarios │<──────┘
               └────────┬─────────┘
                        │
               ┌────────┴─────────┐
               │  ai_analytics    │
               └──────────────────┘

┌──────────┐     ┌──────────────────┐     ┌──────────────────┐
│ tenants  │────<│ tenant_users     │     │ tenant_channels  │
└────┬─────┘     └──────────────────┘     └──────────────────┘
     │     ┌──────────────────┐     ┌──────────────────┐
     │     │ tenant_invites   │     │user_credentials  │
     │     └──────────────────┘     └──────────────────┘
     │
     ├────< sources
     ├────< agent_scenarios
     ├────< ai_analytics
     ├────< agent_tasks
     ├────< jobs
     ├────< digest_runs
     ├────< notifications
     ├────< agent_sessions
     ├────< agent_messages
     ├────< agent_memory
     ├────< agent_feedback
     └────< bot_actions
```

---

## Migration History

| Migration | Description |
|---|---|
| `0031` | Add LLM cost tracking to `ai_analytics` |
| `0044` | Add tenancy core tables (`tenants`, `tenant_users`, `tenant_invites`, `tenant_channels`, workspace `tenant_credentials`) + add `tenant_id` to 10 business tables |
| `0049` | Add `content_hash` to `ai_analytics` for deduplication |
| `0065` | Add `user_credentials` (personal L2 vault, keyed by `users.id`) |
| `0066` | Drop `tenant_credentials` (app/bot config moves to the environment) |

Head migration: `0066` (verify with `alembic current`).
