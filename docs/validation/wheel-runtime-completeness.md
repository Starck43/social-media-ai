# Installed wheel runtime completeness

## Current disposition — BLOCKED after owner review

The previous READY handoff below is historical partial packaging evidence,
not acceptance of complete wheel-only runtime usability. Owner review of
`fa0c7e9701f13bdd68b29047b187431538c92e2d` found two blocking gaps. Keep
Draft PR #226 unmerged; P10-T10 is BLOCKED, not DONE. Original successful
checks and their limitations are preserved rather than rewritten as failures.

### Owner-reported target-platform evidence

Source: [PR review comment](https://github.com/Starck43/social-media-ai/pull/226#issuecomment-6296837084),
relayed from the owner's chat report; this agent did not execute these checks.
macOS 15.7.9, x86_64, CPython 3.12.6; clean `git archive fa0c7e9`; two separate
venvs (requirements-pinned and unconstrained), empty CWD outside the checkout.
This is not a run of the Linux Docker image merely because Python 3.12 matches.

- Wheel build exit 0 and both smoke runs pass: 366 Python files, 91 assets,
  457 matching files. Owner supplied SHA256 prefix `724f613a…`, not a full
  digest; do not substitute the different Linux wheel hash documented below.
- Runtime cryptography metadata, clean dev extra, CLI entry point and both
  `pip check` results pass. `app.templates.filters` imports and `cli --help`
  work from the empty CWD.
- Removing `app/web/templates/web/base.html` yields exit 1 naming that file.
- The owner reports no DB, migrations or providers were run, and no changes
  to their dirty dev tree. The report includes module imports and CLI help,
  not execution of application runtime/lifespan/server startup.

### Blocker: resource paths and logging CWD side effects

From an empty CWD, application import fails in `app/admin/setup.py:91`:

```text
app.mount("/static", StaticFiles(directory="app/static"), ...)
RuntimeError: Directory 'app/static' does not exist
```

Also relative to CWD: `app/main.py` (`Jinja2Templates("app/templates")`) and
`app/web/deps.py` (`FileSystemLoader("app/web/templates")`). The owner observed
`app/core/logger.py` create `./logs` in that empty directory. The allowlisted
smoke never loads app.main/app.web and cannot detect these failures. A passing
smoke from arbitrary CWD is not successful application import/startup there.

### Blocker: undeclared wheel runtime dependencies

Owner's wheel-only installations lack `greenlet` (SQLAlchemy asyncio import
requires it) and `psycopg` v3 (`app/core/database.py` / SQLAlchemy 2.1.x selects
the psycopg dialect for `create_engine("postgresql://…")` and raises
`ModuleNotFoundError: No module named 'psycopg'`). Owner used SQLAlchemy 2.1.4
pinned / 2.1.5 unconstrained. pyproject declares asyncpg and psycopg2-binary,
but not these dependencies; requirements.txt includes psycopg[binary] and can
mask the gap. Successful `pip check` only validates declared requirements,
not whether runtime imports have a closed dependency set.

### Required next step, not yet implemented

Resolve both blockers in an explicitly scoped continuation or linked,
separately claimed prerequisite tasks before this PR can be accepted/merged.
Added evidence must cover installed-wheel app.main/app.core.database imports
and template/static resolution from an empty CWD without DB connections,
migrations, provider calls, runtime/lifespan/server startup or deployment.
Logging's write location and side effects must be explicit. Full raw commands
and complete wheel hash for the owner's run were not supplied; nothing is
invented here. No source/dependency/test code or acceptance scope is changed
by this review-recording patch. READY is retained only as historical status
for the completed packaging slice, not the overall task.

## Scope and source pins

- Task: P10-T10 / AUD-10; Draft PR #226, currently BLOCKED by owner review.
  The following remote results describe the earlier completed packaging slice.
- Initial dev: `adf1d6fc79ea147863c4c2d7560349140ab2a6cf`.
- Claim published before implementation: `6bbc97f841beba30756b99ccebc288339980666c`.
- Dev advanced during execution. GitHub's PR branch update integrated fresh dev
  `aeb8c54d74a55710b5080fd0a980fbc0ecf36d8c` at
  `700727c2bf2b4a5dfb4b8d9370b16ea57a45168b`, preserving new P2-T12/T13/T14
  rows and foreign code. Final wheel was rebuilt from a clean source archive.
- Published implementation and smoke script:
  `fa0c7e9701f13bdd68b29047b187431538c92e2d`.
