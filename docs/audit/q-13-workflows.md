# Q-13 — restrict GitHub workflows to master pushes

Standalone owner-requested CI correction. Registry: [ROADMAP](../../ROADMAP.md).
Branch: `fix/q-13-master-only-workflows` (work landed directly on `dev`; no branch
or PR existed at audit time). Outcome: **DONE 2026-10-29**, integrated into `dev`.

## What was audited

The correction was delivered as `eb27aa69` and covered all four workflow files.
Auditing it against current `dev` found that it had been **reverted in part** by
the later P7-T06 work.

## Findings

### Blocking: `deploy.yml` regressed P7-T06

`eb27aa69` reduced `deploy.yml` to a single master push trigger. Commit
`4e26b701` (P7-T06, merged via PR #281 as `02dde506`) rewrote the same file and
restored both removed triggers:

```yaml
pull_request:
  branches: [dev, master]
push:
  branches: [dev, master]
```

So on `dev` today, `offline.yml`, `package.yml` and `reference.yml` were
correctly master-only, while `deploy.yml` executed on every `dev` push and every
pull request — the exact behaviour Q-13 removed. Deploy gates did not fire, but
the workflow ran where the rule forbids it.

### Correction applied

`deploy.yml` is master-only again, and the parts that only existed to serve the
removed triggers are reconciled rather than left pretending to work:

- `on:` is a single `push` to `master`, keeping the docs-only `paths-ignore`.
- `validate-pr` is removed. It existed solely for `pull_request` events and could
  never run again; its image build without publishing has no remaining caller.
- `build-and-push` no longer carries `if: github.event_name == 'push'`, which is
  now the only event that can reach it.
- The `Login to GHCR` step drops the same redundant condition.
- `deploy` is production-only: the `dev`/`STAGING_DEPLOY_ENABLED` branch is
  removed, the environment is fixed to `production`, and concurrency is a single
  `deploy-production` group.

This is a deliberate change of a P7-T06 deliverable, not a silent one: P7-T06
accepted staging deployment triggered by `dev` pushes, which cannot coexist with
a master-only trigger set. The owner's P0 instruction (Q-13) wins, and
`docs/DEPLOYMENT.md` now states that the `staging` Environment is unused. If
staging deployment returns, it needs a workflow that does not violate Q-13 —
for example a `master`-built image promoted by an explicit owner action.

## Gate

`scripts/ci/check_deployment.py::workflow_contract` encoded the pre-Q-13
contract (PR validation plus a `dev` staging branch). It was rewritten to assert
the current one and to guard the regression:

- `on` must be exactly `push` to `master`;
- `validate-pr` must not exist and no job may build with `push: false`;
- the `deploy` condition must not mention `dev` or `STAGING_DEPLOY_ENABLED`;
- `deploy` must target the protected `production` environment and keep the
  backup-readiness and exact-SHA migration approvals.

Mutation-tested: restoring `branches: [dev, master]` fails with
`Push branch filters must be master only`; re-adding `pull_request` fails with
`Workflow must be limited to master pushes only`.

## Verification

- `python -m scripts.ci.check_deployment` — PASS, 142 assertions.
- All four workflows parse with exactly one trigger, `push` to `master`.
- `docs/DEPLOYMENT.md` updated where it described `dev`-triggered staging
  deploys, the removed PR build, and the unused `staging` Environment.

No workflow was executed, no image published, and no deployment, database or
production state was touched. Offline static validation only, as the card
requires.
