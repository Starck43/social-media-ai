#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

usage() {
  cat >&2 <<'USAGE'
Usage: restore_staging_db.sh <custom-format.dump> \
  --approved-sanitized-copy --confirm-overwrite-staging

The source must be an owner-approved, sanitized PostgreSQL custom-format dump
with a matching .sha256 file. This replaces only the dedicated staging database.
USAGE
}

archive=''
approved=false
confirmed=false
for arg in "$@"; do
  case "$arg" in
    --approved-sanitized-copy) approved=true ;;
    --confirm-overwrite-staging) confirmed=true ;;
    --*) usage; exit 2 ;;
    *)
      if [[ -n "$archive" ]]; then usage; exit 2; fi
      archive="$arg"
      ;;
  esac
done
if [[ -z "$archive" || "$approved" != true || "$confirmed" != true ]]; then
  usage
  exit 2
fi
if [[ ! -f "$archive" || -L "$archive" ]]; then
  echo 'Archive must be a regular, non-symlink file.' >&2
  exit 2
fi
if ! command -v sha256sum >/dev/null 2>&1; then
  echo 'sha256sum is required to verify the archive.' >&2
  exit 127
fi
archive="$(realpath -- "$archive")"
checksum="${archive}.sha256"
if [[ ! -f "$checksum" || -L "$checksum" ]]; then
  echo 'A matching <archive>.sha256 manifest is required.' >&2
  exit 2
fi
archive_base="$(basename -- "$archive")"
if [[ ! "$archive_base" =~ ^[[:alnum:]_.-]+\.dump$ ]]; then
  echo 'Archive filename must use only letters, digits, dot, underscore, or hyphen.' >&2
  exit 2
fi
manifest="$(< "$checksum")"
if [[ ! "$manifest" =~ ^([[:xdigit:]]{64})[[:space:]][[:space:]]([[:alnum:]_.-]+\.dump)$ ]]; then
  echo 'Checksum manifest must contain one SHA-256 line.' >&2
  exit 2
fi
if [[ "${BASH_REMATCH[2]}" != "$archive_base" ]]; then
  echo 'Checksum manifest must name the exact archive file.' >&2
  exit 2
fi
expected_hash="${BASH_REMATCH[1]}"
actual_hash="$(sha256sum -- "$archive" | cut -d ' ' -f 1)"
if [[ "$actual_hash" != "$expected_hash" ]]; then
  echo 'Archive SHA-256 does not match its manifest.' >&2
  exit 2
fi

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
# Confirm format before creating a safety backup or stopping the API.
"$ROOT/scripts/ops/staging_compose.sh" exec -T db pg_restore --list < "$archive" >/dev/null
# Preserve the current staging database before the destructive replacement.
"$ROOT/scripts/ops/backup_staging_db.sh"
# Leave the API stopped: it runs Alembic automatically on startup.
"$ROOT/scripts/ops/staging_compose.sh" stop api
"$ROOT/scripts/ops/staging_compose.sh" exec -T db sh -ec \
  'dropdb --if-exists --maintenance-db=postgres --username="$POSTGRES_USER" "$POSTGRES_DB" && createdb --maintenance-db=postgres --encoding=UTF8 --template=template0 --username="$POSTGRES_USER" "$POSTGRES_DB"'
"$ROOT/scripts/ops/staging_compose.sh" exec -T db sh -ec \
  'exec pg_restore --exit-on-error --single-transaction --no-owner --no-acl --username="$POSTGRES_USER" --dbname="$POSTGRES_DB"' \
  < "$archive"
printf 'Staging restore completed. API remains stopped pending revision/data review.\n'
