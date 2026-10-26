# Model deletion behavior

This is a source-level inventory of declared ORM and FK behavior, not an
inspection of a deployed PostgreSQL schema. It reports no live deletes and
changes no model, migration or cascade. Compare the target catalog before relying
on these declarations.

## Where the behavior is configured

- The child-side `ForeignKey(..., ondelete=...)` / `ForeignKeyConstraint` defines
  the database rule. `CASCADE` deletes child rows; `SET NULL` retains them with
  the FK cleared; `RESTRICT` refuses a referenced parent delete; omitted action
  is PostgreSQL `NO ACTION` (not an implicit cascade).
- SQLAlchemy `relationship(cascade=..., passive_deletes=...)` separately defines
  ORM unit-of-work behavior. `delete` propagates `Session.delete(parent)`;
  `delete-orphan` also deletes a child removed from its owning collection.
  `passive_deletes=True` leaves unloaded children to the database. It does NOT
  create an ON DELETE rule or turn a restrictive FK into a cascade.
- `TenantScopedMixin.tenant_id` supplies `ON DELETE CASCADE` to models that inherit
  it; explicit billing/evidence tables instead carry restrictive FKs.
- SQLAdmin 0.32 is pinned in `requirements.txt` and uses `session.delete()` and
  commits per selected object. A multi-select operation is not a guaranteed
  single all-or-nothing transaction: an earlier object may already be committed
  when a later object's FK prevents deletion.
- `BaseManager.delete_by_id()` uses ORM delete/flush. `QuerySet.delete()` issues
  a SQL DELETE and does not traverse ORM relationship cascades. Database FK rules
  apply to either path. Custom managers/hooks may add explicit cleanup; integer
  identifiers/JSON references without FKs never gain automatic cascades.
- Changing Python declarations does **not** change an existing database. Any
  change to database deletion policy needs a reviewed Alembic migration and
  PostgreSQL acceptance. Never edit already applied migrations.
- The installed schema can differ from these declarations, including trigger-based
  guards documented for LLM evidence/budget tables. Only a read-only catalog
  comparison can establish the deployed rules; metadata loading cannot.

## Important consequences and findings

1. Deleting a platform cascades to `sources`, then to `ai_analytics`,
   `analyzed_content_receipts`, `bot_actions`, `agent_task_sources` and
   `collection_seen`. Task-owned lane sources are also deleted with their owning
   task through `sources.owner_task_id`. `collection_candidates.source_id` instead
   uses SET NULL, so a discovered candidate survives its source. `collection_lane_runs`
   has no FK to its task/job, so its counts survive. The Settings Platforms tab
   deliberately offers create/edit/activate, not hard delete. Deactivation itself
   is not a delete and does not erase sources.
2. Deleting a user cascades to memberships (`tenant_users`), personal credentials,
   notification preferences and user-linked agent memory. Billing/accounting
   references, task payer consents and jobs initiated by that user may RESTRICT
   the whole delete. Disabling membership access is not deleting the account or
   its other spaces.
3. Deleting a workspace has many cascade descendants, but restrictive billing,
   dispatch and outcome evidence can prevent the delete. Cascade reachability
   is not a promise that the whole delete will succeed.
4. Deleting an LLM provider has ORM delete-orphan behavior and a database CASCADE
   to its models; bound-policy references to the provider or model can still
   refuse it. Model references in scenarios use SET NULL, not scenario deletion.
   Scenario/task deletion keeps relevant jobs and digest records with SET NULL,
   while scenario bot actions use CASCADE.
5. **Known mismatch, unchanged:** `ModelType.permissions` is delete-orphan with
   `passive_deletes=True`, but `Permission.model_type_id` has NO ACTION. Loaded
   ORM children can be traversed/deleted; unloaded children and direct SQL may
   refuse deletion. Permission/role association FKs are also NO ACTION. Resolve
   the desired resource/permission retirement policy explicitly before a schema
   change; do not infer a safe cascade from that relationship's comment.