- Open #216/#217/#220/#224/#225 file ownership and heads checked before claim
  and rechecked before publication: no packaging implementation overlap.
  Shared ROADMAP/phase edits are limited to this task, not a transplant of
  another PR's docs. PR #225 remains at
  `876f2728518339b37f1eedbb8ad9ab83067dcfb5`, READY/Draft awaiting owner app
  acceptance. P10-T05 and the pre-existing duplicate P8-T25 are unchanged.
- All publication/commit creation used GitHub MCP; no local git commit,
  direct dev push, force-push, PR merge or subagent. Existing dirty worktrees
  were not modified. The task uses its own sandbox copy/worktree.

## Minimal change

`pyproject.toml` discovers only `app`, `cli` and descendants, including implicit
namespace packages such as `cli.commands` and `app.schemas`. Resource directories
are package data, not namespace packages: all files under `app/static`,
`app/templates` and `app/web/templates` are included. No existing asset is edited.
`cryptography>=42.0.0` moves from the dev extra into project runtime dependencies;
its minimum version and the flat `requirements.txt` installation list are unchanged.

`tests/wheel_runtime_smoke.py` is standalone and requires no pytest. The controller
uses only the standard library. It inventories every runtime Python file and all
three resource trees, compares source/wheel/installed SHA256 bytes, checks runtime
cryptography metadata and the CLI entry-point declaration, then starts the chosen
runtime venv's Python with `-I` in an empty temporary CWD. It verifies that every
installed file and imported application module resolves inside that venv, not the
checkout, and rejects all eight distributions named in the dev extra.

Only `app.core.config`, `app.utils.crypto`, `app.jobs.progress`, `app.types.enums`
and `cli.commands.llm` are explicitly imported, with their reviewed safe parents
and dependencies. The actual crypto helpers perform an in-memory Fernet round
trip using a generated-from-constant, explicitly synthetic test key. No commands,
handlers or runtime entry points are dispatched. A meta-path allowlist refuses
other application imports; Python audit hooks reject DNS, socket connect/bind,
subprocess and shell operations during the child smoke. These are regression
tripwires, not a security sandbox. Environment inheritance is removed, dotenv
loading disabled, CWD has no `.env`, and configuration contains only synthetic
nonsecret values and an unusable `.invalid` PostgreSQL host. No DB engine/model
module, web app, scheduler, worker or provider is imported or started.

## Executed results

Linux sandbox, CPython 3.13.14. Build venv: setuptools 84.2.0, wheel 0.48.0;
Black 26.10.1 and isort 9.0.2 were only in the build/tooling venv, never runtime.

| Check | Actual result |
| --- | --- |
| Original and fresh-dev baseline wheel builds | exit 0; 14 ZIP entries, 448 runtime files missing; crypto is dev-extra-only |
| Final clean-archive wheel build | exit 0; 462 ZIP entries, all 457 expected runtime files present |
| Pinned runtime install from final wheel, no extras | exit 0 |
| Pinned installed-wheel smoke | exit 0; 366 Python files + 91 assets byte-identical; five safe imports; Fernet round trip passed; dev packages absent |
| Unconstrained runtime smoke after installing final wheel | same pass, exit 0; no dev packages |
| `pip check` in both runtime venvs | exit 0; `No broken requirements found.` |
| Fresh-dev baseline with the same smoke script | expected exit 1: 448 missing runtime files |
| Remove `cli/commands/llm.py` from final wheel | expected exit 1: exactly one missing module |
| Remove `app/web/templates/web/base.html` | expected exit 1: exactly one missing asset |
| Move crypto requirement back to dev-only in wheel metadata | expected exit 1: `Encryption must be runtime, not extra` |
| Black/isort checks and scoped `git diff --check` | exit 0 |

The three mutated ZIPs are checker-negative fixtures, not installable artifacts;
their RECORDs are intentionally not regenerated and they are never installed.
There is no pytest suite pass, skip/xfail count or full application acceptance
claim: two installed-wheel smoke passes and four deliberately rejected wheel
inputs are the bounded evidence.

Final wheel: `social_media_ai-0.4.2-py3-none-any.whl`, SHA256
`dea014408406879cb5e029120dbaf50a3bb18e95b640b195bd671539e7aed967`.
Build timestamps can change ZIP hashes; runtime source/install byte hashes are
what the smoke validates.

## Commands

Executed sandbox paths below are task-owned. `REPO` is the isolated fetched
repository at `/data/package-runtime-check`, not the owner's development checkout.
All application imports execute in `--python`'s separate runtime venv.

