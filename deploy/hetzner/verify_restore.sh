#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

POSTGRES_CONTAINER="${POSTGRES_CONTAINER:-school-reports-postgres-1}"
RESTORE_WORKDIR="${RESTORE_WORKDIR:-/var/backups/school-reports/restore-drills}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
test_database="school_reports_restore_test_$(date -u +%Y%m%d%H%M%S)"

for command_name in docker restic mktemp python3; do
  command -v "$command_name" >/dev/null || {
    echo "Required command is unavailable: ${command_name}" >&2
    exit 1
  }
done

case "$test_database" in
  school_reports_restore_test_[0-9]*) ;;
  *) echo "Unsafe restore database name." >&2; exit 1 ;;
esac

mkdir -p "$RESTORE_WORKDIR"
chmod 700 "$RESTORE_WORKDIR"
restore_dir="$(mktemp -d "${RESTORE_WORKDIR}/run.XXXXXX")"
database_created=false

cleanup() {
  if [ "$database_created" = true ]; then
    docker exec "$POSTGRES_CONTAINER" sh -ec \
      'dropdb --if-exists --force --username="$POSTGRES_USER" "$1"' \
      sh "$test_database" >/dev/null
  fi
  case "$restore_dir" in
    "${RESTORE_WORKDIR}"/run.*) rm -rf -- "$restore_dir" ;;
    *) echo "Refusing to remove unexpected restore path: ${restore_dir}" >&2 ;;
  esac
}
trap cleanup EXIT

postgres_target="${restore_dir}/postgres"
mkdir -p "$postgres_target"
restic restore latest --tag postgres --target "$postgres_target"
dump_path="$(find "$postgres_target" -type f -name 'postgres-*.dump' -print -quit)"
test -n "$dump_path" && test -s "$dump_path"
echo "PostgreSQL snapshot materialized."

docker exec "$POSTGRES_CONTAINER" sh -ec \
  'createdb --username="$POSTGRES_USER" "$1"' \
  sh "$test_database"
database_created=true
echo "Temporary PostgreSQL database created."

docker exec -i "$POSTGRES_CONTAINER" sh -ec \
  'pg_restore --username="$POSTGRES_USER" --dbname="$1" --no-owner --no-privileges' \
  sh "$test_database" <"$dump_path"
echo "PostgreSQL dump restored into the temporary database."

migration_count="$(docker exec "$POSTGRES_CONTAINER" sh -ec \
  'psql --username="$POSTGRES_USER" --dbname="$1" --tuples-only --no-align --command="SELECT COUNT(*) FROM django_migrations"' \
  sh "$test_database")"
table_count="$(docker exec "$POSTGRES_CONTAINER" sh -ec \
  'psql --username="$POSTGRES_USER" --dbname="$1" --tuples-only --no-align --command="SELECT COUNT(*) FROM information_schema.tables WHERE table_schema = '\''public'\''"' \
  sh "$test_database")"

test "${migration_count:-0}" -gt 0
test "${table_count:-0}" -gt 0
echo "Temporary PostgreSQL database verified."

media_target="${restore_dir}/media"
mkdir -p "$media_target"
sample_path="$(restic ls latest --tag media --json | python3 -c '
import json, sys
selected = ""
for raw in sys.stdin:
    try:
        item = json.loads(raw)
    except json.JSONDecodeError:
        continue
    node = item.get("node") or item
    if not selected and node.get("type") == "file" and int(node.get("size") or 0) > 0:
        selected = node.get("path") or item.get("path") or ""
print(selected)
')"
test -n "$sample_path"
echo "Media sample selected from the encrypted snapshot."
restic restore latest --tag media --target "$media_target" --include "$sample_path"
sample_file="${media_target}/${sample_path#/}"
test -s "$sample_file"
sample_size="$(wc -c <"$sample_file")"
echo "Media sample restored and verified."

echo "Restore drill completed at ${timestamp}: migrations=${migration_count}, tables=${table_count}, media_sample_bytes=${sample_size}."
