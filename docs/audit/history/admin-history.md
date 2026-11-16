# Admin console implementation history

Historical source-pinned evidence and handoffs. These records do not certify the current checkout or deployment.

### Related-editor validation and local handoff

Task P8-T21. Implementation/test SHA: `6f1f695dec730396bda79043d19414fea13a2094`;
synchronized dev: `1e0e3a0c772c4a7bcc04862434c172d32288868e`. Other active
lanes #216/P10-T11 and #217/P10-T12 were inspected; their owners, reservations,
statuses and evidence were not changed or imported as accepted work.

Initial implementation sandbox evidence (owner remote exception; follow-up below):

```sh
/data/admin-relations-venv/bin/python -m pytest tests/test_admin_related.py \
  --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
NODE_PATH=/data/admin-relations-browser/node_modules node tests/admin_related_browser.cjs
```

- Pytest: exit 0, **20 passed, 0 failed/skipped**. Actual SQLAdmin routes and
  Jinja fragments over synthetic ORM metadata and explicit storage doubles;
  create/edit, ordinary redirects, validation/storage failures, grant and
  object-check denials, excluded/unregistered targets, token preservation,
  JSON escaping and custom-response preservation. No bound database engine.
- Chromium: exit 0, **32 assertions** at 390/1440px. Actual child-completion
  script, FK and m2m selection/rename, unsaved parent preservation, origin,
  source-window, identity/ID rejection, cancellation and popup blocker. Every
  request intercepted; no real application server or external requests.
- Separate venv: Python 3.13.14; SQLAdmin 0.32.0, SQLAlchemy 2.1.5,
  Starlette 1.9.0, WTForms 3.2.2, pytest 9.1.1, pytest-asyncio 1.4.0,
  httpx 0.28.1, greenlet 3.5.6 (manifest-compatible versions). Browser fixture
  uses a separate Playwright install and local Chromium; set `CHROMIUM_PATH`
  if Chromium is elsewhere. No owner `.venv`, `.env`, secrets or providers used.
- Earlier fixture attempts failed on an unbound synthetic read and a missing
  request `query_string`; corrected explicit doubles/request scope, not weakened
  assertions. These were not successful runs. Final counts above are complete.

Original owner-local acceptance checklist (historical plan; final bounded owner
acceptance is recorded below; the full checklist is not claimed as executed).
At the exact implementation SHA, run sequentially under R8/R10 in the owner's
prepared environment, without reset/migration/seed or real provider dispatch:

1. Exercise actual `LLMModel.provider` and `Source.platform` FK forms, then
   `AgentTask.sources` m2m with records in an existing approved test workspace.
   Verify create/edit persistence, unchanged other selections/unsaved fields,
   cancellation and independent child/parent transaction behavior.
2. Verify platform admin/operator create-only/edit-only/denied roles, revoked
   identity/grants between opening and saving, direct forged popup POSTs and
   object-level denials. Catalog permissions and hidden user-role assignment
   must remain restricted; workspace roles must not gain console access.
3. Check actual Select2, application CSP/browser headers, popup opener policy,
   specialized forms, nested windows, validation retry, back/refresh and no-JS.
   A restrictive opener policy may require falling back to ordinary CRUD.
4. Confirm existing workspace/source/scenario validators still reject invalid
   cross-workspace links and actual DB write/rollback failures do not report a
   success receipt. No new atomicity guarantee is made across two forms.

No migration, schema change, real DB/ASGI integration acceptance, full-suite,
whole-console security or deployment certification is claimed by this subset.


### Related-editor follow-up: inline icons and Float forms

The owner-local attempt reported HTTP 500 while opening `/admin/tenant/edit/1`
from a related field. This is failure evidence, not successful local acceptance.
The same failure was reproduced offline on the canonical Tenant/TenantAdmin:
SQLAdmin 0.32.0 could not convert `daily_cost_limit` (`sqlalchemy.sql.sqltypes.Float`)
with SQLAlchemy 2.1.5, where Float no longer inherits Numeric.

`BaseAdmin` now uses a converter for Float/FLOAT/REAL/Double/DOUBLE families;
exact Numeric/Decimal monetary fields retain SQLAdmin's DecimalField path.
This does not alter ORM types, stored values, validation boundaries or grants.
The regression loads the actual model and view declaration with isolated
configuration and a storage function that raises on use; both ordinary and
related create/edit form scaffolding therefore need no database.

Plus/pencil SVG actions now share a flex row with the native input or actual
Select2 container. Icon-only controls have 44px targets, accessible names,
tooltips and visible keyboard focus; the status remains below the field.
Select2 is moved with its original select and handlers, not recreated; scoped
height/padding prevents label clipping and chips follow the existing theme.

Follow-up evidence: **81 isolated pytest cases passed**, 0 failed/skipped, exit 0;
**228 offline Chromium assertions passed**, exit 0, across native + real Select2,
light/dark and 390/1440px. Checks include canonical Tenant related GETs, required
and nullable float parsing, unchanged precise Decimal, existing catalog grants,
parent preservation, popup protocol, inline geometry, icon-only labels, dropdown
operation, label bounds and no page overflow. Eight fixture screens were
individually inspected; this is not owner-local browser or DB acceptance.
Published source SHA is pinned in PR #219's current handoff. Initial evidence
above stays attributed to its original SHA and does not certify this follow-up.

```sh
/data/admin-relations-venv/bin/python -m pytest \
  tests/test_admin_form_compatibility.py tests/test_admin_related.py \
  tests/test_admin_shell_catalog.py tests/test_admin_all_role_records.py \
  --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
SQLADMIN_STATIC_DIR=/data/admin-relations-venv/lib/python3.13/site-packages/sqladmin/statics \
NODE_PATH=/data/admin-relations-browser/node_modules node tests/admin_related_browser.cjs
```

Same Python/SQLAdmin/SQLAlchemy/Starlette/WTForms versions as the initial run;
FastAPI 0.146.0 and itsdangerous 2.2.0 added to the separate sandbox environment
for the existing isolated catalog tests. Python 3.14 was not available/tested
remotely. No application/runtime, real DB, environment secrets, providers,
schema operation, dependency manifest change or delegated test execution.
Owner subsequently reported that the updated local UI works and explicitly
requested task closure and Ready/merge into dev. Accepted implementation:
`aec048db192bfc9842883502065646d5650f43d9`. This is owner-reported functional
smoke, not a remotely verified PostgreSQL/revocation/rollback/full-suite result.
The coordinator reviewed the exact implementation and reused the completed
81-test/228-assertion evidence; no runtime source changed for closeout. P8-T21
is accepted as a bounded feature; no whole-phase, security-gate, schema or
deployment acceptance is implied. Detailed original test limitations remain above.

### Known view/ORM configuration drift

The registered source still contains legacy configuration mismatches; this
reference must not describe them as working schema fields:

- `SourceAdmin.column_list` names `date_from` / `date_to`, while current `Source`
  declares neither column nor property (the columns were removed in migration
  0079). Its create/edit form no longer includes those fields.

These are source observations, not a claim that startup or a particular rendered
page was reproduced. These unrelated runtime/view fixes are outside the LLM model-test change. Real SQLAdmin/ASGI/PG role, revocation and page-render acceptance remains
outside the checks performed here; prior C/D limits stay in the delivered index.
