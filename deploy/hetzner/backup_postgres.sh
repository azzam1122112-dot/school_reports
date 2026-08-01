#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-school-reports-postgres-1}"
BACKUP_WORKDIR="${BACKUP_WORKDIR:-/var/backups/school-reports/postgres}"
RESTIC_HOST="${RESTIC_HOST:-school-reports-prod}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
dump_path="${BACKUP_WORKDIR}/postgres-${timestamp}.dump"

cleanup() {
  rm -f "$dump_path"
}
trap cleanup EXIT

for command_name in docker restic flock; do
  command -v "$command_name" >/dev/null || {
    echo "Required command is unavailable: ${command_name}" >&2
    exit 1
  }
done

mkdir -p "$BACKUP_WORKDIR"

exec 9>"${BACKUP_WORKDIR}/backup.lock"
flock -n 9 || {
  echo "A PostgreSQL backup is already running." >&2
  exit 1
}

docker inspect "$POSTGRES_CONTAINER" >/dev/null
docker exec "$POSTGRES_CONTAINER" sh -ec \
  'pg_dump --username="$POSTGRES_USER" --dbname="$POSTGRES_DB" --format=custom --compress=9' \
  >"$dump_path"

test -s "$dump_path"
docker exec -i "$POSTGRES_CONTAINER" pg_restore --list <"$dump_path" >/dev/null

restic backup "$dump_path" \
  --host "$RESTIC_HOST" \
  --tag postgres \
  --tag daily

restic forget \
  --host "$RESTIC_HOST" \
  --tag postgres \
  --keep-daily 14 \
  --keep-weekly 8 \
  --keep-monthly 12 \
  --prune

restic check --read-data-subset=5%

echo "Encrypted PostgreSQL backup completed at ${timestamp}."