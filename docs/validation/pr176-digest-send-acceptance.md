# PR #176: manual digest-send acceptance

## Current status

The application change (`digest_send_now` requires `digestrun.create`, not
`digestrun.update`) already exists in `dev` through commit
`6d16436e3f3fb701d5dd8595e18bcc95bc430390`. Do not duplicate it or weaken
confirmation, tenancy, CSRF or external-write safeguards.

The PR branch was updated from `dev` without force-pushing. Merge commit
`0436c0ec7061d40171b519abbe63dc3b2c0d384a` has the same tree as the audited
`dev` baseline `9c7bf4d146d8086d279ee9d1f80a950af9cdff43`, and preserves the
original PR head `cb2bc6d4c72d77ed6920a64ceb9e20af91cf4847`.
This follow-up changes tests and acceptance documentation only.

**Keep Draft:** mock coverage is complete for the scope below; real
ASGI/membership/PostgreSQL acceptance is still pending. Historical evidence is
preserved in the [original handoff archive](https://github.com/Starck43/social-media-ai/pull/176#issuecomment-6187394197).
Old PASS counts and the old `exit 3 / collected 0` run are not current acceptance.
PR #180 has merged; waiting for that PR is no longer the blocker.

## Sandbox validation, 2026-10-15

The owner explicitly authorized the assistant to execute its own mock tests.
That is a narrow exception for this sandbox, not a change to project-wide
Kilo/pare-test rules or approval for database/bootstrap operations.

Environment: Python 3.13; built-in `unittest`; real Jinja2 and
`itsdangerous==2.2.0`. The interpreter was `/data/pr176-env/bin/python` in a
separate review tree `/data/pr176-review`. No `.env` was loaded; no database,
application bootstrap, provider or transport was used.

### Actual failure found and fixed

Before changing tests, all ten existing isolated digest tests errored during
setup on the current baseline. Their inherited loader attempted the now-added
`app.services.tenancy.web_access` import from `app/web/perms.py` outside its
isolation boundary and raised `ModuleNotFoundError: No module named 'app'`.

The digest suite now uses `tests/digest_permission_support.py` and the existing
module-local `tests/source_import_isolation.py` loader. Actual policy, identity,
confirmation, runtime, registry, web guard/handler and CSRF code remain loaded;
storage, model-registry classification, HTTP plumbing, LLM and publisher are
doubles. The ten original test methods are preserved.

This new, reproduced loader failure does **not** establish the cause of the
historical owner-local `exit 3` run; its first redacted exception is still needed.

### Completed results

```sh
/data/pr176-env/bin/python -m unittest discover -s tests -p 'test_digest_send*.py' -v
```

**35 tests passed, exit 0**: ten existing regression tests plus 25 new boundary
tests (17 runtime and eight web). Loop subcases are not counted as extra tests.
The formatted combined run completed in 0.493 seconds.

Coverage includes:

- membership CREATE versus VIEW/UPDATE, including a non-owner/non-superuser
  caller with no platform permission;
- real runtime staging, confirmation and registry dispatch; one-use intent,
  actor binding, expiration and old permission-contract rejection;
- permission revocation before confirmation and between mocked consume and
  dispatch; inactive/missing membership, user, workspace and channel binding;
- replaced pending intents, two concurrent yes turns against an in-memory CAS,
  and an injected consume acknowledgement failure that must not dispatch;
- real web guard and CSRF signing/validation, missing/invalid/tampered tokens,
  foreign posted tenant rejection and tenant-context restoration;
- private loaders leave global `app.*` imports unchanged.

A separate negative control loaded a temporary copy of `reports.py` with the
old `digestrun.update` declaration. Declaration parity, VIEW/UPDATE denial and
CREATE-positive runtime tests all failed as expected: **3 failures, 0 errors**.
The application source was not edited. This is a regression-sensitivity check,
not a failing application suite or three additional passing tests.

Black and isort check-only runs (line length 120) and `git diff --check` passed
for the changed/new test files.

### Evidence limits

- Mock managers do not prove ORM loading, detached relationships, SQL tenancy
  filtering or durable membership revocation.
- The CAS double does not prove PostgreSQL transaction/commit behavior,
  database concurrency or acknowledgement recovery.
- Web tests call the real handler with constructed `WebPerms`; they do not run
  ASGI, session middleware or the middleware's fresh membership lookup.
- No real digest delivery, scheduler, provider or production behavior was tested.

Do not mark these limits complete from the mock PASS count. The next bounded
handoff is [local evidence intake](pr176-local-evidence-intake.md). After that
intake, review the original bootstrap-free plugin/membership matrix before
issuing an exact, sequential integration run.