#!/usr/bin/env bash
set -Eeuo pipefail

usage() {
  echo 'Usage: bash scripts/ops/deploy_vps.sh <staging|production> <ghcr-image@sha256:64hex>' >&2
  exit 2
}
[[ $# == 2 ]] || usage
target="$1"
image="$2"
if [[ ! "$image" =~ ^ghcr\.io/starck43/social-media-ai@sha256:[0-9a-f]{64}$ ]]; then
  echo 'Refusing a non-digest-pinned/unexpected GHCR image reference.' >&2
  exit 2
fi
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
export APP_IMAGE="$image"

case "$target" in
  staging)
    compose="$ROOT/scripts/ops/staging_compose.sh"
    bash "$compose" pull api
    bash "$compose" up -d db redis
    # Staging migrations affect only the isolated staging volume.
    bash "$compose" run --rm --no-deps api alembic upgrade head
    bash "$compose" up -d api nginx
    health_port=18000
    ;;
  production)
    compose="$ROOT/scripts/ops/production_compose.sh"
    bash "$compose" pull api
    bash "$compose" up -d db redis
    # Keep the current API running if backup or migration fails.
    APP_IMAGE="$image" bash "$ROOT/scripts/ops/backup_production_db.sh"
    bash "$compose" run --rm --no-deps api alembic upgrade head
    bash "$compose" up -d api nginx
    # Update runtime only if the owner explicitly enabled its Compose profile earlier.
    if [[ -n "$(docker compose --project-name social-ai-production --env-file "$ROOT/.env.production"         --file "$ROOT/docker/docker-compose.production.yml" --profile runtime ps -q runtime)" ]]; then
      bash "$compose" --profile runtime up -d runtime
    fi
    health_port=8226
    ;;
  *) usage ;;
esac

python3 - "$health_port" <<'PYHEALTH'
import sys
import time
from urllib.error import URLError
from urllib.request import urlopen

url = f"http://127.0.0.1:{sys.argv[1]}/readyz"
for _ in range(30):
    try:
        with urlopen(url, timeout=3) as response:
            if response.status == 200:
                print(f"Deployment healthy: {url}")
                raise SystemExit(0)
    except (OSError, URLError):
        time.sleep(2)
raise SystemExit("Deployment did not become ready; inspect service status/logs before rollback.")
PYHEALTH
