# Offline safety net (P11-T03)

## Supported baseline and install profiles

- Supported minimum: **Python 3.14** (README and package metadata agree). The
  offline workflow tests this minor; newer Python/application installations are
  not certified by this lane. No claim of full application/runtime acceptance.
- **Offline tooling:** clean virtual environment, then
  `python -m pip install --require-hashes -r .ci/requirements.txt`.
  The lock includes transitive dependencies and hashes; no editable/package
  install, `.env`, deployment secret, database driver or application dependency.
  Downloads are needed for installation; checks need no external service.
- **Application/development:** existing `requirements.txt` and project `dev`
  extras remain separate. Canonical application/optional/dev profiles and generated exports are documented
  in [requirements/README](../requirements/README.md); they are not installed here. No DB fixtures in the offline lane.
- Regenerate the tooling lock deliberately using
  `uv==0.6.17 pip compile --python 3.14 --generate-hashes --no-emit-index-url
  -o .ci/requirements.txt .ci/requirements.in`; review all version/hash changes.

## Required checks

From the repository root, using the tool-only environment:

```sh
python -m scripts.ci.checks
python -m scripts.ci.run_offline
```

`checks` compiles every tracked Python source in memory (no imports or bytecode),
checks stdlib-only imports of the two explicitly file-loaded leaf modules,
and reads migration revision/down_revision/depends_on literals via AST. It
rejects duplicate/missing revisions, cycles, unknown parents and multiple heads;
it **never imports** Alembic, a migration module, `env.py` or application config.
This checks graph structure, not migration behavior/schema validity.

The exact reviewed test files and isolation reasons are in
`offline-tests.json`. CLI/web tests inspect AST/text; fingerprint/delivery tests
execute only stdlib leaf modules by file; cookie-security uses extracted code
with doubles. Tooling tests exercise guard failures in child pytest processes.
The runner selects these exact files (not the whole suite), disables plugin
autoload/environment addopts, uses a standalone pytest config and
`--noconftest`. A plugin installed **before collection** rejects app/CLI,
DB/provider/HTTP-client imports, explicit DB fixture/bootstrap imports, socket
connections and DNS. Skip/xfail/xpass fail the lane. This is an accidental-access
tripwire, not an OS security sandbox. New test files require import/fixture
review and explicit manifest changes; a filename or pytest marker is not proof
of isolation. DB/integration tests keep the root conftest and remain separately
authorized. Do not install the app merely to make an offline import succeed.

## Frozen baseline, not blanket exclusions

`baseline.json` records initial **P11-T03** ownership/source; the inventories
are `format-baseline.json` and `secret-baseline.json`. It does not disable
Black/isort or secret detectors by directory. Tools remain Black 25.1.0 and
isort 6.0.1, line length 120, Python target 3.14. Formatting exceptions identify
each pre-existing file by exact SHA-256 and failing tools. Unchanged exceptions
are validated by hash; all other files run through the formatters. A changed/new file
must pass both formatters, and deletions/fixes are allowed. Do not auto-refresh
this file in CI or add exceptions for new changes. Reduce inventory as files
are touched; broad formatting churn is intentionally outside this task.

Secret scanning uses pinned detect-secrets with default detectors/heuristics,
**verification disabled** (no credential checks or external calls), on tracked
files only (excluding the hash inventory itself to avoid recursive findings). Findings contain path/type/hash, never secret text. Initial candidate ownership/rationale is recorded alongside the inventory. Newly
introduced fingerprints fail; remove a real secret and rotate it outside this
lane, never suppress it to obtain PASS. This scans the checkout, not history;
it is a safety net, not proof that credentials have never been committed.

The workflow has read-only permissions, SHA-pinned checkout/setup actions,
no secrets, no service containers, no runtime startup and no migration apply.
Rollback: disable/remove this one workflow and its tool-only files; no application
behavior or DB state is modified. Keep Python metadata aligned with README.

## P8-T41 source-only journey lane

The separate `vk-journey-source-only` workflow job runs
`python -m scripts.check_p8_t41` on Python 3.12 with the standard library alone.
It does not extend `offline-tests.json` or bypass the pytest isolation review.
It file-loads actual plan/pipeline/policy/result source with explicit dependency
bindings; ORM managers, provider collection/analysis and report aggregation/model
calls are in-memory doubles. Normal runtime/DB/provider imports, DNS and socket
connections fail closed. The runner reports HEAD/dirty state and rejects skips.
The guard is a tripwire, not an OS sandbox. Cross-surface seam checks are AST-only,
not executed HTTP/browser/CLI/admin contracts. See
[the matrix and remaining DB/live gates](../docs/checks/p8-t41.md).
