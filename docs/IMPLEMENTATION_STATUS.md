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
| Atomic confirmation consumption | Versioned actor-bound intent, tenant-scoped locked consume of the exact pending snapshot and post-commit single-use authorization; losers, replacements and uncertain commits cannot dispatch the approved write. | PR #105, merge `0179296`; owner-local acceptance at `a4d2673`, subsequent board-only reconciliation. | External-effect exactly-once delivery, crash recovery after consumption, every-write coverage and whole PRD-02 acceptance remain open; no runtime rollout or DB migration certification. |
| Collection and analysis | Partial-result diagnostics, tenant-scoped staging, bounded retirement, attachment snapshots and conservative text/media coverage. | PRs #69–80, #88, #90, #92, #94, #97, #98 | Historical repair, remaining source fidelity and end-to-end evidence acceptance. |
| Telegram admission | Durable L1/L2 admission with cursor/receipt coordination and media placeholders. | PRs #97, #101 | Long-term receipt policy and whole-channel recovery acceptance. |
| Credential diagnostic privacy | Bounded API credential-test and MTProto check-session failure statuses without provider/exception secret text; success labels and access gates preserved. | PR #107, head `5ff3d9f`, merge `f02d6d8`; verified in `dev`. | Other login/OAuth/CLI/vault logs, retention/offboarding and whole PRD-06 acceptance; no live-provider or deployment certification. |
| Digest delivery | Per-target/per-part state, snapshot/checkpoint foundations and tenant-safe routing safeguards; legacy-path partial delivery is terminal `partial_delivery_requires_review` and blocks automatic rescheduled resends (explicit force only). Zero-success legacy delivery is a terminal unconfirmed outcome (no automatic retry). Scheduled and forced attempts claim the run row on its observed status and `updated_at` version; an in-flight `pending` row stops other attempts with `digest_run_in_progress_requires_review` before build/send, and a forced resend takes it over only after 30 minutes without a write. Lease loss mid-run leaves the run `pending` (no requeue, no resend) and digest review codes stay visible on the job. | PR #12, follow-up delivery packages, PRs #106, #112, #113, #115, #120 | Staging lease-loss and operator-recovery acceptance; delivery of an attempt stopped mid-flight stays unknown (operator decision). |
| LLM routing | DB-managed providers/models, model-level defaults and capability-aware selection/fallback. | PR #96 and migrations through 0091 | Complete per-attempt cost ledger and concurrent budget admission. |
| LLM analyzer journal foundation — DONE | Tenant-scoped captured stage records before result projection; tariff snapshot, Decimal USD and explicit unknown cost; execution IDs independent of source/job/task foreign keys. New OpenAI-compatible, Anthropic and custom analyze calls have pre-HTTP UUID identities: distinct identical dispatches remain distinct and replay of one envelope stays stable. | Journal `05bf353`, analyzer wiring `5953efb`, contract `9ebe945`, migration definition 0092; PR #114, head `55db324`, merge `f8b468e`; integrated in `dev`. | Legacy/mixed-version envelopes, fallback/learning/agent-chat capture, durable pre-dispatch evidence/write recovery, dispatch-time tariffs and concurrent budget admission; whole PRD-03 remains OPEN. |
| Web and operations | Truthful job outcomes, secure cookies outside development, runtime readiness, conservative recovery guidance and redacted `/app/jobs` backlog/stale-worker signals. | PRs #27, #40, #67, #68, #110 | External alerting, repeatable staging install/restore and release evidence packet. |
| Development workflow | Canonical local/remote skills, collaboration rules and compact handoff format. | PRs #103, #104; commits `7833ac6`, `84ee16d` | Keep instructions aligned with the canonical document set. |

## Current handoff

- Completed package: PRD-03 captured analyzer dispatch identity — **DONE, integrated**
  in PR #114 (merge `f8b468e`), building on the existing journal foundation.
- Owner-local tests and focused compilation/formatting were accepted at `55db324`;
  exact commands, counts, environment correction and baseline comparison are in
  the PR review. Remote verification was static plus clean merge/readback checks;
  no Python/DB/provider tests or migrations were run remotely.
- Whole PRD-03 remains **OPEN**. New analyzer dispatches no longer collapse solely
  because their scope/model/usage match. Legacy/mixed-version envelopes still use
  content-derived keys; capture is from returned envelopes and journal write
  failures remain warnings. Chat/fallback/learning coverage, durable pre-dispatch
  evidence, dispatch-time tariffs and concurrent budget admission remain open.
  See the [usage/cost boundary](AI_PIPELINE_AND_PROMPTS.md#usage-and-cost-boundary)
  and [readiness gate](BUSINESS_PRODUCTION_READINESS.md#prd-03--complete-bounded-economics).
- The completed package is released from active ownership. No remaining gate,
  schema/runtime activation or deployment is marked complete here.

## Recording future delivery

Add or update one area row only when a merge changes a durable project boundary.
Link the PR and state the remaining boundary. Keep command output, individual test
counts, patch hashes and abandoned approaches in the PR rather than this file.
