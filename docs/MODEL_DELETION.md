# Model deletion behavior

Source audit baseline: dev `213980a4955e034894f6552234708d402babc43c`.
All Python files under `app/models` were parsed; the canonical facade registered
45 mappers and 48 tables (45 model tables + 3 associations). Compatibility
modules do not add tables. This is a **source/metadata audit**, not an inspection
of the deployed PostgreSQL schema. No database, migration or live delete ran.

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
- SQLAdmin 0.32 uses `session.delete()` and commits per selected object, through
  `sqladmin._queries.Query._delete_async/_delete_sync`. A multi-select operation
  is not a guaranteed single all-or-nothing transaction: an earlier object may
  already be committed when a later object's FK prevents deletion.
- `BaseManager.delete_by_id()` uses ORM delete/flush. `QuerySet.delete()` issues
  a SQL DELETE and does not traverse ORM relationship cascades. Database FK rules
  apply to either path. Custom managers/hooks may add explicit cleanup; integer
  identifiers/JSON references without FKs never gain automatic cascades.
- Changing Python declarations does **not** change an existing database. Any
  change to database deletion policy needs a reviewed Alembic migration and
  PostgreSQL acceptance. Never edit already applied migrations.

## Important consequences and findings

1. Deleting a platform cascades to `sources`, then to `ai_analytics`,
   `analyzed_content_receipts`, `bot_actions` and task/source association rows.
   The new Settings Platforms tab deliberately offers create/edit/activate, not
   hard delete. Deactivation itself is not a delete and does not erase sources.
2. Deleting a user cascades to memberships (`tenant_users`), personal credentials,
   notification preferences and user-linked agent memory. Billing/accounting
   references and manually initiated jobs may RESTRICT the whole delete.
   Disabling membership access is not deleting the account or its other spaces.
3. Deleting a workspace has many cascade descendants, but restrictive billing,
   dispatch and outcome evidence can prevent the delete. Cascade reachability
   is not a promise that the whole delete will succeed.
4. Deleting an LLM provider has ORM delete-orphan behavior and a database CASCADE
   to its models; billing references can still refuse it. Model references in
   scenarios use SET NULL, not scenario deletion. Scenario/task deletion keeps
   relevant jobs and digest records with SET NULL, while scenario bot actions
   use CASCADE.
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
   and `run_id`; execution/model reference integers in `llm_attempts`;
   notification `related_entity_id`. Do not promise source/job deletion clears
   those rows. Retention and explicit service cleanup remain separate policies.

## Complete FK inventory

Each row describes **child → parent**. Counts are per constraint, not per column:
34 CASCADE, 16 SET NULL, 46 RESTRICT, 4 NO ACTION; 100 total. Composite constraints
appear once. Tables without FKs are included. Exact declarations remain in
`app/models/<domain>/`; inherited tenant FKs originate in `app/models/base.py`.

