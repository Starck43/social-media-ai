# Implementation status

This file is a compact index of delivered foundations. The current queue and
ownership live in [design/README](design/README.md). Pull requests and Git history
are the source for exact patches, review discussions and test transcripts.

## Integration checkpoint

Verified local `dev`: `84ee16d284a6355006dd71c7157a0744ab203ddd` on
2026-10-12, matching `origin/dev` when documentation consolidation began. PRs
through #104 are integrated. This checkpoint does not certify a deployment,
database revision or full release gate.

## Delivered foundations

| Area | Delivered behavior | Integration references | Remaining boundary |
| --- | --- | --- | --- |
| Identity and tenancy | Fail-closed active-user and permission checks, tenant-scoped model/manager boundaries, tenant-scoped agent-session uniqueness. | PRs #22, #29–32, #38, #66, #95 | Durable approval lifecycle and complete global-admin/credential review. |
| PostgreSQL jobs | Claim fencing, renewal, ownership preflight, periodic heartbeat, cancellation and bounded recovery/admission primitives. | PRs #51–57, #87, #89, #91, #93 | Non-cooperative work, production timing and unresolved external-effect recovery. |
| Agent state | Atomic session state/history changes, typed memory/output boundaries and untrusted-data framing of modality results in unified-summary prompts. | PRs #58, #59, #100, #102, #108, #109 | Remaining hostile-input surfaces and every-write authorization coverage. |
| Collection and analysis | Partial-result diagnostics, tenant-scoped staging, bounded retirement, attachment snapshots and conservative text/media coverage. | PRs #69–80, #88, #90, #92, #94, #97, #98 | Historical repair, remaining source fidelity and end-to-end evidence acceptance. |
| Telegram admission | Durable L1/L2 admission with cursor/receipt coordination and media placeholders. | PRs #97, #101 | Long-term receipt policy and whole-channel recovery acceptance. |
| Credential diagnostic privacy | Bounded API credential-test and MTProto check-session failure statuses without provider/exception secret text; success labels and access gates preserved. | PR #107, head `5ff3d9f`, merge `f02d6d8`; verified in `dev`. | Other login/OAuth/CLI/vault logs, retention/offboarding and whole PRD-06 acceptance; no live-provider or deployment certification. |
| Digest delivery | Per-target/per-part state, snapshot/checkpoint foundations and tenant-safe routing safeguards; legacy-path partial delivery is terminal `partial_delivery_requires_review` and blocks automatic rescheduled resends (explicit force only). | PR #12, follow-up delivery packages, PR #106 | Zero-success lost acknowledgement, concurrent publishers, lease loss and staging operator-recovery acceptance. |
| LLM routing | DB-managed providers/models, model-level defaults and capability-aware selection/fallback. | PR #96 and migrations through 0091 | Complete per-attempt cost ledger and concurrent budget admission. |
| LLM analyzer journal foundation — DONE | Tenant-scoped records for captured analyzer stage envelopes before result-projection decisions; tariff snapshot, Decimal USD and explicit unknown cost; execution IDs independent of source/job/task foreign keys. | Journal `05bf353`, analyzer wiring `5953efb`, contract `9ebe945`; migration definition 0092; integrated in `dev`. | Unique per-dispatch identity, fallback/learning/agent-chat capture, durable write-failure handling and concurrent budget admission; whole PRD-03 remains OPEN. |
| Web and operations | Truthful job outcomes, secure cookies outside development, runtime readiness, conservative recovery guidance and redacted `/app/jobs` backlog/stale-worker signals. | PRs #27, #40, #67, #68, #110 | External alerting, repeatable staging install/restore and release evidence packet. |
| Development workflow | Canonical local/remote skills, collaboration rules and compact handoff format. | PRs #103, #104; commits `7833ac6`, `84ee16d` | Keep instructions aligned with the canonical document set. |

## Current handoff

- Completed package: PRD-03 analyzer journal foundation — **DONE, integrated**.
  The three package commits are `05bf353`, `5953efb` and `9ebe945`.
- Code/contract review verified the package in dev `3823177af378b08b77c108be58a4e970aba640c4`.
  Capture precedes the partial-result skip, relevance filter and the
  no-meaningful-results return; the existing analysis decisions are preserved.
- Validation: the owner reports local tests passed. Exact commands, counts and
  tested SHA were not included in the closeout report and are not inferred.
  Remote review was static; no Python/DB/provider tests or migrations were run.
- Whole PRD-03 is **OPEN**, not accepted by this foundation. Content-derived keys
  can deduplicate separate dispatches with identical scope/model/usage; capture
  is from returned analyzer envelopes and journal write failures remain warnings.
  See the [usage/cost boundary](AI_PIPELINE_AND_PROMPTS.md#usage-and-cost-boundary)
  and [readiness gate](BUSINESS_PRODUCTION_READINESS.md#prd-03--complete-bounded-economics).
- The completed package is released from active ownership. Select any subsequent
  bounded PRD-03 work from the [canonical queue](design/README.md#prioritized-open-work);
  no remaining gate or runtime activation is marked complete here.

## Recording future delivery

Add or update one area row only when a merge changes a durable project boundary.
Link the PR and state the remaining boundary. Keep command output, individual test
counts, patch hashes and abandoned approaches in the PR rather than this file.
