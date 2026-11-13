# P17 — 1C:Enterprise integration (TZ module 4)

[Product direction](../product/PRODUCT_PLAN.md#business-modules) · [Adapted specification and review](../product/BUSINESS_MODULES.md) · [ROADMAP](../../ROADMAP.md#p17)

**Phase status: PLANNED** (registered by [P13-T01 accepted](../audit/p13/p13-t01.md); ROADMAP owns task status).
Owner estimate (TZ): 4–5 weeks — not a repository commitment.

**Goal:** Read 1C:Enterprise 8.3+ catalogs, documents and registers through a typed, allowlisted query layer, answer natural-language questions with verified tables and exports, and execute approved write commands with full audit.

## Baseline: done and arriving

- In `dev`: `PlatformType.ONEC` and the `ONEC` catalog descriptor (OData login/password), deterministic Decimal comparison in cases (P12-T05). No adapter.
- Independent of P14–P16; can run in parallel with P18 after P13.

## Adapted requirements

TZ 4.1–4.5, adapted:

- **Connections:** OData standard interface or published HTTP services, basic auth (OAuth if available), encrypted credentials, health check, several connections per workspace; network path per D5 (R-07).
- **Read-only extraction:** catalogs (items, counterparties, warehouses), documents (waybills, invoices, acts), registers (balances, cash flows), filters, pagination, ≤ 1000 rows per request.
- **NL interface:** typed query plan, never raw query text (R-07); tables/charts; Excel/PDF export; history.
- **Commands:** create/post documents, update catalogs; preview; approval for every write; audit.
- **Security:** read-only default, explicit approval, audit, RBAC, 30 s timeout, 10 req/min.

## Data model (adapted)

`onec_connections` → `workspace_connections` + secrets; `onec_query_history` kept (encrypted question, typed plan); `onec_audit_log` → audit events + `business_commands` (R-01, mapping table).

Conventions: integer keys, `TenantScopedMixin`, `settings.DB_SCHEMA`, Alembic revision = next free after rebasing on `dev` (R-01, R-20).

## API (adapted)

As in TZ under `/api/v1/integrations/1c/…`: `connect`, `connections`, `connections/{id}`, `connections/{id}/health-check`, `query`, `query/history`, `command/preview|approve|execute`, `audit-log`.

## Acceptance

TZ criteria: connect via REST (T01); read-only queries (T02); NL translation (T03); tables/charts (T03); Excel/PDF export (T03); writes require approval (T04); audit of all operations (T04/T05); ≤ 10 req/min and ≤ 30 s (T01); credentials encrypted (T01).

## Plan

1. [P17-T01 accepted](../audit/p17/p17-t01.md) — 1C connection: OData/HTTP-service endpoints, credentials and health · done 2026-11-11 · accepted SHA `1141752d` · `P3` · `C3` · depends: [P13-T03 accepted](../audit/p13/p13-t03.md)
2. [P17-T02 accepted](../audit/p17/p17-t02.md) — 1C read-only data extraction from catalogs, documents and registers · done 2026-11-11 · accepted SHA `dd87bc3f` · `P3` · `C4` · depends: [P17-T01 accepted](../audit/p17/p17-t01.md)
3. [P17-T03 accepted](../audit/p17/p17-t03.md) — Natural-language queries, result formatting, export and history · done 2026-11-12 · accepted SHA `a9aad4f5` · `P3` · `C4` · depends: [P17-T02 accepted](../audit/p17/p17-t02.md)
4. [P17-T04 accepted](../audit/p17/p17-t04.md) — 1C write commands with preview, approval, fail-closed execution and audited reconciliation · done 2026-11-14 · accepted SHA `1b6d5236` · `P3` · `C5` · depends: [P17-T02 accepted](../audit/p17/p17-t02.md), [P13-T04 accepted](../audit/p13/p13-t04-p14-t04-business-crm.md)
5. [P17-T05](../tasks/p17-t05.md) — 1C role-based access policy and audit views · `P3` · `C3` · depends: [P17-T03 accepted](../audit/p17/p17-t03.md)
6. [P17-T06 accepted](../audit/p17/p17-t06.md) — 1C query continuation from the stored plan · done 2026-11-12 · accepted SHA `bec2fb8e` · `P3` · `C4` · depends: [P17-T03 accepted](../audit/p17/p17-t03.md)
7. [P17-T07 accepted](../audit/p17/p17-t07.md) — 1C query hardening follow-ups (DTD guard, export marking, float path, API shape) · done 2026-11-12 · accepted SHA `1f1ee61d` · `P3` · `C3` · depends: [P17-T03 accepted](../audit/p17/p17-t03.md)
8. [P17-T08 accepted](../audit/p17/p17-t08.md) — 1C catalog cache and history status values · done 2026-11-12 · accepted SHA `4ab761ab` · `P3` · `C3` · depends: [P17-T03 accepted](../audit/p17/p17-t03.md)
9. [P17-T09 accepted](../audit/p17/p17-t09.md) — 1C cache lifecycle and request-cost follow-ups · done 2026-11-12 · accepted SHA `2b19d184` · `P3` · `C3` · depends: [P17-T03 accepted](../audit/p17/p17-t03.md), [P17-T06 accepted](../audit/p17/p17-t06.md), [P17-T08 accepted](../audit/p17/p17-t08.md)

## Dependencies and gates

Requires a 1C test base with published OData/HTTP services (owner-provided) for integration evidence; CI uses recorded mocks.
