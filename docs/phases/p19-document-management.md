# P19 — Document management (TZ module 6)

[Product direction](../PRODUCT_PLAN.md#business-modules) · [Adapted specification and review](../BUSINESS_MODULES.md) · [ROADMAP](../../ROADMAP.md#p19)

**Phase status: IN_PROGRESS** (registered by [P13-T01 accepted](../audit/p13-t01.md); ROADMAP owns task status).
Owner estimate (TZ): 4–5 weeks — not a repository commitment.

**Goal:** Draft business letters and claim responses with AI from case/CRM context, keep full version history with diff/restore, run approval on an exact revision and export/deliver DOCX/PDF.

## Baseline: done and arriving

- In `dev`: `knowledge_documents` and `document_revisions` with content hash, versions, status and approval invalidation on edit; tenant-isolated evidence and chat handoff (P12-T03, P12-T11, migration 0122); case approvals bound to document hash (P12-T05).
- Arriving via open PRs: private file storage (P12-T12, [PR #327](https://github.com/Starck43/social-media-ai/pull/327)) for binary originals.

## Adapted requirements

TZ 6.1–6.5, adapted:

- **Drafting:** AI letters/claim responses, templates, context from case/CRM, unsupported claims marked, human review for legal/financial statements.
- **Versions:** automatic revision per change, history, diff, restore (as a new revision); branching deferred.
- **Approval:** request with approver, states pending/approved/rejected/revision_requested, comments, notifications, expiry, immutable hash (R-19: not an e-signature).
- **Storage:** PostgreSQL + private storage (D9, R-18); TXT/MD/DOCX/PDF; metadata; links to contacts/cases; search.
- **Export/delivery:** DOCX/PDF, email attachment, TG/MAX when supported, delivery tracking via the P15 outbox.

## Data model (adapted)

TZ `documents`/`document_versions` → extend existing `knowledge_documents`/`document_revisions` (R-04); `document_approvals` and `document_templates` added.

Conventions: integer keys, `TenantScopedMixin`, `settings.DB_SCHEMA`, Alembic revision = next free after rebasing on `dev` (R-01, R-20).

## API (adapted)

As in TZ under `/api/v1/documents/…`: CRUD, `{id}/versions`, `{id}/restore/{version_id}`, `draft`, `templates`, `{id}/request-approval`, `approvals/pending`, `approvals/{id}/approve|reject`, `{id}/export/pdf|docx`, `{id}/send/email`.

## Acceptance

TZ criteria: AI letters from context (T02); templates (T02); every version saved (T01/T03); diff (T03); approval request→approve/reject (T04); notifications (T04); edit after approval invalidates it (T04); PDF/DOCX export (T05); email with attachment (T05); metadata search; content search not built under D3 (T06).

## Plan

1. [P19-T01 accepted](../audit/p19-t01.md) — **DONE 2026-11-07**: document types, links, templates, CRUD/API/UI and atomic revision-bound transitions; accepted `b2d8931174f7736afd50e0ffa75d629de20fcdd2`.
2. [P19-T02](../tasks/p19-t02.md) — Templates and AI drafting with evidence and unsupported-claim marking · `P3` · `C4` · depends: [P19-T01](../audit/p19-t01.md), [P14-T02 accepted](../audit/p13-t04-p14-t04-business-crm.md) or [P15-T02](../tasks/p15-t02.md) (context sources)
3. [P19-T03](../tasks/p19-t03.md) — Version history, diff and restore · `P3` · `C3` · depends: [P19-T01](../audit/p19-t01.md)
4. [P19-T04](../tasks/p19-t04.md) — Document approval workflow bound to an immutable revision hash · `P3` · `C4` · depends: [P19-T01](../audit/p19-t01.md), [P13-T04 accepted](../audit/p13-t04-p14-t04-business-crm.md)
5. [P19-T05 accepted](../audit/p19-t05.md) — **DONE 2026-11-11**: DOCX/PDF export, approved email attachments, atomic idempotent scheduling and durable delivered-revision provenance; accepted `e7c6e7a64459c14340f2a0d241b62f2a0d329bb8`, merge `fef205340cb06b98e3357d21b7300ac792c1a795` ([#465](https://github.com/Starck43/social-media-ai/pull/465)).
6. [P19-T06](../tasks/p19-t06.md) — Document search · `P3` · `C2` · depends: [P19-T01](../audit/p19-t01.md)
7. [P19-T07 accepted](../audit/p19-t07.md) — **DONE 2026-11-09**: `app/services/knowledge` to the 80% coverage gate; accepted `66d356e3`, package coverage 78.08% → 85.63%, test-only. Its finding — the `retrieval.py` boundary predicate contradicting its docstring — is registered as [P19-T09](../tasks/p19-t09.md), awaiting one owner choice.

## Dependencies and gates

Delivery (T05) after [P15-T05](../tasks/p15-t05.md)/T06. Its retrieval-boundary finding [P19-T09](../audit/p19-t09.md) is accepted: one destination-clearance predicate, `f90c4a75`. Contacts/case links after [P13-T07 accepted](../audit/p13-t07.md).
