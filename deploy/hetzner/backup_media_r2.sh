#!/usr/bin/env bash
set -Eeuo pipefail

umask 077

APP_ENV_FILE="${APP_ENV_FILE:-/opt/school-reports/deploy/hetzner/env.production}"
BACKUP_WORKDIR="${MEDIA_BACKUP_WORKDIR:-/var/backups/school-reports/media}"
RESTIC_HOST="${RESTIC_HOST:-school-reports-prod}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"

for command_name in python3 rclone restic flock mktemp; do
  command -v "$command_name" >/dev/null || {
    echo "Required command is unavailable: ${command_name}" >&2
    exit 1
  }
done

test -r "$APP_ENV_FILE" || {
  echo "Production environment file is unavailable: ${APP_ENV_FILE}" >&2
  exit 1
}

read_env_value() {
  local key="$1"
  sed -n "s/^${key}=//p" "$APP_ENV_FILE" | tail -n 1
}

R2_ACCESS_KEY_ID="$(read_env_value R2_ACCESS_KEY_ID)"
R2_SECRET_ACCESS_KEY="$(read_env_value R2_SECRET_ACCESS_KEY)"
R2_BUCKET_NAME="$(read_env_value R2_BUCKET_NAME)"
R2_ENDPOINT_URL="$(read_env_value R2_ENDPOINT_URL)"
R2_REGION="$(read_env_value AWS_S3_REGION_NAME)"
R2_REGION="${R2_REGION:-auto}"

for required_value in R2_ACCESS_KEY_ID R2_SECRET_ACCESS_KEY R2_BUCKET_NAME R2_ENDPOINT_URL; do
  test -n "${!required_value}" || {
    echo "${required_value} is required for media backup." >&2
    exit 1
  }
done

# Some legacy environment files include the bucket as the first endpoint path.
# rclone expects the endpoint origin only.
R2_ENDPOINT_URL="$(python3 - "$R2_ENDPOINT_URL" <<'PY'
import sys
from urllib.parse import urlsplit, urlunsplit

parts = urlsplit(sys.argv[1])
if parts.scheme not in {"http", "https"} or not parts.netloc:
    raise SystemExit("Invalid R2_ENDPOINT_URL")
print(urlunsplit((parts.scheme, parts.netloc, "", "", "")))
PY
)"

export RCLONE_CONFIG_R2PRIMARY_TYPE=s3
export RCLONE_CONFIG_R2PRIMARY_PROVIDER=Other
export RCLONE_CONFIG_R2PRIMARY_ACCESS_KEY_ID="$R2_ACCESS_KEY_ID"
export RCLONE_CONFIG_R2PRIMARY_SECRET_ACCESS_KEY="$R2_SECRET_ACCESS_KEY"
export RCLONE_CONFIG_R2PRIMARY_ENDPOINT="$R2_ENDPOINT_URL"
export RCLONE_CONFIG_R2PRIMARY_REGION="$R2_REGION"
export RCLONE_CONFIG_R2PRIMARY_ACL=private

mkdir -p "$BACKUP_WORKDIR"
chmod 700 "$BACKUP_WORKDIR"

exec 9>"${BACKUP_WORKDIR}/backup.lock"
flock -n 9 || {
  echo "A media backup is already running." >&2
  exit 1
}

staging_dir="$(mktemp -d "${BACKUP_WORKDIR}/run.XXXXXX")"
cleanup() {
  case "$staging_dir" in
    "${BACKUP_WORKDIR}"/run.*) rm -rf -- "$staging_dir" ;;
    *) echo "Refusing to remove unexpected staging path: ${staging_dir}" >&2 ;;
  esac
}
trap cleanup EXIT

rclone copy "r2primary:${R2_BUCKET_NAME}" "$staging_dir" \
  --checksum \
  --fast-list \
  --checkers "${RCLONE_CHECKERS:-8}" \
  --transfers "${RCLONE_TRANSFERS:-4}"

rclone check "r2primary:${R2_BUCKET_NAME}" "$staging_dir" \
  --one-way \
  --checksum

restic backup "$staging_dir" \
  --host "$RESTIC_HOST" \
  --tag media \
  --tag daily

restic forget \
  --host "$RESTIC_HOST" \
  --tag media \
  --keep-daily 14 \
  --keep-weekly 8 \
  --keep-monthly 12 \
  --prune

restic check --read-data-subset=5%

echo "Encrypted R2 media backup completed at ${timestamp}."
