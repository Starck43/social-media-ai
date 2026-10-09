# Cloud/hybrid architecture plan: work status and continuation

## Status

Prepared on 2026-10-09 in `ai/docs-cloud-hybrid-architecture` from dev `7a0637412ae761fff83f26fb4f8b5a581dcf6e28`. **Documentation prepared for PR review; not merged into dev, not implemented or deployed.** The owner requested a plan and PR, not merge or application implementation.

## Prepared in this PR

- [x] [Deployment architecture v1](../DEPLOYMENT_ARCHITECTURE.md): cloud/hybrid first, autonomous later; personal and server connector profiles; direct cloud access without a company server.
- [x] Local inference routing and capability checks, prohibition of external fallback for local-only work; current global provider fleet explicitly distinguished from proposed tenant-private routing.
- [x] Data-transfer policy, device/connection distinction, user/source ACLs, local secret handling, outbound protocol and revocation/offline/retry behavior.
- [x] Strict local-only input/result path explicitly required: cloud chat cannot secretly carry local-only content; locally hosted inference alone is not confidentiality.
- [x] Delivery phases, acceptance evidence, unresolved implementation choices and autonomous prerequisites.
- [x] Add discovery links in [documentation index](../DOCS_INDEX.md); preserve all existing reference/readiness links.
- [x] Inspect open PR #5/#6: existing digest schema/retry work remains separate, not relabeled as a general connector/outbox implementation.

These checked items mean prepared documents, not merged capabilities.

## Validation and parallel work

Local plan checks passed: Markdown fences, single title, whitespace, required architecture boundaries and expected relative-link targets. Existing linked docs were read; the handoff target is supplied in this PR. No application tests, deploy/restore, migration, network confidentiality test or real connector/model calls ran. They are implementation gates, not documentation results.

Root directory and missing `.github` were inspected: no PR template was found. Preserve parallel UI/theme work from dev. PR #5 also edits DOCS_INDEX in the Business readiness section; this PR adds links in the Product direction section. Different insertion regions may merge cleanly but overlap still requires a fresh merge/review check. PR #6 touches schema/model/tests and separate docs, not this scope. Do not modify either PR or their tracker on their behalf.

## Remaining work

- [ ] Review/merge this documentation PR only on owner instruction.
- [ ] Revalidate existing cloud readiness blockers against fresh code before implementation.
- [ ] Choose pilot OS/package and one source; design device enrollment and source ACL mapping.
- [ ] Approve protocol/API/schema, data policy and threat model as a separate bounded unit.
- [ ] Implement read-only personal/server connector and controlled local model routing incrementally.
- [ ] Implement/test local-only input and result viewing before advertising that privacy mode.
- [ ] Add internal adapters, write operations and autonomous packaging in separate phases.

## Continue next session

Fetch fresh dev and this branch; inspect changes since the baseline and both open digest PRs. Read the deployment plan, general architecture/product plan and readiness roadmap. Confirm this PR's actual merge status, then select protocol/identity/data-policy design as the first connector architecture unit, after preserving ongoing readiness priorities. Keep prepared/in-review/merged/deployed distinct. Record exact checks, limitations and next continuation after each task; no direct push to dev, no automatic merge, no production migration.
