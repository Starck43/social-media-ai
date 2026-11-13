# P10-T10 owner review and integration evidence

Review date: 2026-10-20. This record captures owner-attributed macOS evidence,
independent code review and GitHub integration for P10-T10.

## Revision pins and disposition

- Fresh `dev` before merge: `6fbe7a961dafc3b05fccd9cfe99f7652bdc6a33a`.
- Reviewed implementation/test SHA: `60638e5b40a205c233941367c3edf41ea66758f8`.
- PR #226 registry/evidence head: `98aaa76a567a2eb33b5895190cc810365bdebc9c`.
- GitHub merge commit and resulting `dev`: `c90ac9192103d59d4fcd54f14d050110537af72b`.
  Its parents are the fresh pre-merge `dev` and PR head above.
- PR #226 is MERGED. Final pre-merge state was `CLEAN`; the only reported
  check, `GitGuardian Security Checks`, passed. `dev` had no branch protection
  or repository rulesets; there were no submitted reviews or new inline review
  comments. Older blocking owner comments were for implementation `fa0c7e9`.
- Runtime/config/test paths in `98aaa76` are byte-identical to `60638e5`.
  Changes after the implementation SHA are registry/evidence documentation.

## Owner-attributed macOS evidence

Source: owner's handoff in chat on 2026-10-20. The owner reports the target
macOS check completed against implementation SHA
`60638e5b40a205c233941367c3edf41ea66758f8`. This report is attributed to the
owner; no local macOS run was made. The handoff supplied completion and the
source SHA, but not a command transcript or detailed per-check output, so none
is inferred here.

Earlier, separately attributed macOS evidence for `fa0c7e9` is documented in
[the original owner review comment](https://github.com/Starck43/social-media-ai/pull/226#issuecomment-6296837234).
It exposed the resource-path and dependency-closure defects that were later
fixed; it is historical evidence and is not presented as a run of `60638e5`.

## Independent implementation review

The dependency closure is consistent with the runtime import paths:
`sqlalchemy[asyncio]` supplies greenlet through SQLAlchemy's supported extra,
and `psycopg[binary]` supplies the psycopg v3 dialect selected by the sync
PostgreSQL URL. Existing asyncpg and psycopg2 dependencies remain.

Static files and template roots resolve relative to installed modules in
`app/admin/setup.py`, `app/main.py` and `app/web/deps.py`. The wheel smoke loads
templates through the actual loaders and checks the mounted static directory
from an empty CWD. The `main.py` code change is limited to its path import and
template expression; recorded AST checks preserve lifespan, maintenance loop
and application factory behavior.

The logger keeps rotating file output and moves its default from `./logs` to
`~/.social-media-ai/logs`. `LOG_DIR` expands `~`, requires an absolute path,
creates its directory on logger import and fails explicitly when unwritable.
This is acceptable for local user installs. Deployments that collected
`./logs` must set an absolute writable mounted path and update collection; this
requirement is documented in `docs/CONFIGURATION.md`.

No blocking implementation defect was found in dependency, resource-path or
logging changes. Review does not claim full application startup, DB behavior,
deployment mount validation or universal dependency-range compatibility.

## Exact-source remote validation

The remote report records clean-archive Linux x86_64 / CPython 3.13.14
validation at `60638e5`, not a local rerun:

- Offline wheel build; two fresh wheel-only installs (unconstrained and
  requirements-constrained); both expanded smokes and both `pip check` runs
  passed.
- Both wheels contained 368 Python files and 94 assets byte-for-byte. Real
  database/main module imports, template/static lookups and Fernet checks passed.
- Default and explicit `LOG_DIR` modes passed in four child checks; CWD stayed
  unchanged. DB, runtime and external-operation attempt counters were zero.
- Nine negative controls failed as expected; restored smoke passed. Scoped
  formatting, diff check and lifecycle/application-factory AST checks passed.

Full commands, versions, hashes, negative controls and harness limits are in
[the exact-source validation report](https://github.com/Starck43/social-media-ai/blob/50c319c91cfe1e77f8dc244c29253e4f1a1df3c1/docs/validation/wheel-runtime-completeness.md).
It records that importing `app.main` constructed the ASGI object without
entering lifespan, opening DB connections, running migrations, starting runtime
or contacting providers. No pytest/full suite, real DB, migration, runtime,
provider or deployment was run for this local review.

## Acceptance

P10-T10 is DONE after the owner-reported target-platform check, independent
review and merge of PR #226 at the exact reviewed runtime bytes. This acceptance
covers wheel dependency closure and installed resource lookup; it does not
claim application startup, DB, provider or deployment acceptance.
