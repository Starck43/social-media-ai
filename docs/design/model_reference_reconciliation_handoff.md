# Model reference reconciliation: status and continuation

Prepared 2026-10-09 on `ai/docs-model-reference-reconciliation` from dev `234a23dd7d1d2ce8dd3813f1ed63c7c7ef748669`. Documentation-only, not merged/deployed; no ORM or migration edits.

## Source review and corrections

Manually inspected all mapped-model source files under app/models (including the four workspace models in tenant.py and the two association tables), base.py, BaseManager, and revisions 0086/0087. Repository listings establish file presence, not the full deployed schema. Replaced the stale field-by-field reference with source-linked inventories for all 27 tables and three FK-only Mermaid ER views.

Corrected:
- Base has no generic instance save/delete; tenant FK ownership does not always mean TenantScopedMixin, and raw SQL/bypass/association paths need explicit guards.
- No source.user_id or direct source/scenario FK; correct source uniqueness excludes user_id.
- role_permission actual association name; model_types are application metadata, not LLM capability enumeration; permissions.model_type is a relationship.
- Platform rate-limit/timestamp columns were phantom fields; corrected real column lengths.
- Starter/pro/business with default pro, chat overrides and explicit membership/invite ownership.
- Added previously omitted collected_items, its attempts/dedup index and deliberate soft source/run references.
- Digest Date bounds, string status, period/channel/content/error/message fields and nullable delivery_state; removed phantom results column.
- Job started_at/finished_at and string status; message tool_name rather than tool_outputs; global session chat uniqueness.
- Feedback voter attribution and string vote; memory scope length; full action audit fields confirmed_by/task/analytics/result/dry_run rather than approved_by.
- Clarified analytics cents versus USD cost columns and NULL/unknown; capability/default routing is application behavior, not DB uniqueness.
- Latest inspected revision 0087 -> 0086 -> 0085; no deployed migration or retry activation claim.

## Checks and limits

Local structural checks passed: 27 expected table headings, three balanced Mermaid blocks, no trailing whitespace, corrected-field/validation-limit markers. Actual source reconciliation was manual, not an automated ORM-column comparison. Mermaid rendering, full relative-link crawler, ORM import, alembic heads/check/current, database introspection and application tests did NOT run. No production operations, secrets or live API calls.

Field inventories link exact declarations; no exhaustive audit of every historical migration or all API/client/manager behavior is claimed. Historical test evidence in other documents is preserved. Current delivery status remains owned by PR #12; do not overwrite its shared checklist.

## Integration with documentation PR #13

This branch is from dev, not stacked on #13. MODELS.md already exists in the index, so no shared-index rewrite is needed. After both docs PRs merge, reconcile DOCUMENTATION_GUIDE's MODELS correction items to this evidence: ORM inventories/ER and stale head wording are corrected, but Alembic graph/drift/deployment checks remain unrun. Do not mark unrelated root LLM guidance or historical deployment wording fixed; those need a separate client/reference pass.

## Continue

Recheck fresh dev and active PRs, especially #12 changes to DigestRun factory/binding versus actual schema. Review the GitHub-rendered ER views and new inventories; merge only on owner instruction. Next reference unit: inspect actual LLM client formats/routing and fix stale AGENTS paths/accessor/status prose without weakening operational rules. No production migration is authorized by documentation review.
