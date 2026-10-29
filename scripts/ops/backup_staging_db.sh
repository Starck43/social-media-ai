#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/../.." && pwd -P)"
BACKUP_DIR="${STAGING_BACKUP_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/social-media-ai/staging/backups}"

if [[ "$BACKUP_DIR" != /* ]]; then
  echo 'STAGING_BACKUP_DIR must be an absolute path.' >&2
  exit 2
fi
if [[ ! -e "$BACKUP_DIR" ]]; then
  mkdir -m 700 -p -- "$BACKUP_DIR"
fi
if [[ ! -d "$BACKUP_DIR" ]]; then
  echo 'STAGING_BACKUP_DIR must be a directory.' >&2
  exit 2
fi
BACKUP_DIR="$(cd -- "$BACKUP_DIR" && pwd -P)"
case "$BACKUP_DIR/" in
  "$ROOT/"*) echo 'Backups must be outside the repository.' >&2; exit 2 ;;
esac
mode="$(stat -c '%a' "$BACKUP_DIR")"
if (( (8#$mode & 077) != 0 )); then
  echo 'Backup directory must be private (chmod 700); refusing to write.' >&2
  exit 2
fi

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
base="social-ai-staging-$stamp-$$.dump"
final="$BACKUP_DIR/$base"
checksum="$final.sha256"
if [[ -e "$final" || -e "$checksum" ]]; then
  echo 'Refusing to overwrite an existing backup.' >&2
  exit 2
fi
tmp="$(mktemp "$BACKUP_DIR/.staging-backup.XXXXXX")"
tmp_checksum="$tmp.sha256"
cleanup() { rm -f -- "$tmp" "$tmp_checksum"; }
trap cleanup EXIT
chmod 600 "$tmp"

bash "$ROOT/scripts/ops/staging_compose.sh" exec -T db sh -ec \
  'exec pg_dump --format=custom --no-owner --no-acl --username="$POSTGRES_USER" "$POSTGRES_DB"' \
  > "$tmp"
bash "$ROOT/scripts/ops/staging_compose.sh" exec -T db pg_restore --list < "$tmp" >/dev/null

digest="$(sha256sum -- "$tmp" | cut -d ' ' -f 1)"
printf '%s  %s\n' "$digest" "$base" > "$tmp_checksum"
chmod 600 "$tmp_checksum"
mv -- "$tmp" "$final"
mv -- "$tmp_checksum" "$checksum"
printf 'Staging backup created: %s\n' "$final"
printf 'SHA-256 manifest: %s\n' "$checksum"
