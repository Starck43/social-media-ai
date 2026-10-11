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
| Agent state | Atomic session state/history changes and typed memory/output boundaries. | PRs #58, #59, #100, #102 | Full hostile-input and every-write authorization coverage. |
| Collection and analysis | Partial-result diagnostics, tenant-scoped staging, bounded retirement, attachment snapshots and conservative text/media coverage. | PRs #69–80, #88, #90, #92, #94, #97, #98 | Historical repair, remaining source fidelity and end-to-end evidence acceptance. |
| Telegram admission | Durable L1/L2 admission with cursor/receipt coordination and media placeholders. | PRs #97, #101 | Long-term receipt policy and whole-channel recovery acceptance. |
| Digest delivery | Per-target/per-part state, snapshot/checkpoint foundations and tenant-safe routing safeguards. | PR #12 and follow-up delivery packages | Staging acceptance for partial failures, lost acknowledgements and operator recovery. |
| LLM routing | DB-managed providers/models, model-level defaults and capability-aware selection/fallback. | PR #96 and migrations through 0091 | Complete per-attempt cost ledger and concurrent budget admission. |
| Web and operations | Truthful job outcomes, secure cookies outside development, runtime readiness and conservative recovery guidance. | PRs #27, #40, #67, #68 | Repeatable staging install/restore and release evidence packet. |
| Development workflow | Canonical local/remote skills, collaboration rules and compact handoff format. | PRs #103, #104; commits `7833ac6`, `84ee16d` | Keep instructions aligned with the canonical document set. |

## Current handoff

- Task: canonical documentation consolidation is complete and validated in the local `dev` working tree.
- Base revision: `84ee16d284a6355006dd71c7157a0744ab203ddd`; the documentation checkpoint is the commit containing this handoff. It remains local until explicitly pushed.
- Result: 23 canonical files under `docs`; `docs/design/README.md` is the only
  design document; root README is a 138-line project entry point; explicit
  Russian closeout phrases authorize local commits for completed validated work
  and require a truthful checkpoint plus copyable new-chat prompt;
  application/schema behavior is unchanged.
- Validation: 27 Markdown entry files checked with zero broken relative links and
  zero unbalanced fences; all 27 ORM tables match the 27 `MODELS.md` table
  sections; key 0088–0091 field changes match metadata; Alembic reports one head,
  `0091`; changed Python comments/docstrings compile; `git diff --check` passes.
- Database tests, `alembic current/check` and the full suite were not run because
  this change does not alter runtime behavior and no target database was selected.
- Next action: select the next bounded PRD-05 package from
  [the prioritized queue](design/README.md#prioritized-open-work).

## Recording future delivery

Add or update one area row only when a merge changes a durable project boundary.
Link the PR and state the remaining boundary. Keep command output, individual test
counts, patch hashes and abandoned approaches in the PR rather than this file.
