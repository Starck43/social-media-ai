#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
ENV_FILE="$ROOT/.env.staging"
COMPOSE_FILE="$ROOT/docker/docker-compose.staging.yml"

if [[ ! -f "$ENV_FILE" ]]; then
  printf 'Missing %s; copy .env.staging.example and fill staging-only secrets.\n' "$ENV_FILE" >&2
  exit 2
fi
mode="$(stat -c '%a' "$ENV_FILE")"
if (( (8#$mode & 077) != 0 )); then
  echo '.env.staging must be private (chmod 600); refusing to start.' >&2
  exit 2
fi
python3 - "$ENV_FILE" <<'PYENV'
import base64
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

values = {}
for raw in Path(sys.argv[1]).read_text().splitlines():
    line = raw.strip()
    if not line or line.startswith("#"):
        continue
    key, sep, value = line.partition("=")
    if not sep or not key.strip():
        raise SystemExit("Invalid .env.staging assignment; refusing to start.")
    values[key.strip()] = value.strip()
required = {
    "ENVIRONMENT", "DEBUG", "SECRET_KEY", "CREDENTIALS_KEY",
    "BACKEND_CORS_ORIGINS", "POSTGRES_USER", "POSTGRES_PASSWORD",
    "POSTGRES_DB", "POSTGRES_URL", "DB_SCHEMA", "DB_POOL",
    "SCHEDULER_ENABLED",
}
allowed = required
missing = sorted(required - values.keys())
extra = sorted(values.keys() - allowed)
if missing or extra:
    raise SystemExit(".env.staging must contain only the documented staging keys.")
if any(not values[key] or "replace-with-" in values[key] for key in required):
    raise SystemExit("Replace every .env.staging placeholder before starting.")
if values["ENVIRONMENT"] != "production" or values["DEBUG"].lower() != "false":
    raise SystemExit("Staging must use ENVIRONMENT=production and DEBUG=false.")
if values["SCHEDULER_ENABLED"].lower() != "false":
    raise SystemExit("SCHEDULER_ENABLED must remain false in this API-only profile.")
if values["POSTGRES_USER"] != "staging" or values["POSTGRES_DB"] != "social_ai_staging" or values["DB_SCHEMA"] != "public":
    raise SystemExit("Staging database/schema must match the isolated profile.")
if len(values["SECRET_KEY"]) < 64:
    raise SystemExit("SECRET_KEY must be freshly generated and at least 64 characters.")
if not values["POSTGRES_PASSWORD"].isascii() or len(values["POSTGRES_PASSWORD"]) != 64 or any(c not in "0123456789abcdef" for c in values["POSTGRES_PASSWORD"]):
    raise SystemExit("POSTGRES_PASSWORD must be 64 lowercase hex characters.")
try:
    key_bytes = base64.urlsafe_b64decode(values["CREDENTIALS_KEY"].encode("ascii"))
except (ValueError, UnicodeError):
    raise SystemExit("CREDENTIALS_KEY must be a valid, staging-only Fernet key.")
if len(key_bytes) != 32 or base64.urlsafe_b64encode(key_bytes).decode("ascii") != values["CREDENTIALS_KEY"]:
    raise SystemExit("CREDENTIALS_KEY must be a valid, staging-only Fernet key.")
try:
    origins = json.loads(values["BACKEND_CORS_ORIGINS"])
except (TypeError, ValueError):
    raise SystemExit("BACKEND_CORS_ORIGINS must be a JSON list for the loopback staging origin.")
if not isinstance(origins, list) or any(origin not in {"http://localhost:18000", "http://127.0.0.1:18000"} for origin in origins):
    raise SystemExit("BACKEND_CORS_ORIGINS may contain only loopback staging origins.")
try:
    url = urlsplit(values["POSTGRES_URL"])
    correct_target = (url.scheme == "postgresql+asyncpg" and url.hostname == "db"
                      and url.port == 5432 and url.username == values["POSTGRES_USER"]
                      and url.password == values["POSTGRES_PASSWORD"]
                      and url.path == "/social_ai_staging")
except ValueError:
    correct_target = False
if not correct_target:
    raise SystemExit("POSTGRES_URL must target only the staging db service/database.")
PYENV
if ! command -v docker >/dev/null 2>&1; then
  echo 'Docker is required to run the staging Compose stack.' >&2
  exit 127
fi

exec docker compose \
  --project-name social-ai-staging \
  --env-file "$ENV_FILE" \
  --file "$COMPOSE_FILE" \
  "$@"