6. `roles -> users` is NO ACTION with a non-nullable role FK; deleting an assigned
   role is not a supported way to remove users. ORM default relationship
   handling can attempt NULL and fail before the database checks the parent.
7. Examples of identifiers **without** FK cascades: `collected_items.source_id`
   and `run_id`; `collection_lane_runs.job_id` and `task_id`;
   `collection_candidates.platform` (a String, not a platform FK); execution/model
   reference integers in `llm_attempts`; notification `related_entity_id`. Do not
   promise source/job deletion clears those rows. Retention and explicit service
   cleanup remain separate policies.

### Raw administrator deletion bypasses source admission

Source review also identifies a lifecycle gap outside the FK inventory:
`SourceAdmin` and `PlatformAdmin` in `app/admin/views.py` inherit the generic
SQLAdmin delete path; neither those views nor `BaseAdmin` forwards deletion to
`app/services/collection/source_deletion.py`. A platform's database cascade does
not invoke that Python service either. Those routes therefore bypass its
source-admission lock and active-task refusal policy. Removing the final named
source can leave an active task with no links, which the runtime interprets as
all active workspace sources.

This is a source-confirmed bypass, not a new PostgreSQL/ASGI reproduction or a
claim that every deployed FK matches the declarations. The new Settings tab
offers no hard delete, but it does not repair the existing raw admin routes.
A separate lifecycle fix must either route administrator/parent deletions
through the admission policy or disallow the bypassing operations. No cascade,
model, schema or deletion behavior is changed by this audit.

## Complete FK inventory

Each row describes **child → parent**. Counts are per constraint, not per column:
39 CASCADE, 17 SET NULL, 46 RESTRICT, 4 NO ACTION; 106 total. Composite constraints
appear once. Tables without FKs are included. Exact declarations remain in
`app/models/<domain>/`; inherited tenant FKs originate in `app/models/base.py`.

