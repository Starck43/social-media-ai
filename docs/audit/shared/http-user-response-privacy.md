# HTTP user response privacy verification

## Outcome and revision pins

P10-T02 owner integration acceptance is complete and the coordinator has accepted the task for DONE. PR #227 carries the task-scoped docs and regression-test closeout.
The runtime fix was already in dev: `UserInDB` has no password hash field since
`eb6cad6c56e78768b6482eba312082b849d24712`. This PR adds verification and
corrects the stale response contract/example, not a second runtime fix.

- Fresh synchronized dev: `aeb8c54d74a55710b5080fd0a980fbc0ecf36d8c`.
- Claim before test implementation: `6093a08597cfa46a430c628d74fb291060c8deb9`.
- Published test/API-doc SHA: `35df749c1ba3b29d71b99089d072f96f00d8e814`.
- The exact published SHA was fetched into a clean source archive and tested.
  `app/` and `cli/` are byte-identical to synchronized dev; schemas, routes,
  policies, managers, recovery, login and privilege behavior are unchanged.

Open PRs #216/#217/#220/#224/#225/#226 and the phase ownership were checked
before claim and before publication. No active user.py/schema ownership overlap;
#216 and #224 API doc hunks are outside the changed Users response section.
P10-T03 remains unassigned/TODO and is not implemented or accepted by this work.
P10-T01/T04/T05 are unchanged. PR #225 stays READY/Draft at
`876f2728518339b37f1eedbb8ad9ab83067dcfb5`; PR #226 stays READY/Draft at
`67686aaa58be872cf90d3da269449ce201930115`. Their files/evidence are not copied
into this PR. Foreign claims/evidence and the pre-existing duplicate P8-T25
are preserved. Shared-document edits are task-scoped, from the fresh base.

## What the isolated tests execute

`tests/test_http_user_response_privacy.py` uses the repository's existing
`source_import_isolation.load_isolated_source` helper. Actual source is loaded
with module-local dependency bindings, never wholesale sys.modules replacement.
The real user schemas, endpoint router, API dependency factories, shared permission
policy and resource catalog run unchanged. Four exact User policy method bodies
are extracted from the canonical identity model to avoid ORM construction; enum
helpers use a synthetic config object, not the real settings module.

A minimal FastAPI TestClient harness mounts only that router, with the same
router-wide auth declaration checked against actual entry.py via AST. There is
no application factory, middleware, lifespan, application startup or network
server. Identity/token validation and all manager/storage operations are explicit
in-memory doubles. A meta-path guard rejects unreviewed app/cli imports; socket
connect/DNS are blocked. No real config/.env, database/engine, provider or secret
is loaded. Fixtures contain only conspicuously synthetic hash/token sentinels.

This tests HTTP response projection and the route-level guard composition, not
real token authentication, tenant isolation or manager persistence/authorization.
For example, an allowed update means the real route guard permitted dispatch to
the mocked manager; it does NOT prove the real manager accepts a foreign target.
The operator UPDATE success case targets its own ID. Platform-role route coverage
observes the existing route and does not accept the P10-T03 privilege workflow.

## Executed results

Separate sandbox venv; Linux, CPython 3.13.14. The published SHA run returned:

```text
47 passed, 1 warning in 2.77s
exit 0; 0 skips, 0 xfails
```

| Coverage | Cases |
| --- | --- |
| All five profile response routes, attribute objects and dictionaries | 10 |
| Both public compatibility schemas never access secret attributes | 2 |
| All five response OpenAPI shapes and no hash properties | 1 |
| Missing/invalid identity boundary denies before any storage dispatch | 10 |
| Existing role/action route matrix, positive and denied cases | 11 |
| Operator guards re-read deleted/inactive/revoked actors | 9 |
| Missing update targets preserve 404 responses | 3 |
| Personal route dependencies, manager forwarding, router-wide auth declaration | 1 |

Routes covered:
- `GET /api/v1/users`
- `GET /api/v1/users/me`
- `PUT /api/v1/users/me`
- `PUT /api/v1/users/{user_id}`
- `PUT /api/v1/users/{user_id}/platform-role`