| Child table | Foreign keys and ON DELETE action |
| --- | --- |
| `agent_feedback` | `message_id` → `agent_messages(id)` — SET NULL<br />`session_id` → `agent_sessions(id)` — CASCADE<br />`tenant_id` → `tenants(id)` — CASCADE |
| `agent_memory` | `evidence_message_id` → `agent_messages(id)` — SET NULL<br />`tenant_id` → `tenants(id)` — CASCADE<br />`user_id` → `users(id)` — CASCADE |
| `agent_messages` | `session_id` → `agent_sessions(id)` — CASCADE<br />`tenant_id` → `tenants(id)` — CASCADE |
| `agent_scenarios` | `image_llm_model_id` → `llm_models(id)` — SET NULL<br />`tenant_id` → `tenants(id)` — CASCADE<br />`text_llm_model_id` → `llm_models(id)` — SET NULL<br />`video_llm_model_id` → `llm_models(id)` — SET NULL |
| `agent_sessions` | `tenant_id` → `tenants(id)` — CASCADE |
| `agent_task_sources` | `agent_task_id` → `agent_tasks(id)` — CASCADE<br />`source_id` → `sources(id)` — CASCADE |
| `agent_tasks` | `agent_scenario_id` → `agent_scenarios(id)` — SET NULL<br />`tenant_id` → `tenants(id)` — CASCADE |
| `ai_analytics` | `parent_analysis_id` → `ai_analytics(id)` — SET NULL<br />`source_id` → `sources(id)` — CASCADE<br />`tenant_id` → `tenants(id)` — CASCADE |
| `analyzed_content_receipts` | `analytics_id` → `ai_analytics(id)` — SET NULL<br />`scenario_id` → `agent_scenarios(id)` — SET NULL<br />`source_id` → `sources(id)` — CASCADE<br />`tenant_id` → `tenants(id)` — CASCADE |
| `bot_actions` | `agent_scenario_id` → `agent_scenarios(id)` — CASCADE<br />`agent_task_id` → `agent_tasks(id)` — SET NULL<br />`analytics_id` → `ai_analytics(id)` — SET NULL<br />`confirmed_by` → `tenant_users(id)` — SET NULL<br />`source_id` → `sources(id)` — CASCADE<br />`tenant_id` → `tenants(id)` — CASCADE |
| `collected_items` | `tenant_id` → `tenants(id)` — CASCADE |
| `digest_runs` | `agent_task_id` → `agent_tasks(id)` — SET NULL<br />`tenant_id` → `tenants(id)` — CASCADE |
| `jobs` | `agent_task_id` → `agent_tasks(id)` — SET NULL<br />`initiated_by_user_id` → `users(id)` — RESTRICT<br />`tenant_id` → `tenants(id)` — CASCADE |
| `llm_attempts` | `tenant_id` → `tenants(id)` — CASCADE |
| `llm_budget_days` | `tenant_id` → `tenants(id)` — RESTRICT |
| `llm_budget_reservations` | `dispatch_id` → `llm_dispatch_receipts(dispatch_id)` — RESTRICT<br />`tenant_id` → `tenants(id)` — RESTRICT<br />`tenant_id,utc_day` → `llm_budget_days(tenant_id,utc_day)` — RESTRICT |
| `llm_dispatch_receipts` | `tenant_id` → `tenants(id)` — RESTRICT |
| `llm_models` | `provider_id` → `llm_providers(id)` — CASCADE |
| `llm_outcome_events` | `dispatch_id` → `llm_dispatch_receipts(dispatch_id)` — RESTRICT<br />`tenant_id` → `tenants(id)` — RESTRICT |
| `llm_providers` | None |
| `model_types` | None |
| `notification_preferences` | `user_id` → `users(id)` — CASCADE |
| `notifications` | `tenant_id` → `tenants(id)` — CASCADE |
| `permissions` | `model_type_id` → `model_types(id)` — NO ACTION |
| `personal_billing_accounts` | `user_id` → `users(id)` — RESTRICT |
| `personal_billing_cutovers` | `account_id` → `personal_billing_accounts(id)` — RESTRICT<br />`reviewed_by_user_id` → `users(id)` — RESTRICT |
| `personal_legacy_attribution_consents` | `account_id` → `personal_billing_accounts(id)` — RESTRICT<br />`attempt_key` → `llm_attempts(attempt_key)` — RESTRICT<br />`consented_by_user_id` → `users(id)` — RESTRICT<br />`dispatch_id` → `llm_dispatch_receipts(dispatch_id)` — RESTRICT<br />`tenant_id` → `tenants(id)` — RESTRICT |
| `personal_legacy_attributions` | `account_id` → `personal_billing_accounts(id)` — RESTRICT<br />`attempt_key` → `llm_attempts(attempt_key)` — RESTRICT<br />`consent_id` → `personal_legacy_attribution_consents(id)` — RESTRICT<br />`consented_by_user_id` → `users(id)` — RESTRICT<br />`dispatch_id` → `llm_dispatch_receipts(dispatch_id)` — RESTRICT<br />`reviewed_by_user_id` → `users(id)` — RESTRICT<br />`tenant_id` → `tenants(id)` — RESTRICT |
| `personal_llm_billing_incidents` | `account_id` → `personal_billing_accounts(id)` — RESTRICT<br />`dispatch_id` → `personal_llm_reservations(dispatch_id)` — RESTRICT<br />`event_id` → `llm_outcome_events(event_id)` — RESTRICT |
| `personal_llm_bound_policies` | `model_row_id` → `llm_models(id)` — RESTRICT<br />`provider_row_id` → `llm_providers(id)` — RESTRICT<br />`reviewed_by_user_id` → `users(id)` — RESTRICT |
| `personal_llm_budget_days` | `account_id` → `personal_billing_accounts(id)` — RESTRICT |
| `personal_llm_dispatches` | `account_id` → `personal_billing_accounts(id)` — RESTRICT<br />`initiated_by_user_id` → `users(id)` — RESTRICT<br />`payer_user_id` → `users(id)` — RESTRICT<br />`tenant_id` → `tenants(id)` — RESTRICT |
| `personal_llm_ledger_entries` | `account_id,utc_day` → `personal_llm_budget_days(account_id,utc_day)` — RESTRICT<br />`dispatch_id` → `personal_llm_reservations(dispatch_id)` — RESTRICT<br />`event_id` → `llm_outcome_events(event_id)` — RESTRICT |
| `personal_llm_reservation_states` | `account_id,utc_day` → `personal_llm_budget_days(account_id,utc_day)` — RESTRICT<br />`dispatch_id` → `personal_llm_reservations(dispatch_id)` — RESTRICT<br />`dispatch_id,account_id,utc_day` → `personal_llm_reservations(dispatch_id,account_id,utc_day)` — RESTRICT<br />`latest_event_id` → `llm_outcome_events(event_id)` — RESTRICT |
| `personal_llm_reservations` | `account_id,utc_day` → `personal_llm_budget_days(account_id,utc_day)` — RESTRICT<br />`dispatch_id` → `personal_llm_dispatches(dispatch_id)` — RESTRICT |
| `platforms` | None |
| `role_permission` | `permission_id` → `permissions(id)` — NO ACTION<br />`role_id` → `roles(id)` — NO ACTION |
| `roles` | None |
| `sources` | `platform_id` → `platforms(id)` — CASCADE<br />`tenant_id` → `tenants(id)` — CASCADE |
| `task_billing_payers` | `consented_by_user_id` → `users(id)` — RESTRICT<br />`payer_user_id` → `users(id)` — RESTRICT<br />`tenant_id` → `tenants(id)` — RESTRICT |
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