| Child table | Foreign keys and ON DELETE action |
| --- | --- |
| `agent_feedback` | `message_id` → `agent_messages(id)` — SET NULL<br />`session_id` → `agent_sessions(id)` — CASCADE<br />`tenant_id` → `tenants(id)` — CASCADE |
| `agent_memory` | `evidence_message_id` → `agent_messages(id)` — SET NULL<br />`tenant_id` → `tenants(id)` — CASCADE<br />`user_id` → `users(id)` — CASCADE |
| `agent_messages` | `tenant_id` → `tenants(id)` — CASCADE<br />`session_id` → `agent_sessions(id)` — CASCADE |
| `agent_scenarios` | `image_llm_model_id` → `llm_models(id)` — SET NULL<br />`tenant_id` → `tenants(id)` — CASCADE<br />`text_llm_model_id` → `llm_models(id)` — SET NULL<br />`video_llm_model_id` → `llm_models(id)` — SET NULL |
| `agent_sessions` | `tenant_id` → `tenants(id)` — CASCADE |
| `agent_task_sources` | `source_id` → `sources(id)` — CASCADE<br />`agent_task_id` → `agent_tasks(id)` — CASCADE |
| `agent_tasks` | `tenant_id` → `tenants(id)` — CASCADE<br />`agent_scenario_id` → `agent_scenarios(id)` — SET NULL |
| `ai_analytics` | `parent_analysis_id` → `ai_analytics(id)` — SET NULL<br />`tenant_id` → `tenants(id)` — CASCADE<br />`source_id` → `sources(id)` — CASCADE |
| `analyzed_content_receipts` | `scenario_id` → `agent_scenarios(id)` — SET NULL<br />`source_id` → `sources(id)` — CASCADE<br />`analytics_id` → `ai_analytics(id)` — SET NULL<br />`tenant_id` → `tenants(id)` — CASCADE |
| `bot_actions` | `source_id` → `sources(id)` — CASCADE<br />`agent_scenario_id` → `agent_scenarios(id)` — CASCADE<br />`tenant_id` → `tenants(id)` — CASCADE<br />`analytics_id` → `ai_analytics(id)` — SET NULL<br />`agent_task_id` → `agent_tasks(id)` — SET NULL<br />`confirmed_by` → `tenant_users(id)` — SET NULL |
| `collected_items` | `tenant_id` → `tenants(id)` — CASCADE |
| `collection_candidates` | `tenant_id` → `tenants(id)` — CASCADE<br />`source_id` → `sources(id)` — SET NULL |
| `collection_lane_runs` | `tenant_id` → `tenants(id)` — CASCADE |
| `collection_seen` | `tenant_id` → `tenants(id)` — CASCADE<br />`source_id` → `sources(id)` — CASCADE |
| `digest_runs` | `tenant_id` → `tenants(id)` — CASCADE<br />`agent_task_id` → `agent_tasks(id)` — SET NULL |
| `jobs` | `tenant_id` → `tenants(id)` — CASCADE<br />`initiated_by_user_id` → `users(id)` — RESTRICT<br />`agent_task_id` → `agent_tasks(id)` — SET NULL |
| `llm_attempts` | `tenant_id` → `tenants(id)` — CASCADE |
| `llm_budget_days` | `tenant_id` → `tenants(id)` — RESTRICT |
| `llm_budget_reservations` | `dispatch_id` → `llm_dispatch_receipts(dispatch_id)` — RESTRICT<br />`tenant_id, utc_day` → `llm_budget_days(tenant_id, utc_day)` — RESTRICT<br />`tenant_id` → `tenants(id)` — RESTRICT |
| `llm_dispatch_receipts` | `tenant_id` → `tenants(id)` — RESTRICT |
| `llm_models` | `provider_id` → `llm_providers(id)` — CASCADE |
| `llm_outcome_events` | `tenant_id` → `tenants(id)` — RESTRICT<br />`dispatch_id` → `llm_dispatch_receipts(dispatch_id)` — RESTRICT |
| `llm_providers` | None |
| `model_types` | None |
| `notification_preferences` | `user_id` → `users(id)` — CASCADE |
| `notifications` | `tenant_id` → `tenants(id)` — CASCADE |
| `permissions` | `model_type_id` → `model_types(id)` — NO ACTION |
| `personal_billing_accounts` | `user_id` → `users(id)` — RESTRICT |
| `personal_billing_cutovers` | `account_id` → `personal_billing_accounts(id)` — RESTRICT<br />`reviewed_by_user_id` → `users(id)` — RESTRICT |
| `personal_legacy_attribution_consents` | `account_id` → `personal_billing_accounts(id)` — RESTRICT<br />`attempt_key` → `llm_attempts(attempt_key)` — RESTRICT<br />`tenant_id` → `tenants(id)` — RESTRICT<br />`consented_by_user_id` → `users(id)` — RESTRICT<br />`dispatch_id` → `llm_dispatch_receipts(dispatch_id)` — RESTRICT |
| `personal_legacy_attributions` | `account_id` → `personal_billing_accounts(id)` — RESTRICT<br />`attempt_key` → `llm_attempts(attempt_key)` — RESTRICT<br />`consented_by_user_id` → `users(id)` — RESTRICT<br />`consent_id` → `personal_legacy_attribution_consents(id)` — RESTRICT<br />`tenant_id` → `tenants(id)` — RESTRICT<br />`reviewed_by_user_id` → `users(id)` — RESTRICT<br />`dispatch_id` → `llm_dispatch_receipts(dispatch_id)` — RESTRICT |
| `personal_llm_billing_incidents` | `dispatch_id` → `personal_llm_reservations(dispatch_id)` — RESTRICT<br />`account_id` → `personal_billing_accounts(id)` — RESTRICT<br />`event_id` → `llm_outcome_events(event_id)` — RESTRICT |
| `personal_llm_bound_policies` | `model_row_id` → `llm_models(id)` — RESTRICT<br />`reviewed_by_user_id` → `users(id)` — RESTRICT<br />`provider_row_id` → `llm_providers(id)` — RESTRICT |
| `personal_llm_budget_days` | `account_id` → `personal_billing_accounts(id)` — RESTRICT |
| `personal_llm_dispatches` | `account_id` → `personal_billing_accounts(id)` — RESTRICT<br />`tenant_id` → `tenants(id)` — RESTRICT<br />`payer_user_id` → `users(id)` — RESTRICT<br />`initiated_by_user_id` → `users(id)` — RESTRICT |
| `personal_llm_ledger_entries` | `dispatch_id` → `personal_llm_reservations(dispatch_id)` — RESTRICT<br />`account_id, utc_day` → `personal_llm_budget_days(account_id, utc_day)` — RESTRICT<br />`event_id` → `llm_outcome_events(event_id)` — RESTRICT |
| `personal_llm_reservation_states` | `account_id, utc_day` → `personal_llm_budget_days(account_id, utc_day)` — RESTRICT<br />`account_id, dispatch_id, utc_day` → `personal_llm_reservations(account_id, dispatch_id, utc_day)` — RESTRICT<br />`latest_event_id` → `llm_outcome_events(event_id)` — RESTRICT<br />`dispatch_id` → `personal_llm_reservations(dispatch_id)` — RESTRICT |
| `personal_llm_reservations` | `account_id, utc_day` → `personal_llm_budget_days(account_id, utc_day)` — RESTRICT<br />`dispatch_id` → `personal_llm_dispatches(dispatch_id)` — RESTRICT |
| `platforms` | None |
| `role_permission` | `permission_id` → `permissions(id)` — NO ACTION<br />`role_id` → `roles(id)` — NO ACTION |
| `roles` | None |
| `sources` | `owner_task_id` → `agent_tasks(id)` — CASCADE<br />`platform_id` → `platforms(id)` — CASCADE<br />`tenant_id` → `tenants(id)` — CASCADE |
| `task_billing_payers` | `payer_user_id` → `users(id)` — RESTRICT<br />`consented_by_user_id` → `users(id)` — RESTRICT<br />`tenant_id` → `tenants(id)` — RESTRICT |
| `tenant_channels` | `tenant_id` → `tenants(id)` — CASCADE |
| `tenant_invites` | `role_id` → `workspace_roles(id)` — SET NULL<br />`tenant_id` → `tenants(id)` — CASCADE |
| `tenant_users` | `role_id` → `workspace_roles(id)` — SET NULL<br />`tenant_id` → `tenants(id)` — CASCADE<br />`user_id` → `users(id)` — CASCADE |
| `tenants` | None |
| `user_credentials` | `user_id` → `users(id)` — CASCADE |
| `users` | `role_id` → `roles(id)` — NO ACTION |
| `workspace_role_permission` | `permission_id` → `permissions(id)` — CASCADE<br />`role_id` → `workspace_roles(id)` — CASCADE |
| `workspace_roles` | None |

## ORM relationships with deletion behavior

| Parent relationship | ORM cascade | passive_deletes |
| --- | --- | --- |
| `AIAnalytics.parent` | merge, save-update | True |
| `AIAnalytics.children` | merge, save-update | True |
| `LLMProvider.models` | delete, delete-orphan, expunge, merge, refresh-expire, save-update | False |
| `ModelType.permissions` | delete, delete-orphan, expunge, merge, refresh-expire, save-update | True |
| `Platform.sources` | delete, delete-orphan, expunge, merge, refresh-expire, save-update | True |
| `Source.analytics` | delete, delete-orphan, expunge, merge, refresh-expire, save-update | True |

## Validation and remaining PostgreSQL acceptance

Historical source pins, checks and the detailed destructive-path acceptance
plan are in the [model deletion audit record](audit/model-deletion-history.md).
This inventory alone does not establish deployed FK behavior; read-only catalog
comparison and controlled test-schema acceptance are required before a policy
change.
