# Dependency profiles

`pyproject.toml` is the only direct dependency/extra declaration. `constraints.txt`
is its generated, fully pinned union; it is resolver input, not a second list of
requested dependencies. Root `requirements.txt` is the generated **core** export.
The exports in this directory add one named optional profile or development tools.
Regenerate with `python scripts/packaging/dependencies.py export` using the pinned
uv compiler in `tools.txt`; commit the reviewed constraints and all exports.
`python scripts/packaging/dependencies.py check` verifies source fingerprint,
profile parity and every export against the locked union without a resolver or
application import. `export --check` also resolves and compares exact closures.

Baseline lock target is Python 3.12 on Linux; package metadata allows newer
Python but this task does not certify additional interpreter/platform combinations.
Use a clean virtual environment and `pip install -r <export>`, then install the
built wheel with `--no-deps` (or an editable checkout with `--no-deps`). Always
run `pip check`. Installing the wheel normally uses the canonical pinned direct
dependencies; exports additionally pin the complete transitive closure.

- Core: root `requirements.txt`; no Celery, Telethon, segno or test tools. Redis stays core because the active
  admin SlowAPI limiter constructs Redis storage during ASGI import.
- `telegram.txt`: core + `.[telegram]` (MTProto only; bot HTTP remains core).
- `qr.txt`: core + `.[qr]` (optional invite QR images).
- `legacy-celery.txt`: core + `.[legacy-celery]`; frozen compatibility only, not
  permission to start Celery/Redis or change the active rate-limiter backend. `app/worker.py` is unrelated and retained.
- `dev.txt`: core + `.[dev]`; DB-dependent tests remain separately authorized.
- Combined extras are resolved together in the union constraints. Install desired
  profiles together with `-c requirements/constraints.txt`; never enable a live
  provider just because its Python library is installed.

Existing `pip install -r requirements.txt` consumers now receive only core runtime
packages. Install the explicit extras above if previously relying on bundled
MTProto/QR/frozen or development dependencies. No application feature is removed;
all optional imports remain lazy. Repository/distribution name, DB identifiers,
env keys, tenant plans, member caps and deferred P2-T15 validation are unchanged.