`tests/model_deletion_inventory.py` loads the actual canonical declarations in
an isolated process, replaces settings constants and skips manager binding only;
engine/manager imports are blocked. All mappers configure and every table's DDL
compiles for PostgreSQL. Tests verify all FK targets, SET NULL nullability,
constraint counts and real loaded-child ORM cascade traversal for the four
owning relationships. This does NOT execute a DELETE or prove a deployed FK.

The owner-local sequential test route must still verify:

- Compare installed PostgreSQL constraints and current Alembic revision with the
  declarations (read-only first; no automatic migration/reset).
- ORM deletes with children loaded AND unloaded, plus direct/bulk SQL deletes.
- Platform → source → analytics/actions/receipts, user → membership/credentials,
  workspace descendants, scenario/task SET NULL and provider/model deletion.
- RESTRICT cases with billing/ledger/dispatch evidence and initiated jobs;
  transaction rollback leaves parent and all protected children intact.
- ModelType/permission association mismatch and assigned-role refusal.
- SQLAdmin multi-select partial-commit/error behavior on a controlled test fixture.
- Real authenticated/CSRF Settings Platforms create/update, uniqueness conflicts,
  admin-without-workspace, operator/member refusal and no cross-tenant side effects.

Run these only against the existing approved local test target through the
repository's sequential primary test runner. No production deletion, reset,
new schema, migration or deployment is authorized by this report.
