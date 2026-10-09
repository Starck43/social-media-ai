# Deployment Guide

> **Status:** deployment examples are drafts, not a verified production profile.
> An observation/recovery runbook is prepared below against dev `10212b5e`.
> It is documentation, not executed recovery, staging acceptance or deployment.

During an incident start at [Observation and conservative recovery](#observation-and-conservative-recovery),
not installation/bootstrap/update commands. Starting or restarting a process can
write data or cause external effects. The checked-in [Compose](../docker/docker-compose.yml)
contains `db` and `api` only; `api` starts with `alembic upgrade head` before HTTP.
It does not start the scheduler, worker or messenger listener. Do not restart it
as a supposedly read-only migration diagnostic. The examples below are historical
sketches; do not copy their service names/topology into an incident response.

## Prerequisites

- VPS with Ubuntu 22.04+ (or Debian 12+)
- 2 vCPU, 4 GB RAM minimum
- PostgreSQL 15+ (or managed PostgreSQL)
- Python 3.12+
- Domain name (optional, for HTTPS)

---

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
FROM python:3.12-slim

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

Copy from [CONFIGURATION.md](./CONFIGURATION.md) and fill in values.

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
sudo apt install -y python3.12 python3.12-venv python3.12-dev libpq-dev

# Create virtualenv
python3.12 -m venv /opt/smm-ai/venv
source /opt/smm-ai/venv/bin/activate

# Install project
cd /opt/smm-ai
git clone https://github.com/.../social-media-ai.git .
pip install -r requirements.txt

# Install telethon (optional, for L2 Telegram)
pip install telethon
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
| Client error / 000 | Address, network, proxy, process or client timeout needs investigation | A confirmed DB failure or a replay-safe message |

The response includes no-store and a UTC timestamp; DB readiness returns
status/database, liveness status only. The handler uses a two-second cooperative
async timeout; this is not a hard deadline for middleware/startup/uncooperative
drivers. A timeout is not a reason to repeatedly restart the DB/API.

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

| Symptom / evidence | Safe interpretation and next step | Do not do |
| --- | --- | --- |
| livez 200, readyz 503 | API responds but DB-read gate fails. Compare recent deployment/config changes and existing authorized DB diagnostics; escalate cause. | Loop restarts, stop the working DB for a test, reset/stamp/seed |
| Both 200, overdue backlog | API probe is not worker/scheduler health. Identify actual process owners; distinguish due jobs from future run_at/backoff. | Start a duplicate worker/listener, manually claim jobs |
| Old running job / unchanged locked_at | Age alone does not prove the worker or external effect is dead. Capture IDs, timestamps, attempt and process evidence; escalate ownership/race investigation. | Set pending/clear locked_at, call reap_stale, delete/recreate job |
| job_retry_scheduled | Existing automatic backoff may explain pending/future run_at. Observe original job rather than add another run. | Reduce run_at or force a second attempt while original may still run |
| job_returned_failure / terminal failure | Declared failure is not success; inspect bounded status/category under authorization and keep audit. | Treat absent notification as proof of success or replay authorization |
| job_*_claim_lost | Old completion/failure write was refused by the checkpoint claim guard; ownership must be reconciled. | Repair result/status manually or assume generic queue fencing is solved |
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
checked-in API Compose auto-runs a migration at startup. Do not restart a generic
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

## Checklist for First Deployment

- [ ] VPS provisioned with Ubuntu 22.04+
- [ ] PostgreSQL installed and secured
- [ ] Python 3.12+ installed
- [ ] Project cloned and `requirements.txt` installed
- [ ] `.env` configured with all required values
- [ ] `SECRET_KEY` and `CREDENTIALS_KEY` generated
- [ ] Alembic migrations run (`alembic upgrade head`)
- [ ] Admin user created
- [ ] Platform credentials set up (`credentials set`)
- [ ] Sources configured
- [ ] Tasks created (`task add`)
- [ ] Service started (systemd or docker compose)
- [ ] Health check passes
- [ ] First digest sent successfully (`digest send-now day`)
- [ ] Agent responds in Telegram/MAX
- [ ] Backup script configured
- [ ] Monitoring configured
- [ ] Firewall rules set (only 80/443/22 open)
