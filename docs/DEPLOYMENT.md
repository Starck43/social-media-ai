# Deployment Guide

> **Status:** draft — deployment artifacts not yet created.
> This guide provides the reference for when deployment is ready.

## Prerequisites

- VPS with Ubuntu 22.04+ (or Debian 12+)
- 2 vCPU, 4 GB RAM minimum
- PostgreSQL 15+ (or managed PostgreSQL)
- Python 3.12+
- Domain name (optional, for HTTPS)

---

## Option A: Docker Compose (Recommended)

### 1. Project Structure

```
social-media-ai/
├── docker-compose.yml
├── .env                      # Secrets (never committed)
├── Dockerfile
├── requirements.txt
├── alembic.ini
├── migrations/
├── app/
├── cli/
└── ...
```

### 2. Dockerfile

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

### 3. docker-compose.yml

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
curl http://localhost:8000/api/v1/health
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
Description=Social Media AI Runtime
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
Description=Social Media AI Worker
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

```bash
# Run Alembic migrations
python -m alembic upgrade head

# Or via CLI
python -m cli.main ...  # after bootstrap

# Verify
alembic current
alembic check
```

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

# Keep last 7 days
find "$BACKUP_DIR" -name "social_media_*.sql.gz" -mtime +7 -delete
```

Crontab:
```
0 2 * * * /opt/smm-ai/scripts/backup.sh
```

### Docker Volume Backup

```bash
docker run --rm \
  -v smm-ai_pgdata:/data \
  -v /backups:/backup \
  alpine tar czf /backup/pgdata_$(date +%Y%m%d).tar.gz -C /data .
```

---

## Monitoring

### Health Check

```bash
curl -s http://localhost:8000/api/v1/health | jq
```

Expected response:
```json
{"status": "ok", "database": "connected"}
```

### Log Monitoring

```bash
# Systemd
journalctl -u smm-ai -f

# Docker
docker compose logs -f app

# Check scheduler status
python -m cli.main schedule list

# Check job queue
# Query jobs table directly
```

### Uptime Monitoring

Configure an external health check (e.g., UptimeRobot, Pingdom):
```
GET https://smm.example.com/api/v1/health
Expected: 200 OK
```

---

## Update Procedure

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
curl http://localhost:8000/api/v1/health
journalctl -u smm-ai --since "5 minutes ago"
```

---

## Troubleshooting

### Service won't start

```bash
# Check logs
journalctl -u smm-ai -n 100 --no-pager

# Check port
ss -tlnp | grep 8000

# Check DB connection
psql "$POSTGRES_URL" -c "SELECT 1"
```

### Scheduler not running

```bash
# Check if scheduler is enabled
grep SCHEDULER_ENABLED .env

# Check schedules
python -m cli.main schedule list

# Check job queue
# Query: SELECT * FROM jobs WHERE status = 'pending' ORDER BY run_at;
```

### Agent not responding

```bash
# Check channel configuration
python -m cli.main credentials test telegram
python -m cli.main credentials test max

# Check tenant binding
# Query: SELECT * FROM tenant_channels WHERE is_active = true;

# Check agent logs
journalctl -u smm-ai -g "agent" --since "1 hour ago"
```

### Telegram not receiving messages

```bash
# Verify bot token
python -m cli.main credentials test telegram

# Check digest channel ID
grep TELEGRAM_DIGEST_CHANNEL_ID .env

# Test send
curl -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
  -d "chat_id=${TELEGRAM_DIGEST_CHANNEL_ID}&text=Test from $(hostname)"
```

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
- [ ] Schedules created (`schedule add`)
- [ ] Service started (systemd or docker compose)
- [ ] Health check passes
- [ ] First digest sent successfully (`digest send-now day`)
- [ ] Agent responds in Telegram/MAX
- [ ] Backup script configured
- [ ] Monitoring configured
- [ ] Firewall rules set (only 80/443/22 open)