```sh
REPO=/data/package-runtime-check
IMPL=fa0c7e9701f13bdd68b29047b187431538c92e2d
BASE=aeb8c54d74a55710b5080fd0a980fbc0ecf36d8c
python -m venv /data/wheel-build-venv
/data/wheel-build-venv/bin/python -m pip install 'setuptools>=45' wheel black isort
mkdir -p /data/wheel-final-source /data/wheel-baseline-final-source
cd "$REPO"
git archive "$IMPL" | tar -x -C /data/wheel-final-source
git archive "$BASE" | tar -x -C /data/wheel-baseline-final-source
cd /data/wheel-final-source
PIP_NO_INDEX=1 /data/wheel-build-venv/bin/python -m pip wheel . \
  --no-deps --no-build-isolation --wheel-dir /data/wheel-final
cd /data/wheel-baseline-final-source
PIP_NO_INDEX=1 /data/wheel-build-venv/bin/python -m pip wheel . \
  --no-deps --no-build-isolation --wheel-dir /data/wheel-baseline-final
cd /data/wheel-final-source
```

Runtime constraints were derived from the repository's requirements, not installed
with `-r` (which would install dev dependencies). Pip rejects extras in constraints;
remove only extras syntax, retaining every package's pinned version:

```sh
python - <<'PY'
from pathlib import Path
import re
lines = []
for line in Path('requirements.txt').read_text().splitlines():
    line = line.split('#')[0].strip()
    if line:
        lines.append(re.sub(r'\[[^]]+\]', '', line))
Path('/data/wheel-runtime-constraints.txt').write_text('\n'.join(lines) + '\n')
PY
python -m venv /data/wheel-final-runtime
/data/wheel-final-runtime/bin/python -m pip install \
  -c /data/wheel-runtime-constraints.txt \
  /data/wheel-final/social_media_ai-0.4.2-py3-none-any.whl
python tests/wheel_runtime_smoke.py --python /data/wheel-final-runtime/bin/python \
  --wheel /data/wheel-final/social_media_ai-0.4.2-py3-none-any.whl
/data/wheel-final-runtime/bin/python -m pip check
/data/wheel-final-runtime/bin/python -m pip freeze
```

The unconstrained runtime venv was initially created/installed with
`python -m venv /data/wheel-runtime-venv` and
`/data/wheel-runtime-venv/bin/python -m pip install /data/wheel-fixed/*.whl`.
After fresh-dev synchronization, only the project wheel was replaced:

```sh
/data/wheel-runtime-venv/bin/python -m pip install --force-reinstall --no-deps \
  /data/wheel-final/social_media_ai-0.4.2-py3-none-any.whl
python tests/wheel_runtime_smoke.py --python /data/wheel-runtime-venv/bin/python \
  --wheel /data/wheel-final/social_media_ai-0.4.2-py3-none-any.whl
/data/wheel-runtime-venv/bin/python -m pip check
/data/wheel-build-venv/bin/python -m black --check tests/wheel_runtime_smoke.py
/data/wheel-build-venv/bin/python -m isort --check-only tests/wheel_runtime_smoke.py
# In the task worktree:
git diff --check
```

To reproduce the four negative cases (each checker command must exit 1):

```python
from pathlib import Path
from zipfile import ZipFile
import subprocess

wheel = Path('/data/wheel-final/social_media_ai-0.4.2-py3-none-any.whl')
output = Path('/data/wheel-final-negative')
output.mkdir(exist_ok=True)
for case, missing in [('nested', 'cli/commands/llm.py'),
                      ('asset', 'app/web/templates/web/base.html'),
                      ('encryption', None), ('baseline', None)]:
    target = output / (case + '.whl')
    if case == 'baseline':
        target = Path('/data/wheel-baseline-final/social_media_ai-0.4.2-py3-none-any.whl')
    else:
        with ZipFile(wheel) as src, ZipFile(target, 'w') as dst:
            for name in src.namelist():
                if name == missing:
                    continue
                data = src.read(name)
                if case == 'encryption' and name.endswith('/METADATA'):
                    data = data.replace(b'Requires-Dist: cryptography>=42.0.0\n',
                                        b'Requires-Dist: cryptography>=42.0.0; extra == "dev"\n')
                dst.writestr(name, data)
    result = subprocess.run(['python', 'tests/wheel_runtime_smoke.py',
        '--python', '/data/wheel-final-runtime/bin/python', '--wheel', str(target)],
        capture_output=True, text=True)
    assert result.returncode == 1, (case, result.stdout, result.stderr)
    message = 'Encryption must be runtime' if case == 'encryption' else 'Missing runtime files'
    assert message in result.stderr
```