Responses retain exactly `id`, `username`, `email`, `is_active`, `created_at`,
`updated_at`; hash/password, role and privilege attributes of the mock object
are not serialized. Role/action denials are 403; missing/invalid identity is 401;
no profile-list/update/role manager call occurs for rejected requests.

Black/isort, `pip check` and task-scoped `git diff --check` passed (exit 0).
Published test and API doc bytes were compared with the reviewed sandbox files.
The sole final warning is Starlette's deprecation of httpx-based TestClient;
httpx 0.28.1 matches requirements.txt. No warning was suppressed or assertion
weakened; there is no full-suite green claim.

### Negative regression sensitivity

A separate disposable archive of the same dev runtime received only the exact
published test file and one intentionally leaking schema field:
`hashed_password: str` was added to the existing `UserInDB` class. This mutant
was never published, installed in the owner environment or merged.

The exact same command returns **18 failed / 29 passed, exit 1**, with 1 warning.
HTTP failures explicitly detect the synthetic hash in real serialized response
bodies; OpenAPI and no-secret-attribute-read assertions also fail. This is a
negative checker-sensitivity result, not an unfixed failure on the PR branch.
The original current-dev runtime passes because its fix is already present.

### Initial harness failure, corrected

The first fixture iteration yielded 12 failed / 35 passed: the AST-generated
policy class lacked `__name__` in its exec namespace, so its class module became
`builtins` and Pydantic rejected it as an attribute-backed response object.
The fixture now supplies its real test module name. Production code and response
assertions were not changed to obtain a pass. Initial pytest-config warnings
were resolved by installing the manifest-pinned pytest-asyncio; httpx was aligned
from the allowed minimum 0.27.0 to requirements.txt's 0.28.1. Final results above
were rerun after those corrections and again from the published source archive.

## Exact commands

Task-owned paths only; do not run the full application or root conftest.

```sh
python -m venv /data/profile-test-venv
/data/profile-test-venv/bin/python -m pip install \
  'pytest==9.1.1' 'fastapi==0.143.2' 'starlette==1.8.0' 'httpx==0.27.0' \
  'pydantic==2.14.1' 'email-validator==2.3.0' 'sqlalchemy==2.1.4' \
  'black==26.10.1' 'isort==9.0.2'
/data/profile-test-venv/bin/python -m pip install \
  'pytest-asyncio==1.4.0' 'httpx==0.28.1'
cd /data/http-user-response-check
/data/profile-test-venv/bin/python -m black tests/test_http_user_response_privacy.py
/data/profile-test-venv/bin/python -m isort tests/test_http_user_response_privacy.py
/data/profile-test-venv/bin/python -m pytest tests/test_http_user_response_privacy.py \
  --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
/data/profile-test-venv/bin/python -m black --check tests/test_http_user_response_privacy.py
/data/profile-test-venv/bin/python -m isort --check-only tests/test_http_user_response_privacy.py
/data/profile-test-venv/bin/python -m pip check
/data/profile-test-venv/bin/python -m pip freeze
git diff --check

# Fetched published test/API-doc revision, clean archive:
mkdir -p /data/profile-published-source
git archive 35df749c1ba3b29d71b99089d072f96f00d8e814 | tar -x -C /data/profile-published-source
cd /data/profile-published-source
/data/profile-test-venv/bin/python -m pytest tests/test_http_user_response_privacy.py \
  --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
```

The negative run used a task-owned clean dev archive and copied the same test and
existing helper into it. Reproduce without modifying the reviewed checkout:

```sh
cd /data/http-user-response-check
mkdir -p /data/profile-leak-mutant
git archive aeb8c54d74a55710b5080fd0a980fbc0ecf36d8c | tar -x -C /data/profile-leak-mutant
cp tests/test_http_user_response_privacy.py tests/source_import_isolation.py \
  /data/profile-leak-mutant/tests/
python - <<'PY'
from pathlib import Path
p = Path('/data/profile-leak-mutant/app/schemas/user.py')
s = p.read_text()
old = 'class UserInDB(UserInDBBase):\n'
assert s.count(old) == 1
p.write_text(s.replace(old, old + '    hashed_password: str\n'))
PY
cd /data/profile-leak-mutant
/data/profile-test-venv/bin/python -m pytest tests/test_http_user_response_privacy.py \
  --noconftest -p no:cacheprovider -o addopts='' -q --tb=short --no-header
# Expected exit 1, 18 failed / 29 passed.
```

