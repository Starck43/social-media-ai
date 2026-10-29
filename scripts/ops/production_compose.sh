#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
ENV_FILE="$ROOT/.env.production"
COMPOSE_FILE="$ROOT/docker/docker-compose.production.yml"
IMAGE_FILE="$ROOT/.deployed-image.production"

if [[ ! -f "$ENV_FILE" ]]; then
  printf 'Missing %s; copy .env.production.example and configure production-only values.\n' "$ENV_FILE" >&2
  exit 2
fi
mode="$(python3 -c 'import os, sys; print(format(os.stat(sys.argv[1]).st_mode & 0o777, "o"))' "$ENV_FILE")"
if (( (8#$mode & 077) != 0 )); then
  echo '.env.production must be private (chmod 600); refusing to continue.' >&2
  exit 2
fi
python3 - "$ENV_FILE" <<'PYENV'
import base64
import ipaddress
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
        raise SystemExit("Invalid .env.production assignment; refusing to continue.")
    values[key.strip()] = value.strip()
required = {
    "ENVIRONMENT", "DEBUG", "SECRET_KEY", "CREDENTIALS_KEY", "BACKEND_CORS_ORIGINS",
    "POSTGRES_USER", "POSTGRES_PASSWORD", "POSTGRES_DB", "POSTGRES_URL", "DB_SCHEMA",
    "DB_POOL", "REDIS_URL", "DOCKER_SUBNET", "NGINX_CONTAINER_IP", "SCHEDULER_ENABLED",
    "DATA_RETENTION_MONTHS",
}
if required - values.keys():
    raise SystemExit(".env.production is missing required documented keys.")
if any(not values[key] or "replace-with-" in values[key] for key in required - {"BACKEND_CORS_ORIGINS"}):
    raise SystemExit("Replace every required .env.production placeholder before use.")
if values["ENVIRONMENT"] != "production" or values["DEBUG"].lower() != "false":
    raise SystemExit("Production requires ENVIRONMENT=production and DEBUG=false.")
if len(values["SECRET_KEY"]) < 64:
    raise SystemExit("SECRET_KEY must be newly generated and at least 64 characters.")
password = values["POSTGRES_PASSWORD"]
if not password.isascii() or len(password) != 64 or any(ch not in "0123456789abcdef" for ch in password):
    raise SystemExit("POSTGRES_PASSWORD must be 64 lowercase hex characters.")
try:
    key = values["CREDENTIALS_KEY"]
    decoded = base64.urlsafe_b64decode(key.encode("ascii"))
except (ValueError, UnicodeError):
    raise SystemExit("CREDENTIALS_KEY must be a valid, stable production Fernet key.")
if len(decoded) != 32 or base64.urlsafe_b64encode(decoded).decode("ascii") != key:
    raise SystemExit("CREDENTIALS_KEY must be a valid, stable production Fernet key.")
if values["POSTGRES_USER"] != "social_ai" or values["POSTGRES_DB"] != "social_ai" or values["DB_SCHEMA"] != "public":
    raise SystemExit("Production database user/database/schema must match the documented isolated profile.")
try:
    url = urlsplit(values["POSTGRES_URL"])
    valid_db = (url.scheme == "postgresql+asyncpg" and url.hostname == "db" and url.port == 5432
                and url.username == values["POSTGRES_USER"] and url.password == password
                and url.path == "/social_ai")
except ValueError:
    valid_db = False
if not valid_db:
    raise SystemExit("POSTGRES_URL must target only the production db service/database.")
if values["REDIS_URL"] != "redis://redis:6379/0":
    raise SystemExit("REDIS_URL must target the private production Redis service.")
try:
    network = ipaddress.ip_network(values["DOCKER_SUBNET"], strict=True)
    proxy_ip = ipaddress.ip_address(values["NGINX_CONTAINER_IP"])
except ValueError:
    raise SystemExit("DOCKER_SUBNET and NGINX_CONTAINER_IP must be valid network values.")
if proxy_ip not in network or proxy_ip in {network.network_address, network.broadcast_address}:
    raise SystemExit("NGINX_CONTAINER_IP must be a usable address inside DOCKER_SUBNET.")
try:
    origins = json.loads(values["BACKEND_CORS_ORIGINS"])
except (TypeError, ValueError):
    raise SystemExit("BACKEND_CORS_ORIGINS must be a JSON list (empty is valid for same-origin UI).")
if not isinstance(origins, list) or any(not isinstance(origin, str) for origin in origins):
    raise SystemExit("BACKEND_CORS_ORIGINS must be a JSON list of strings.")
try:
    if int(values["DATA_RETENTION_MONTHS"]) < 1:
        raise ValueError
except ValueError:
    raise SystemExit("DATA_RETENTION_MONTHS must be an integer >= 1.")
if values["SCHEDULER_ENABLED"].lower() not in {"true", "false"}:
    raise SystemExit("SCHEDULER_ENABLED must be true or false.")
PYENV
APP_IMAGE="${APP_IMAGE:-}"
if [[ -z "$APP_IMAGE" && -f "$IMAGE_FILE" ]]; then
  IFS= read -r APP_IMAGE < "$IMAGE_FILE" || true
fi
if [[ ! "$APP_IMAGE" =~ ^ghcr\.io/starck43/social-media-ai@sha256:[0-9a-f]{64}$ ]]; then
  echo 'APP_IMAGE must name the immutable GHCR image digest.' >&2
  exit 2
fi
export APP_IMAGE
if ! command -v docker >/dev/null 2>&1; then
  echo 'Docker is required to run the production Compose stack.' >&2
  exit 127
fi
exec docker compose   --project-name social-ai-production   --env-file "$ENV_FILE"   --file "$COMPOSE_FILE"   "$@"
