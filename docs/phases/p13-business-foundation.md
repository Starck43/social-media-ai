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