## Owner integration acceptance

Executed against the exact PR source
`987796aa01ba9dd924207512b2a314bb63fbda97`; fresh dev at test time was
`e446c044a863d641b27714a4bffe8563ecbb91dd`. The OpenCode test-runner used
Python 3.12.6, pytest 9.1.1, FastAPI 0.143.2, httpx 0.28.1,
SQLAlchemy 2.1.4 and greenlet 3.5.6.

The real `app.main.app` was mounted with `httpx.ASGITransport` without
lifespan/startup. Real JWT creation/validation, middleware, managers and ORM
sessions were used; there were no auth, identity, storage, manager or policy
doubles. The full application OpenAPI check and real HTTP/auth/ORM acceptance
ran as two tests:

```text
2 passed, 0 failed, 0 skipped, 6 warnings; exit 0
```

Exact command:

```sh
.venv/bin/python -m pytest .agents/tmp/test_pr227_users_http.py --noconftest -o addopts= -q --tb=short --no-header
```

The executed scratch module SHA256 was
`825be27eb62e867bd92f346df200702c07e84301274c50f4111349d4d5e2c977`.
Its implementation is local audit evidence and was not added to the PR because
it loads the approved owner-local environment. The durable
[owner integration report](https://github.com/Starck43/social-media-ai/pull/227#issuecomment-6300278265)
records scope and attribution.

OpenAPI excluded password-hash fields from response schemas and components.
Real `GET /users`, `GET/PUT /users/me` and cross-user profile update returned
hash-free public profiles. Missing identity returned 401; denied ordinary-user
list/update returned 403 with no target write. Self-update and permitted admin
profile update persisted only intended profile fields; role and password hash
were unchanged. The test removed exactly its synthetic users in `finally`;
independent read-only verification found no remaining `pr227-` rows.

The first attempt to return the full default user list encountered 262
pre-existing `@example.invalid` records in shared `test_schema`, rejected by
the actual `EmailStr` response model. No foreign rows were changed. The passing
list assertion uses the endpoint's real `skip/limit` and manager query to locate
the synthetic admin, then asserts that the HTTP page contains that exact ID.
This accepts hash privacy and rights on the real list route; compatibility of
unbounded default responses with those historical invalid address strings
remains a separate follow-up.

Preflight attribution: exact standalone `pgrep -fl pytest` before delegation
and after execution each returned 1 (no active pytest). Immediately before
execution OpenCode verified the exact source SHA and ran
`pgrep -fl pytest; echo "pgrep_exit=$?"`, which reported exit 1. That wrapper
is recorded accurately. No process was killed.

P10-T03, recovery, login protection, app startup/lifespan, full-suite behavior,
schema creation/reset/migration, providers and deployment were not tested or
accepted. PR #227 is accepted at the source SHA above; this report does not
attribute the run to the later merge/closeout commit.

## Resolved sandbox dependencies

```text
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.15.1
black==26.10.1
certifi==2026.10.19
click==8.5.0
dnspython==2.9.0
email-validator==2.3.0
fastapi==0.143.2
h11==0.16.0
httpcore==1.0.9
httpx==0.28.1
idna==3.20
iniconfig==2.3.1
isort==9.0.2
mypy_extensions==1.1.0
opentelemetry-api==1.45.1
packaging==26.3
pathspec==1.1.1
platformdirs==4.13.5
pluggy==1.7.0
pydantic==2.14.1
pydantic_core==2.50.1
Pygments==2.21.0
pytest==9.1.1
pytest-asyncio==1.4.0
pytokens==0.4.1
sniffio==1.3.1
SQLAlchemy==2.1.4
starlette==1.8.0
typing-inspection==0.4.4
typing_extensions==4.16.0
```
