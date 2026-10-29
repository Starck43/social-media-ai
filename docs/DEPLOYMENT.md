# Deployment Guide

> **Status:** repository deployment assets are prepared; VPS/runtime acceptance remains unrun.
> Observation/recovery runbook: [PR #27](https://github.com/Starck43/social-media-ai/pull/27), MERGED as `0234c21`; source baseline `10212b5e`.
> GitGuardian and Kilo passed for head `a874ada`; eight tabletop cases remain unexecuted.
> It is documentation, not executed recovery, staging acceptance or deployment.

During an incident start at [Observation and conservative recovery](#observation-and-conservative-recovery),
not installation/bootstrap/update commands. Starting or restarting a process can
write data or cause external effects. The root [Compose](../docker-compose.yml)
is a legacy `web:3000` sketch and is not the supported Python service stack. The
supported Python profiles are the isolated staging and production Compose files
described below; migrations are explicit deployment steps, not API startup
commands. Do not run old examples as a supposedly read-only migration diagnostic.

## Supported single-VPS topology (prepared, not provisioned)

The supported deployment target is the Python modular monolith described in
[DESIGN](DESIGN.md): FastAPI serves `/app`, `/admin`, API, and health routes in
one `api` container. There is no separate Node/web or admin container. The
production Compose stack separates PostgreSQL (persistent), Redis (private,
volatile rate-limit storage), API, and an optional single `app.runtime` owner.
The runtime is an explicit `runtime` Compose profile and is intentionally not
started by deploy automation until its operational and retention gates are
accepted. `app.main` still starts its own short maintenance loop in API-only mode;
API-only does not mean a read-only process.

The host's `/etc/nginx` is the public TLS terminator. The internal Nginx
container is a private reverse proxy to the `api` service; it publishes only
`127.0.0.1:8226` for production and `127.0.0.1:18000` for staging. Install and
customize [the host-vhost example](../nginx/vps-sites.conf.example) only after
choosing domains and obtaining a certificate. Replace every `example.invalid`
and certificate placeholder, then run `nginx -t` before reload. HTTP is redirected
to HTTPS; the ACME challenge path is left available for webroot validation.

`docker-compose.yml` at the repository root is an old `web:3000` sketch, not the
supported Python stack; it also expects an untracked root Dockerfile and claims
port 8226. Do not start it beside the new Compose projects. `nginx/default.conf`
is now the container-side proxy for the Python `api`; the root web sketch is
retained only as legacy material pending a VPS inventory/cutover.

Both Compose projects may share the VPS but must not share env files, database
volumes, Redis state, or provider credentials. Staging uses a separate named DB
volume and an internal no-egress network; only staging publishes the PostgreSQL
port, bound to loopback `127.0.0.1:55432`. Production PostgreSQL has no host port.
Staging starts no `app.runtime`; use only synthetic or approved sanitized data.
The same VPS is
not a separate failure domain. Check `DOCKER_SUBNET` on the host before first
start; choose a non-overlapping private subnet if another Docker network already
uses the documented example range.

The GitHub workflow builds the checked-in `docker/Dockerfile`, publishes a
commit-SHA tag to GHCR, and deploys the returned immutable image digest. Pushes to `dev` deploy that image to staging only when
`STAGING_DEPLOY_ENABLED=true`; pushes to `master` can reach production only when
`PRODUCTION_DEPLOY_ENABLED=true`, `PRODUCTION_BACKUP_READY=true`, and
`PRODUCTION_MIGRATIONS_APPROVED_SHA` equal to the full push commit SHA, then wait
for the GitHub `production` Environment approval. Configure a read-only GHCR
pull credential on the VPS; the workflow does not transmit it. Set environment
secrets for the SSH endpoint, private key and pinned `known_hosts`, plus the
environment variable `VPS_DEPLOY_DIR`. Enable required reviewers/branch restrictions in GitHub
Settings—workflow YAML cannot create those protection rules. Until those values
are configured, CI only builds the image and skips deployment.

The deployment script takes an exclusive per-target host lock and waits for DB/Redis
health. Production first creates a protected local logical snapshot, then stops
the API and any already running runtime before one explicit Alembic step; this is
not offsite disaster recovery. API/runtime stay stopped on migration failure;
inspect migration state and the snapshot before deciding recovery. Do not enable the production deployment gate until
an offsite backup and restore policy is established. Review migrations on the
approved test schema and authorize production apply separately. After migration, the API and proxy are recreated and `/readyz` is checked;
a previously running runtime is restarted only after HTTP readiness. A runtime
that was stopped stays stopped. The successful image digest is stored privately
in `.deployed-image.production` or `.deployed-image.staging` for later wrapper
commands and backups; these files are ignored and never contain credentials.
Runtime remains
off by default; the configured one-month retention is irreversible, so do not
start the runtime profile before the owner accepts P6-T01 and recovery evidence.

Owner setup still required later: create production/staging DNS records; issue a
certificate whose SANs cover both names; install the reviewed host-vhost file;
open only SSH/80/443; create private `.env.production` and `.env.staging` files
with mode 600 and separate keys; prepare the two deployment directories; configure
GitHub Environments/secrets/variables and approvals; verify the actual VPS Docker
subnets; then execute the staging install, sanitized-copy, backup/restore, and
P7-T02/P4-T01/P6-T01 proofs. No domain, host, certificate, database, or secret
was accessed while preparing these files.

## Prerequisites

- VPS with Ubuntu 22.04+ (or Debian 12+)
- 2 vCPU, 4 GB RAM minimum
- Docker Engine and Docker Compose v2
- PostgreSQL 16 container image (bundled in the prepared profiles)
- Domain name (optional, for HTTPS)

## Isolated staging Compose (prepared, not provisioned)

The standalone [`docker-compose.staging.yml`](../docker/docker-compose.staging.yml)
profile is separate from development and uses only `.env.staging`. It runs
PostgreSQL, Redis (for the admin rate limiter), the FastAPI application, and an
internal Nginx proxy in the `social-ai-staging` Compose project with its own DB
volume. PostgreSQL and the proxy bind only to loopback (`127.0.0.1:55432` and
`127.0.0.1:18000`); containers use an internal no-egress network. The API image
uses Python 3.14. Staging does not start `app.runtime` or provider integrations.
Run one explicit Alembic upgrade before starting the API; API startup itself does
not migrate the schema.

This is repository preparation, not a provisioned host, restored database,
deploy-gated check, or staging acceptance. No production credentials or database
are supplied. For a remote host, keep the ports loopback-only and use an approved
SSH/VPN access path; public ingress/TLS and host provisioning are separate work.

### Prepare and start

1. Copy `.env.staging.example` to `.env.staging`; replace every placeholder with
   newly generated, staging-only values. Set mode `600`. Generate `SECRET_KEY`
   with `python -c 'import secrets; print(secrets.token_urlsafe(64))'`,
   `CREDENTIALS_KEY` with `python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'`,
   and `POSTGRES_PASSWORD` with `python -c 'import secrets; print(secrets.token_hex(32))'`.
   Use the same hex password in `POSTGRES_URL`; its host must remain `db` and
   database `social_ai_staging`. Never copy a production `SECRET_KEY`,
   `CREDENTIALS_KEY`, provider token, or `.env` into staging.
2. Validate the required keys and staging-only database target, then start the
   isolated services:

   ```bash
   cp .env.staging.example .env.staging
   chmod 600 .env.staging
   # Edit .env.staging with freshly generated values before the next command.
   bash scripts/ops/staging_compose.sh config --quiet
   bash scripts/ops/staging_compose.sh up -d --wait db redis
   # Apply one reviewed migration, then start HTTP/proxy services.
   APP_IMAGE=ghcr.io/starck43/social-media-ai@sha256:<64-hex-digest> \
     bash scripts/ops/staging_compose.sh run --rm --no-deps api alembic upgrade head
   APP_IMAGE=ghcr.io/starck43/social-media-ai@sha256:<64-hex-digest> \
     bash scripts/ops/staging_compose.sh up -d --no-deps api nginx
   bash scripts/ops/staging_compose.sh ps
   ```

   The wrapper refuses unresolved placeholders, a non-private env file,
   unexpected/provider keys, a non-staging database URL, or missing Docker.
   For routine releases use `scripts/ops/deploy_vps.sh staging <immutable-image>`
   so the image pull, single migration, and readiness check happen in order.
   `config --quiet` avoids printing interpolated values.
3. Take a private logical backup at any approved checkpoint:

   ```bash
   bash scripts/ops/backup_staging_db.sh
   ```

   The script writes a PostgreSQL custom-format dump and SHA-256 manifest under
   `$HOME/.local/state/social-media-ai/staging/backups` by default (or an absolute
   `STAGING_BACKUP_DIR` outside the repository). Files are mode `600`; the
   directory must be mode `700`. It validates the archive and never prunes old
   backups. Mode bits are not encryption: store real data only on approved,
   encrypted private storage. Never commit or attach dumps/manifests to issues or
   PRs.

### Restore an approved sanitized copy

Database copying is a separate owner-approved action. Use only an approved,
privacy-reviewed/sanitized PostgreSQL custom-format dump and its matching
`.sha256` manifest, transported through an approved private channel. For a dump
received from another system, create the manifest from its containing directory
(e.g. `cd /private/path && sha256sum approved-sanitized.dump > approved-sanitized.dump.sha256`)
so it names the exact archive basename. Do not copy production encryption keys
or provider credentials; encrypted credentials in a
source dump will not be usable with the staging-only key. Start the staging DB,
then use the explicit destructive flags:

```bash
bash scripts/ops/staging_compose.sh up -d db
bash scripts/ops/restore_staging_db.sh /private/path/approved-sanitized.dump \
  --approved-sanitized-copy --confirm-overwrite-staging
```

Restore verifies the checksum and archive format before changes, saves the
current staging DB to a new protected backup, stops the API, and replaces only
the database reached through the fixed staging Compose project. The API remains
stopped afterward. Review the source Alembic revision and data scope; separately
approve and run exactly one explicit migration before starting the API. A
successful `pg_restore` is not a
restore acceptance proof: verify schema, app access, and the required operation
under [P7-T02](tasks/p7-t02.md). Backup retention and deletion remain an explicit
operator policy.

Stop containers without deleting the staging volume with
`bash scripts/ops/staging_compose.sh down`. `down --volumes` permanently deletes the
staging database; use it only after confirming no retained staging data or
backup is needed.

### GitHub deployment workflow (prepared, gated off)

Pull requests to `dev` or `master` build the checked-in `docker/Dockerfile`
without publishing. Branch pushes publish a commit-tagged image to GHCR and the
deploy step pins the build's content digest.
`dev` can deploy only to the GitHub `staging` Environment when the repository
variable `STAGING_DEPLOY_ENABLED=true`; `master` additionally requires
`PRODUCTION_DEPLOY_ENABLED=true`, `PRODUCTION_BACKUP_READY=true`, and
`PRODUCTION_MIGRATIONS_APPROVED_SHA` equal to the full push commit SHA, then waits
for approval configured on the protected `production` Environment. The workflow
uses SSH with a pinned `VPS_KNOWN_HOSTS` secret and copies only the Compose,
Nginx, and operations support files. It does not copy environment files or pass
a registry credential to the VPS; configure a read-only GHCR pull login on the
server first. Deployment paths are fixed as
`/opt/social-media-ai/staging` and `/opt/social-media-ai/production` using the
environment variable `VPS_DEPLOY_DIR`. Deploy variables remain unset/false by
default, so preparing this workflow does not start a deployment.

---

## Historical development sketches (not the production installation path)

Use the prepared single-VPS profiles above for new hosts. The following local
and systemd examples are historical alternatives, not additional services to
start beside Compose. Inventory existing owners before any cutover.

## Option A: Docker Compose — illustrative deployment sketch

### 1. Project Structure

```
social-media-ai/
├── docker/
│   ├── docker-compose.yml     # Checked-in db/api sketch, not a production profile
│   └── Dockerfile
├── .env                      # Secrets (never committed)
├── requirements.txt
├── alembic.ini
├── migrations/
├── app/
├── cli/
└── ...
```

### 2. Illustrative Dockerfile

The snippet is not the checked-in Dockerfile and is not an incident command.

```dockerfile
FROM python:3.14-slim

WORKDIR /app

# System deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    gcc libpq-dev && \
    rm -rf /var/lib/apt/lists/*

# Python deps
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Project
COPY . .

# Non-root user
RUN useradd -m -s /bin/bash appuser
USER appuser

EXPOSE 8000

CMD ["python", "-m", "app.runtime"]
```

### 3. Illustrative runtime Compose

This historical `app` example is NOT the checked-in `db`/`api` Compose. Review a
production profile separately; the profile and its commands are not certified here.

```yaml
version: "3.9"

services:
  db:
    image: postgres:15-alpine
    environment:
      POSTGRES_DB: social_media
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER}"]
      interval: 10s
      timeout: 5s
      retries: 5

  app:
    build: .
    ports:
      - "8000:8000"
    env_file: .env
    depends_on:
      db:
        condition: service_healthy
    volumes:
      - ./static:/app/app/static:ro
    restart: unless-stopped

volumes:
  pgdata:
```

### 4. .env

Copy from [CONFIGURATION.md](CONFIGURATION.md) and fill in values.

### 5. Deploy

```bash
# Build and start
docker compose up -d --build

# Run migrations
docker compose exec app python -m cli.main ...  # or run alembic inside container

# Check logs
docker compose logs -f app

# Health check
curl http://localhost:8000/readyz
```

---

## Option B: Systemd (Direct Install)

### 1. Install Dependencies

```bash
# Python
sudo apt update
sudo apt install -y python3.14 python3.14-venv python3.14-dev libpq-dev

# Create virtualenv
python3.14 -m venv /opt/smm-ai/venv
source /opt/smm-ai/venv/bin/activate

# Install project
cd /opt/smm-ai
git clone https://github.com/.../social-media-ai.git .
pip install -r requirements.txt

# Install telethon (optional, for L2 Telegram)
pip install -r requirements/telegram.txt
```

### 2. Configure

```bash
# Copy .env
sudo cp .env.example /opt/smm-ai/.env
sudo nano /opt/smm-ai/.env  # fill in values

# Set permissions
sudo chown -R smm-ai:smm-ai /opt/smm-ai
```

### 3. Systemd Service

Create `/etc/systemd/system/smm-ai.service`:

```ini
[Unit]
Description=ИИ Ассистент Runtime
After=network.target postgresql.service

[Service]
Type=simple
User=smm-ai
Group=smm-ai
WorkingDirectory=/opt/smm-ai
Environment="PATH=/opt/smm-ai/venv/bin"
EnvironmentFile=/opt/smm-ai/.env
ExecStart=/opt/smm-ai/venv/bin/python -m app.runtime
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
```

Enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable smm-ai
sudo systemctl start smm-ai
sudo systemctl status smm-ai
```

### 4. Worker Service (Optional)

Create `/etc/systemd/system/smm-ai-worker.service`:

```ini
[Unit]
Description=ИИ Ассистент Worker
After=network.target postgresql.service

[Service]
Type=simple
User=smm-ai
Group=smm-ai
WorkingDirectory=/opt/smm-ai
Environment="PATH=/opt/smm-ai/venv/bin"
EnvironmentFile=/opt/smm-ai/.env
ExecStart=/opt/smm-ai/venv/bin/python -m app.worker
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

---

## Nginx Reverse Proxy (Optional)

Create `/etc/nginx/sites-available/smm-ai`:

```nginx
server {
    listen 80;
    server_name smm.example.com;

    # Redirect to HTTPS
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name smm.example.com;

    ssl_certificate /etc/letsencrypt/live/smm.example.com/fullchain.pem;
    ssl_certificate_key /etc/letsencrypt/live/smm.example.com/privkey.pem;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 300s;
        proxy_connect_timeout 75s;
        send_timeout 300s;
    }

    location /static/ {
        alias /opt/smm-ai/app/static/;
        expires 30d;
    }
}
```

Enable:
```bash
sudo ln -s /etc/nginx/sites-available/smm-ai /etc/nginx/sites-enabled/
sudo nginx -t && sudo systemctl reload nginx
```

---

## Database Migration

**⚠️ Single Migrator Rule:** Never run `alembic upgrade` in parallel from multiple processes/containers. If you have multiple app instances (e.g., in Docker Compose with `scale`), only **one** should run migrations. The others must wait for it to complete.

```bash
# Run Alembic migrations (single process only!)
python -m alembic upgrade head

# Or via CLI
python -m cli.main ...  # after bootstrap

# Verify
alembic current
alembic check
```

**Multiple migration heads / unexpected revision: stop and escalate.**
Do not choose a revision by its number, delete a migration or mark an unexecuted
revision as applied. Have the authorized maintainer reconcile the migration graph
in a separate branch, review schema-qualified DDL and verify staging/restore.
Do not run upgrade/downgrade/stamp/reset during observational triage. The commands
above belong only to a separately approved deployment, with a verified target and
one migrator; a migration file in GitHub does not prove the target DB revision.

---

## Bootstrap

After deployment, bootstrap the owner workspace:

```bash
# Create admin user (via CLI or API)
# Via API:
curl -X POST http://localhost:8000/api/v1/auth/register \
  -H "Content-Type: application/json" \
  -d '{"username": "admin", "email": "admin@example.com", "password": "securepass123"}'

# Set up platform credentials
python -m cli.main credentials set vk user_token --tenant owner
python -m cli.main credentials set telegram bot_token --tenant owner

# Add a source
# Via API or admin panel

# Run first digest
python -m cli.main digest send-now day
```

---

## Backup Strategy

### PostgreSQL

```bash
# Daily backup script
#!/bin/bash
BACKUP_DIR="/backups/postgres"
DATE=$(date +%Y%m%d_%H%M%S)
mkdir -p "$BACKUP_DIR"

pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" \
  | gzip > "$BACKUP_DIR/social_media_$DATE.sql.gz"

# Retention is a separately approved policy; preserve incident-related backups.
# Do not prune evidence or assume seven days is an adequate recovery window.
```

Crontab:
```
0 2 * * * /opt/smm-ai/scripts/backup.sh
```

### Database consistency and recovery evidence

Do not use a live PostgreSQL data-volume tar as a claimed consistent backup.
Choose an authorized logical backup or PostgreSQL-aware physical backup/snapshot
with an explicit consistency/WAL policy and independently verified restore.
Backup credentials and customer data are sensitive; do not attach dumps to issues.
Protect the encryption key separately so restored encrypted records remain usable.
Restore into an isolated approved target first, never over the working database
as a diagnostic step. Backup file presence is not restore acceptance.

---

## Observation and conservative recovery

### Scope and source contract

Prepared from dev `10212b5e6a9f7808c103a0265b1a0aa8741f0bf3` on 2026-10-09.
This section is a read-first incident procedure, not a recovery tool or authority
to restart, migrate, replay, send, delete or change permissions. Confirm operator
authority, tenant, environment and the actual deployed revision before inspecting
any records. Repository behavior may differ from a target not yet updated.

Source anchors:

- [Health routes](../app/core/health.py) and [main registration/startup](../app/main.py).
- [Runtime owners](../app/runtime.py), [actual Compose](../docker/docker-compose.yml).
- [Dispatcher](../app/jobs/dispatcher.py), [job manager](../app/models/managers/job_manager.py).
- [Digest job binding/finalization](../app/services/digest/job_delivery.py),
  [part state contract](../app/services/digest/checkpoints.py),
  [outcome summary](../app/services/digest/delivery_outcomes.py).

The runbook changes none of those files. Identity/queue/budget implementation
remains in its separately allocated lane. Existing owner-run checks are not rerun
or relabeled as a new recovery drill.

### 1. Confirm target and observe existing processes

Record UTC incident time, deployment SHA, service/container ownership and last
known good point. Use the actual unit name and Compose project/config of the
running target; do not start a second listener/scheduler/worker to diagnose it.
An API-only installation cannot prove background work is healthy.

On an authorized host, these commands inspect existing state only:

```bash
# Example unit name from the systemd sketch; substitute the actual installed unit.
systemctl show smm-ai.service --property=Id,ActiveState,SubState,MainPID,ExecMainStatus
# Repository Compose sketch, only if this is the config/project actually deployed.
docker compose -f docker/docker-compose.yml ps
```

Do not use `docker compose config`, process environments, `.env` dumps or command
lines containing DSNs/tokens as a support attachment. Do not launch `app.runtime`,
`app.worker`, a CLI task run or the update/bootstrap flow as a "health check".
Runtime startup seeds default tasks and starts a worker even with scheduling off.
SCHEDULER_ENABLED controls the scheduler, not pending-job processing/listening.
Stopping future schedules does not drain existing jobs or cancel in-flight sends.

### 2. Interpret the HTTP probes correctly

For an already running HTTP service, replace the address with its authorized
local/trusted endpoint. Commands discard bodies and show status only; do not
include embedded credentials in the URL or bypass TLS verification.

```bash
curl --silent --show-error --connect-timeout 2 --max-time 5 --output /dev/null --write-out '%{http_code}\n' http://127.0.0.1:8000/livez
curl --silent --show-error --connect-timeout 2 --max-time 5 --output /dev/null --write-out '%{http_code}\n' http://127.0.0.1:8000/readyz
```

| Probe | Meaning | Not evidence of |
| --- | --- | --- |
| `/livez` = 200 | HTTP process responds; no dependency query in this handler | DB, migrations, scheduler, worker, listener, provider or complete startup recovery |
| `/readyz` = 200 | One global Permission ORM count succeeded | Tenant rights, queue progress, all tables/schema revisions, digest delivery or production acceptance |
| `/readyz` = 503 | That DB read failed or timed out; response is a generic failure, not root cause | Proof that PostgreSQL itself is down rather than credentials/schema/permissions/driver failure |
| `/health` | Same readiness handler and 200/503 semantics, at the root path | A distinct process-only probe; `/api/v1/health` is not the contract used here |
| `/runtimez` = 200 | Recent running heartbeat plus held local owner lock in configured shared directory | DB health, useful queue/scheduler/listener progress, cross-host uniqueness or production acceptance |
| `/runtimez` = 503 | Presence is unconfirmed: unconfigured, missing, unavailable, invalid, starting, stopped or stale | Permission to start/restart runtime, provision storage, change permissions or reset DB |
| Client error / 000 | Address, network, proxy, process or client timeout needs investigation | A confirmed DB failure or a replay-safe message |

The response includes no-store and a UTC timestamp; DB readiness returns
status/database, liveness status only. The handler uses a two-second cooperative
async timeout; this is not a hard deadline for middleware/startup/uncooperative
drivers. A timeout is not a reason to repeatedly restart the DB/API.

`/runtimez` is a separate optional runtime-presence observation. See
[configuration and topology requirements](CONFIGURATION.md#optional-same-host-runtime-observation).
It is read-only, does not create files or query the DB, and always returns
`Cache-Control: no-store`. A 200 requires a held exclusive owner lock and a
`running` heartbeat no older than 60 seconds. A future timestamp, malformed or
oversized record cannot be green. Released ownership overrides even a recent
running record after process death; an unresponsive loop retaining ownership
becomes stale after the freshness window. During shutdown a heartbeat may still
be recent while cleanup drains; it does not promise continued task availability.

The response exposes only component, observation state, UTC heartbeat/response
time, age, random runtime ID and scheduler-enabled flag where evidence is valid;
no PID, hostname, path, DSN, customer data or raw exception is returned. This root
probe is infrastructure metadata, not a tenant/operator data endpoint. Keep it on
the same trusted monitoring boundary as existing probes. File reads run off the
HTTP event loop; the local filesystem is trusted, not a hard-deadline remote
storage dependency. Clock rollback conservatively reports invalid evidence.

Example read-only command for an **already running authorized service**:

```bash
curl --silent --show-error --connect-timeout 2 --max-time 5 http://127.0.0.1:8000/runtimez
```

Do not equate presence with queue progress. Follow scoped Job/delivery evidence
below even when `/runtimez` is green. No runtime startup, DB migration or deployment
was performed to validate this source change.

### 3. Inspect scoped outcomes, not only a green process

Use existing authorized UI/operator access in the affected workspace. Inspect
jobs/tasks/delivery records; do not create bypass SQL or export all tenants.
If existing access cannot expose sufficient evidence, escalate to the maintainer
for a separately authorized, schema-qualified read-only investigation. Do not
seed/grant roles or bypass identity checks to make a diagnostic succeed.

Minimal private evidence: tenant/task/job IDs; Job status, run_at, attempts,
max_attempts, locked_at/started_at/finished_at; related task last status/time;
original digest reference/run/generation/window; per-part statuses and aggregate
counts. Missing or malformed metadata is unknown, not an empty/unsent ledger.
Use the exact deployed contract: no new "partial Job status" is invented here.
Missing/NULL cost is unknown, not zero or proof that the summary can be rebuilt
for free; keep any known incurred cost and its original attribution.

| Symptom / evidence | Safe interpretation and next step | Do not do |
| --- | --- | --- |
| livez 200, readyz 503 | API responds but DB-read gate fails. Compare recent deployment/config changes and existing authorized DB diagnostics; escalate cause. | Loop restarts, stop the working DB for a test, reset/stamp/seed |
| Both 200, overdue backlog | API probe is not worker/scheduler health. Identify actual process owners; distinguish due jobs from future run_at/backoff. | Start a duplicate worker/listener, manually claim jobs |
| Old running job / unchanged locked_at | Age alone does not prove the worker or external effect is dead. Capture IDs, timestamps, attempt and process evidence; escalate ownership/race investigation. | Set pending/clear locked_at, call reap_stale, delete/recreate job |
| job_retry_scheduled | Existing automatic backoff may explain pending/future run_at. Observe original job rather than add another run. | Reduce run_at or force a second attempt while original may still run |
| job_returned_failure / terminal failure | Declared failure is not success; inspect bounded status/category under authorization and keep audit. | Treat absent notification as proof of success or replay authorization |
| job_completion_claim_lost / job_outcome_claim_lost | Old completion/failure write was refused by the checkpoint claim guard; ownership must be reconciled. | Repair result/status manually or assume generic queue fencing is solved |
| Collect run done with per-source errors/skips | Job completion need not mean every source succeeded. Preserve original partial/auth_required result and assess sources individually. | Reclassify partial results or overwrite the original audit |
| Summary build started without bound snapshot | `summary_build_requires_reconciliation`: cost/build may already have happened. Keep reservation and investigate. | Rebuild the summary, clear build_started or create another snapshot |
| Original request/window/run changed or missing | Original identity/reference cannot be trusted for resumption. Retain records and escalate. | Copy a payload reference, change period/window/generation or force_refresh |

Generic worker polling currently calls reap_stale (default 30 minutes) and
cleanup_done (default 24 hours) before claiming. Those are existing automatic
mutations, not commands in this runbook and not queue-wide leases/atomicity.
Do not invoke polling/drain as a read-only inspection. Original Job references
can be pruned; request owner-approved evidence preservation/controlled containment
when necessary. This documentation does not implement retention or pause controls.

### 4. Treat uncertain external delivery as possibly accepted

Distinguish Job pending/running/done/failed from the structured digest outcome
and each frozen target/part. Summaries count in_flight together with uncertain;
only all-confirmed parts yield `sent`. A failed Job does not prove no part sent.

| Frozen part / outcome | Meaning and safe handling |
| --- | --- |
| sent | Durable acknowledgement recorded. Never send it again as an incident test. |
| pending / rejected | Known-unsent candidates under the validated original ledger. Not operator replay permission: current binding, claim, request and generation still must validate. |
| in_flight / uncertain | The messenger MAY have accepted it, including cancellation/timeout or failed receipt commit. Preserve intent and existing IDs; inspect authorized recipient/provider evidence privately. No blind replay. |
| blocked | Stop for authorization/availability/operator review. Do not reactivate a revoked binding or retarget the frozen run. |
| partial | Some parts sent; inspect the complete ledger, not just the aggregate label. |
| unknown / NULL / malformed legacy state | Not proof of unsent content. Refuse inferred receipts; escalate. |

The publisher can automatically retry some known-unsent original pending jobs
according to its contract. This runbook offers **no manual resume/force command**.
Original job/run/window/generation/frozen recipients and content hashes must not
be replaced. If any acceptance is uncertain, keep the current evidence and get a
separate operator decision; no reset/new-generation/reconciliation UI is claimed.

`rate_limit_requires_operator_delay` / HTTP 429 stops automatic retry in this
publisher; verified Retry-After is not exposed by its current transport contract.
Do not guess a delay or hammer the provider. Ask the responsible operator to
reconcile provider scope and authorize a future change. Per-call pacing is not
a distributed limiter. Do not toggle DIGEST_CHECKPOINT_DELIVERY_ENABLED during
triage: rollback does not permit bound/reserved work to use the legacy sender.

### 5. Preserve safe evidence and escalate before changing state

Read only the necessary local log time window under operator authority. Logs,
Job.error, task.last_error, payloads, summaries, provider/ORM messages and screenshots
may contain secrets/customer data despite bounded dispatcher/handler warnings.
Do not paste raw logs, DSNs, `.env`, SQL parameters, report text, recipient addresses,
tokens or backups into an issue. Log grepping is not a reliable sanitization filter.

Share only a manually reviewed packet: incident/time/environment/revision;
process ownership; HTTP status; numeric tenant/task/job/run correlation and exact
static categories/counts; last known good point; suspected scope; steps already
taken; unresolved acceptance/ownership; requested owner decision. Keep necessary
private originals under the existing authorized retention policy, not as a new
public export. Absent delivery evidence is not negative delivery evidence.

Escalation/change gate:

1. Preserve audit/reservation/receipts; identify whether work is still in flight.
2. Ask the owner to authorize any drain/stop/restart/config/schema change and its
   exact scope. No automatic process intervention is performed by this guide.
3. For possible external acceptance, reconciliation comes before resumption.
   Any separately approved send must account for duplicate risk and recipients.
4. For DB/deployment problems, use a reviewed single-migrator/change plan and
   isolated restore validation. Never overwrite the working DB as a diagnostic.
5. Record decision, actor, exact version and resulting evidence before closing.
   Process recovery, job outcome, recipient delivery and release acceptance are
   separate; do not close on a green health probe alone.

### 6. Prepared tabletop review — not executed checks

Owner/operator can review these NEW documentation cases without invoking a live
fault or rerunning already completed application checks:

- [ ] DB-read 503 with livez 200 does not trigger restart/reset/stamp instructions.
- [ ] Healthy HTTP plus backlog checks process ownership, not a duplicate worker.
- [ ] Old running/claim-lost evidence produces escalation, not row repair/replay.
- [ ] Retry/backoff uses the original job; terminal/partial outcomes remain honest.
- [ ] in_flight/uncertain/NULL/legacy history never becomes a manual test send.
- [ ] Summary build uncertainty, changed scope/window and 429 stop for review.
- [ ] Shared incident packet is minimized/redacted; private receipts are preserved.
- [ ] Restart/migration/restore/activation remain separately approved changes.

No probe, service command, application test, DB, provider or messenger operation
was executed while authoring this runbook. It does not certify an environment or
supply a new recovery API. Review the Markdown and compare source contracts; live
staging/fault/restore exercises require their own approval and evidence.

---

## Update Procedure — separately authorized change, not incident triage

The following historical sketch is not an automatic recovery action. Verify
target/revision, migration plan, process ownership and in-flight deliveries; obtain
owner approval before executing installation/migration/restart commands. The
supported staging/production Compose profiles run migrations only through the
explicit deployment step; the older development Compose sketch still embeds a
startup migration and must not be used for production. Do not restart a generic
worker during ambiguous delivery merely because the HTTP process is unhealthy.

```bash
# 1. Pull latest code
cd /opt/smm-ai
git pull

# 2. Install deps
source venv/bin/activate
pip install -r requirements.txt

# 3. Run migrations
python -m alembic upgrade head

# 4. Restart service
sudo systemctl restart smm-ai
sudo systemctl restart smm-ai-worker  # if applicable

# 5. Verify
curl http://localhost:8000/readyz
journalctl -u smm-ai --since "5 minutes ago"
```

---

## Troubleshooting

Use [Observation and conservative recovery](#observation-and-conservative-recovery)
for incidents. Do not use direct credential-test sends, deprecated env digest
channel IDs, role/DB seeding or broad SQL dumps as diagnostic shortcuts. Recipient
authority is the active owned workspace channel binding, not an env destination
or operator chat. A separate approved deployment/credential change may use its
reviewed contract; no such action is included in this runbook.

---

## Release gates and first-host checklist

Repository preparation closes P7-T05/P7-T06 only. It does not waive the following
host-specific release gates or authorize production migrations, retention, sends,
or provider calls. Keep deployment-enable variables false until the host is ready.

| Gate | Required evidence / owner action |
| --- | --- |
| Host | Provision VPS; install Docker Engine, Compose v2 supporting `up --wait`, host Nginx and Python 3; verify RAM/disk, firewall (SSH/80/443), non-overlapping subnets and one runtime owner. |
| Secrets | Separate mode-600 staging/production env files; distinct keys/passwords; private offsite encryption-key custody; read-only GHCR pull login on VPS. Never rotate the production Fernet key during upgrade. |
| Ingress | Select DNS names, obtain certificates, replace host-vhost placeholders; run real `nginx -t`, verify HTTPS, secure cookies, `/app`, `/admin`, and rate-limit storage through the real proxy chain. Restrict staging to approved operator ingress (VPN/IP allowlist/auth). |
| GitHub | Create `staging`/`production` Environments, production required reviewers and deployment branch restriction `master`; set SSH secrets and fixed deployment directory variable. Keep enable flags repository-scoped; environment variables cannot enable a job before its environment is selected. |
| Schema | Complete active [Q-14](tasks/q-14.md) migration baseline work before selecting the first production revision. Validate that exact revision on the approved test schema; obtain separate apply approval. Set repository variable `PRODUCTION_MIGRATIONS_APPROVED_SHA` to that full commit only. A later commit requires fresh approval. |
| Recovery | Schedule protected logical backups with offsite copies and separately secured encryption keys; select RPO/RTO, retention and monitoring. Run an isolated verified restore before setting `PRODUCTION_BACKUP_READY=true`. Local pre-migration snapshots alone do not satisfy this gate. |
| Operations | Execute [P7-T02](tasks/p7-t02.md) on staging: install, backup, restore, verify revision/schema, HTTP and controlled operation; record exact image digest and cleanup. |
| Delivery | Execute [P4-T01](tasks/p4-t01.md) operator recovery without duplicate sends; use approved test recipients and preserve receipts. |
| Runtime / retention | Review and approve [P6-T01](tasks/p6-t01.md) cutoff, backup and rollout before explicitly enabling `--profile runtime`; confirm no other systemd/container runtime owner exists. |
| Billing | Complete [P2-T09](tasks/p2-t09.md) production billing verification before billable public use. Provider permission and availability remain separate integration gates. |

The workflow passes the approved digest to `deploy_vps.sh`. For a separately
approved manual production rollout, set `PRODUCTION_MIGRATIONS_APPROVED_IMAGE`
to the same immutable digest supplied as the second argument; the script rejects
missing/mismatched approval before contacting Docker. `APP_IMAGE` may override
the stored successful digest for a reviewed operator command. Never use mutable
`latest` tags or run direct production Alembic as a diagnostic.

### Rollback and interrupted rollout

Record the previous image digest and database revision before a rollout. The
stored digest changes only after successful API readiness and any intended
runtime restart; after a failed migration it still identifies the last successful
release, not necessarily the currently running containers. Inspect both states.

For a backward-compatible schema, stop the active runtime/API and use the
wrappers with `APP_IMAGE=<previous-reviewed-digest>` to pull and replace HTTP and
proxy with `up -d --no-deps --force-recreate api nginx`; verify `/readyz` and the
application before restarting the one previously active runtime. Record the
successful rollback digest explicitly. This API rollback does not downgrade the
schema. Do not invoke `deploy_vps.sh` with an older image as a schema rollback.
For an incompatible or partially applied migration, keep writers stopped and use
an approved restore/recovery plan against an isolated target first. No automatic
Alembic downgrade or destructive production restore is provided. A failed health
probe does not authorize replay, pruning or another provider call.

No VPS, real TLS certificate, live database restore, production rollout, or
external-provider smoke test was available during repository preparation. Hosted
CI configuration parsing and image building do not certify a running environment.
Those checks remain explicitly unrun and their task rows remain open.