## Harness corrections and limitations

Initial smoke development exited 1 because the strict import allowlist omitted
`app.utils.db_enums`, a reviewed helper imported by the enum package. After adding
that helper, the next run rejected a mis-sized synthetic Fernet key. The fixture
now derives valid base64 from exactly 32 constant bytes; production crypto code
and assertions were not weakened. The expected file inventory moved from argv
to stdin to avoid huge failure messages. An attempted pinned install with
`-c requirements.txt` failed before installation (`Constraints cannot have
extras`); the normalization above resolved that tooling-only issue. All final
positive/negative results above were rerun on the published implementation.

- Only Linux/Python 3.13.14 and the stated build-tool versions were executed.
  Other supported Python/platform versions, minimum setuptools, sdist/release
  workflows and install with extras were not verified.
- Unconstrained dependency resolution differs from `requirements.txt`: notably
  bcrypt 5.0.0 versus pinned 4.0.1, FastAPI 0.155.0 versus 0.143.2, Starlette
  1.9.0 versus 1.8.0 and SQLAlchemy 2.1.5 versus 2.1.4. `pip check` verifies
  declared dependencies, not application compatibility. Authentication and
  other runtime behavior with those versions are NOT certified; dependency
  range cleanup is outside this package-completeness task.
- Presence/bytes of all files are verified, but importing every runtime module
  would cross DB/app/provider boundaries. No `cli.main`, `app.main`,
  `app.runtime`, ASGI integration, CLI dispatch, engine, migration or provider
  test was run. No full regression suite, live actions or deployment.
- Existing resource consumers still use CWD-relative paths, for example
  `app/main.py`, `app/admin/setup.py`, `app/web/deps.py`. Shipping assets does
  not prove the application can locate them when started from any directory.
  Path-resolution changes and application startup are deliberately outside
  this minimal packaging patch. No arbitrary-CWD startup success is claimed.
- Owner/coordinator: review this exact implementation SHA and evidence; reproduce
  installation/smoke on the intended Python/platform. Before wheel-based app
  deployment, separately validate resource lookup under the intended working
  directory and ordinary runtime integration through the repository's approved
  local test route. No DB/migration/deploy action is authorized by this handoff.
  Keep PR Draft; current owner review blocks merge until both gaps above are
  resolved. Only the coordinator can accept DONE; merge needs explicit owner
  confirmation.

## Pinned runtime environment

No dev extra was requested; the smoke verifies all its distributions absent.
The full resolved runtime freeze (excluding the project wheel's sandbox URL):

```text
alembic==1.20.0
amqp==5.4.1
annotated-doc==0.0.5
annotated-types==0.8.0
anyio==4.15.1
asyncpg==0.32.0
bcrypt==4.0.1
billiard==4.3.1
celery==5.6.3
certifi==2026.10.19
cffi==2.1.1
click==8.5.0
click-didyoumean==0.3.1
click-plugins==1.1.1.2
click-repl==0.4.1
croniter==6.2.4
cryptography==50.0.2
Deprecated==3.0.0
dnspython==2.9.0
email-validator==2.3.0
fastapi==0.143.2
fastapi-pagination==0.16.1
h11==0.16.0
httpcore==1.0.9
httpx==0.28.1
idna==3.20
itsdangerous==2.2.0
Jinja2==3.1.6
kombu==5.6.2
limits==5.8.0
Mako==1.4.3
markdown-it-py==4.2.0
MarkupSafe==3.0.4
mdurl==0.1.2
opentelemetry-api==1.45.1
packaging==26.3
passlib==1.7.4
prompt_toolkit==3.0.53
psycopg2-binary==2.9.13
pyaes==1.6.1
pyasn1==0.6.4
pycparser==3.11
pydantic==2.14.1
pydantic-settings==2.16.0
pydantic_core==2.50.1
Pygments==2.21.0
PyJWT==2.15.1
python-dateutil==2.9.0.post0
python-dotenv==1.2.4
python-multipart==0.0.32
redis==8.1.0
rich==15.0.0
rsa==4.9.1
segno==1.6.6
shellingham==1.5.4
six==1.17.0
slowapi==0.1.10
sqladmin==0.32.0
SQLAlchemy==2.1.4
starlette==1.8.0
Telethon==1.45.0
typer==0.27.3
typing-inspection==0.4.4
typing_extensions==4.16.0
tzdata==2026.5
tzlocal==5.4.4
uvicorn==0.54.0
vine==5.1.0
wcwidth==0.9.3
wrapt==2.5.1
WTForms==3.2.2
```
