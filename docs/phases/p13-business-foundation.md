# P13 — Business-module foundation

[Product direction](../PRODUCT_PLAN.md#business-modules) · [Adapted specification and review](../BUSINESS_MODULES.md) · [ROADMAP](../../ROADMAP.md#p13)

**Phase status: PLANNED** (registered by [P13-T01](../tasks/p13-t01.md); ROADMAP owns task status).
Owner estimate (TZ): Spread across modules (no separate TZ estimate) — not a repository commitment.

**Goal:** Deliver the shared requirements of the owner's TZ once — protected personal data, contact core, outbound rate limits, approval workflow for writes, audit codes, API/OpenAPI conventions, OAuth framework and an enforceable 80% coverage gate — so modules P14–P19 reuse them instead of re-implementing them.

## Baseline: done and arriving

- In `dev`: Fernet credential vault (`app/utils/crypto.py`), workspace connections with separately encrypted secrets (P12-T08, 0121), provider catalog (`app/integrations/catalog/`), action audit (P11-T12), P9 capability activation pattern, collection rate-limit policy, FastAPI OpenAPI + generated route tables, tenant enforcement in managers.
- Arriving via open PRs: P12-T14 credential security and connector authorizations ([PR #308](https://github.com/Starck43/social-media-ai/pull/308)) — [P13-T02](../tasks/p13-t02.md) and [P13-T08](../tasks/p13-t08.md) start after it is integrated.
- Missing: field-level PII encryption with lookup, a business contact model, shared outbound budgets, a generic write-command ledger, business audit codes, per-package coverage gate, OAuth for business providers.

## Adapted requirements

- Shared requirements table and review findings: [BUSINESS_MODULES](../BUSINESS_MODULES.md#shared-requirements).
- Every later module consumes: `app/core/pii.py` ([P13-T02](../tasks/p13-t02.md)), `contacts` ([P13-T07](../tasks/p13-t07.md)), `app/integrations/ratelimit` ([P13-T03](../tasks/p13-t03.md)), `app/services/commands` ([P13-T04](../tasks/p13-t04.md)), audit codes and API conventions ([P13-T05](../tasks/p13-t05.md)), coverage script and mocks ([P13-T06](../tasks/p13-t06.md)), OAuth framework ([P13-T08](../tasks/p13-t08.md)).

## Data model (adapted)

- `contacts`, `contact_identities` (encrypted value + blind index), `contact_consents`, `contact_merge_journal` ([P13-T07](../tasks/p13-t07.md)).
- `provider_rate_budgets` ([P13-T03](../tasks/p13-t03.md)).
- `business_commands`, `business_command_targets` ([P13-T04](../tasks/p13-t04.md)).

Conventions: integer keys, `TenantScopedMixin`, `settings.DB_SCHEMA`, Alembic revision = next free after rebasing on `dev` (R-01, R-20).

## API (adapted)

- `/api/v1/contacts` (read), `/api/v1/commands` (preview/approve/reject/execute/status).
- OAuth start/callback routes under `/app/settings` connections.

## Acceptance

- Each task's acceptance plus: no new module may store personal data in plaintext columns or send/write externally without a [P13-T04](../tasks/p13-t04.md) command.
- Coverage ≥ 80% per new package (TZ), OpenAPI contract test green, audit rows without personal data.

## Plan

1. [P13-T01](../tasks/p13-t01.md) — Business-module plan registration and TZ review · `P2` · `C2` · depends: —
2. [P13-T02](../tasks/p13-t02.md) — Protected personal-data fields: Fernet encryption, blind index and log redaction · `P2` · `C4` · depends: P12-T14 integrated ([PR #308](https://github.com/Starck43/social-media-ai/pull/308)) — shares credential/key handling
3. [P13-T03](../tasks/p13-t03.md) — Shared external-API rate limiter and retry policy · `P2` · `C3` · depends: —
4. [P13-T04](../tasks/p13-t04.md) — Business write-approval workflow and command ledger · `P2` · `C4` · depends: [P13-T05](../tasks/p13-t05.md) audit codes (can proceed in parallel; integrate before READY)
5. [P13-T05](../tasks/p13-t05.md) — Audit codes, business API conventions and OpenAPI contract · `P2` · `C3` · depends: —
6. [P13-T06](../tasks/p13-t06.md) — Per-package coverage gate and mock-provider test kit · `P2` · `C2` · depends: —
7. [P13-T07](../tasks/p13-t07.md) — Contact core: contacts, identities and consent · `P2` · `C4` · depends: [P13-T02](../tasks/p13-t02.md) (encrypted fields, blind index)
8. [P13-T08](../tasks/p13-t08.md) — OAuth 2.0 workspace-connection framework (authorization code + refresh) · `P2` · `C4` · depends: P12-T14 integrated ([PR #308](https://github.com/Starck43/social-media-ai/pull/308): connector authorizations), [P13-T03](../tasks/p13-t03.md)

## Dependencies and gates

[P13-T02](../tasks/p13-t02.md)/T08 wait for P12-T14 integration. [P13-T03](../tasks/p13-t03.md)/T05/T06 can start immediately; [P13-T04](../tasks/p13-t04.md) and [P13-T07](../tasks/p13-t07.md) after T05/T02 respectively.

## Execution waves and agent assignment (P13–P19)

Owner-requested dispatch plan (2026-11-03, [dialogue](https://app.notion.com/chat?t=40d2b17295a0801ba40b00a96c95595f)).
It sets order and lane only; ROADMAP keeps status/owner and a claim is still
required. A wave starts when its trigger holds, not on a date.

**Lanes.** *Remote* = Git/PR execution without real PostgreSQL; `test_schema`
checks for its PR come from the local helper as a PR comment
([remote → local DB help](../PROMPT_EXAMPLES.md#remote-work--local-db-help--audit)).
*Local* = tasks up to `C3`, tasks with migrations or live provider/sandbox
evidence, and DB validation of remote PRs. Owner items are decisions in [Q-18](../tasks/q-18.md).

### Wave 0 — while PRs #307/#308/#311/#327 remain open

| Lane | Task | Why now |
| --- | --- | --- |
| Owner / integrator | Merge planning PR #341 to `dev` before task claims | Planning only; P13-T01 stays IN_PROGRESS until its required link check passes and it is independently accepted |
| Remote | P13-T06 coverage gate and provider mocks | New files only (`scripts/ci/`, `tests/shared/`) |
| Remote | P18-T01 local connector protocol (docs) | No code; reads P12-T12 contracts from the PR #327 branch until merged |
| Remote | P13-T05 audit codes and API conventions | No migration; rebase after #308 (both touch `app/api/v1`) |
| Owner | D1, D5, D8 | D8 gates P13-T02, D5 gates P17-T01, D1 confirms bands |

### Wave 1 — trigger: #308 and #327 merged, migration head stable

| Lane | Task | Note |
| --- | --- | --- |
| Local | P13-T03 rate limiter (`C3`) | First migration in the business queue |
| Remote | P13-T02 PII protection (`C4`) | No migration; needs #308 and D8 |

### Wave 2 — foundation core

| Lane | Task | Depends on |
| --- | --- | --- |
| Remote + local DB check | P13-T04 command ledger (`C4`) | P13-T05; second migration |
| Remote + local DB check | P13-T07 contact core (`C4`) | P13-T02; third migration |
| Remote | P13-T08 OAuth framework (`C4`) | #308, P13-T03; no migration |
| Remote | P18-T03 desktop connector core (`C5`) | accepted P18-T01; separate `local_connector/` package |
| Local | P17-T01 1C connection (`C3`) | P13-T03, D5 |

### Wave 3 — first modules

| Lane | Task | Depends on |
| --- | --- | --- |
| Local | P14-T01 Bitrix24 connection (`C3`) | P13-T08, D2; live sandbox evidence is local |
| Remote + local DB check | P14-T02 Bitrix24 sync (`C4`) | P14-T01, P13-T07 |
| Local | P14-T03, P14-T04 (`C3`) | P14-T02; T04 has a migration |
| Remote | P17-T02 → P17-T03 (`C4`) | chain; T03 has a migration |
| Local | P17-T05 (`C3`) | P17-T03 |
| Remote | P18-T02, P18-T04, P18-T05 (`C4`) | P18-T02 after P18-T01 + P13-T05 (+ #327); T04/T05 chain after P18-T03 |

### Wave 4 — `P3` modules

| Lane | Tasks |
| --- | --- |
| Remote | P15-T01…T06, P16-T01, P16-T03, P16-T04, P17-T04, P19-T02, P19-T04, P14-T06 |
| Local | P15-T07, P15-T08, P16-T02, P16-T05, P19-T01 (changes existing 0122 data), P19-T03, P19-T05, P19-T06, P14-T05, P18-T06 |
| Local / owner | P18-T07 packaging and signing (certificates, three OS) |

Within a wave follow each card's dependencies; P15-T05/T06 must precede
P14-T05/T06 and P19-T05.

### Merge and conflict rules

1. **One migration-bearing PR in the merge queue at a time**, in the order
   P13-T03 → P13-T04 → P13-T07 → P14-T02 → later ones. Take the revision number at
   the pre-merge rebase (R-20); the local lane validates upgrade → downgrade →
   re-upgrade on `test_schema` before READY.
2. **Shared files have one active writer at a time:** `app/core/config.py`
   (P13-T02, P13-T03), `app/integrations/catalog/__init__.py` (P13-T08, P14-T01,
   P17-T01), the audit-code allowlist (P13-T05 first), navigation/router
   registration, and `docs/generated/api-routes.md` (regenerate only at rebase).
3. **Concurrency:** about 3–4 remote tasks and 1 local task at once, plus the
   local helper role for DB checks of remote PRs.
