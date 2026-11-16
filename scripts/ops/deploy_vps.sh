#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

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
if [[ "$target" == production && "${PRODUCTION_MIGRATIONS_APPROVED_IMAGE:-}" != "$image" ]]; then
  echo 'Production migration requires an explicit approval for this exact image digest.' >&2
  exit 2
fi
if [[ "$target" != staging && "$target" != production ]]; then
  usage
fi
ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
state_dir="${XDG_STATE_HOME:-$HOME/.local/state}/social-media-ai"
mkdir -m 700 -p -- "$state_dir"
chmod 700 "$state_dir"
exec 9>"$state_dir/deploy-$target.lock"
if ! flock -n 9; then
  echo "Another $target deployment is in progress; refusing to race its migration." >&2
  exit 2
fi
export APP_IMAGE="$image"

maintenance=false
image_tmp=''
on_error() {
  status=$?
  if [[ -n "$image_tmp" ]]; then
    rm -f -- "$image_tmp" || true
  fi
  if [[ "$maintenance" == true ]]; then
    echo 'Deployment failed after maintenance mode began; inspect API/runtime state, backup and migration state before recovery.' >&2
  fi
  exit "$status"
}
trap on_error ERR

case "$target" in
  staging)
    compose="$ROOT/scripts/ops/staging_compose.sh"
    bash "$compose" pull api
    bash "$compose" up -d --wait db redis
    # A single migrator runs while the staging API is stopped.
    maintenance=true
    bash "$compose" stop api
    bash "$compose" run --rm --no-deps api alembic upgrade head
    bash "$compose" up -d --no-deps --force-recreate api
    bash "$compose" up -d --no-deps --force-recreate nginx
    health_port=18000
    ;;
  production)
    compose="$ROOT/scripts/ops/production_compose.sh"
    bash "$compose" pull api
    bash "$compose" up -d --wait db redis
    # Preserve a local recovery point before stopping services or migrating.
    APP_IMAGE="$image" bash "$ROOT/scripts/ops/backup_production_db.sh"

    runtime_was_running=false
    runtime_id="$(bash "$compose" --profile runtime ps --status running -q runtime)"
    if [[ -n "$runtime_id" ]]; then
      runtime_was_running=true
    fi
    maintenance=true
    bash "$compose" stop api
    if [[ "$runtime_was_running" == true ]]; then
      bash "$compose" --profile runtime stop runtime
    fi
    bash "$compose" run --rm --no-deps api alembic upgrade head
    bash "$compose" up -d --no-deps --force-recreate api
    # Recreate the proxy so it resolves the replacement API container address.
    bash "$compose" up -d --no-deps --force-recreate nginx
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

if [[ "$target" == production && "$runtime_was_running" == true ]]; then
  bash "$compose" --profile runtime up -d --no-deps --force-recreate runtime
fi

# Record only a rollout that has passed readiness. The wrappers use this digest
# for later Compose operations, including logical backups.
image_tmp="$ROOT/.deployed-image.$target.tmp.$$"
printf '%s\n' "$image" > "$image_tmp"
chmod 600 "$image_tmp"
mv -f -- "$image_tmp" "$ROOT/.deployed-image.$target"
maintenance=false
trap - ERR
