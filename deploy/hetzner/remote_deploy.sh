#!/usr/bin/env bash
#
# Runs *on the Hetzner server*, invoked by .github/workflows/ci.yml after the
# test job passes. The workflow ships the compose files into $DEPLOY_PATH over
# ssh, then executes this script there.
#
# Deliberately never touches deploy/hetzner/env.production — production secrets
# live only on the server and are the one thing a deploy must not overwrite.
#
# Required environment (exported by the workflow over ssh):
#   APP_IMAGE              fully qualified image ref, e.g. ghcr.io/owner/repo:<sha>
#   GHCR_USER              GitHub actor, for `docker login ghcr.io`
#   GHCR_TOKEN_FROM_STDIN  when 1, the registry token is read from stdin
#
# Can also be run by hand on the server for a rollback:
#   APP_IMAGE=ghcr.io/owner/repo:<older-sha> bash deploy/hetzner/remote_deploy.sh
set -euo pipefail

DEPLOY_PATH="${DEPLOY_PATH:-/opt/school_reports}"
EDGE_NETWORK="school-platform-edge"
COMPOSE_FILES=(-f compose.hetzner.yaml -f compose.caddy.yaml)

die() { printf '\n[deploy] ERROR: %s\n' "$1" >&2; exit 1; }
log() { printf '[deploy] %s\n' "$1"; }

# --- preflight ---------------------------------------------------------------
: "${APP_IMAGE:?APP_IMAGE is not set}"

command -v docker >/dev/null 2>&1 || die "docker is not installed on this server."
docker compose version >/dev/null 2>&1 || die "the docker compose v2 plugin is missing."

cd "$DEPLOY_PATH" || die "DEPLOY_PATH '$DEPLOY_PATH' does not exist."

[ -f compose.hetzner.yaml ] || die "compose.hetzner.yaml missing in $DEPLOY_PATH — the file sync step did not run."
[ -f deploy/hetzner/env.production ] || die \
  "deploy/hetzner/env.production is missing in $DEPLOY_PATH.
   Production secrets are never shipped from CI. Create it once on the server
   from deploy/hetzner/env.production.example, then re-run this deploy."

# `edge` is declared external in compose.hetzner.yaml, so compose will refuse to
# start rather than create it. Creating it here keeps a fresh server bootable.
docker network inspect "$EDGE_NETWORK" >/dev/null 2>&1 || {
  log "creating external network $EDGE_NETWORK"
  docker network create "$EDGE_NETWORK" >/dev/null
}

# Remember what is running now, so a failed deploy can be reverted by hand.
PREVIOUS_IMAGE="$(docker inspect --format '{{.Config.Image}}' school-reports-web-1 2>/dev/null || true)"

export APP_IMAGE

# --- pull --------------------------------------------------------------------
if [ "${GHCR_TOKEN_FROM_STDIN:-}" = "1" ]; then
  GHCR_TOKEN="$(cat)"
fi

if [ -n "${GHCR_TOKEN:-}" ]; then
  log "logging in to ghcr.io"
  printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "${GHCR_USER:-x-access-token}" --password-stdin
  # The registry token expires with the CI run; do not leave it on disk.
  trap 'docker logout ghcr.io >/dev/null 2>&1 || true' EXIT
fi

log "pulling $APP_IMAGE"
docker pull --quiet "$APP_IMAGE"

# --- release -----------------------------------------------------------------
# `up -d` re-runs the one-shot `migrate` service (migrate --noinput +
# collectstatic); web/worker/beat wait on service_completed_successfully, so a
# failed migration aborts the release instead of serving a half-migrated app.
# No --remove-orphans: compose counts services from inactive profiles (pgbouncer)
# as orphans and would tear them down on every deploy.
log "starting release"
docker compose "${COMPOSE_FILES[@]}" up -d

# --- verify ------------------------------------------------------------------
log "waiting for web to report healthy"
for _ in $(seq 1 60); do
  status="$(docker inspect --format '{{.State.Health.Status}}' school-reports-web-1 2>/dev/null || echo missing)"
  case "$status" in
    healthy|unhealthy) break ;;
  esac
  sleep 5
done

if [ "$status" != "healthy" ]; then
  log "web is '$status' after the deploy — last 60 log lines:"
  docker compose "${COMPOSE_FILES[@]}" logs --tail 60 migrate web || true
  if [ -n "$PREVIOUS_IMAGE" ]; then
    log "roll back with: APP_IMAGE=$PREVIOUS_IMAGE docker compose ${COMPOSE_FILES[*]} up -d"
  fi
  die "deploy did not reach a healthy state."
fi

docker image prune --force --filter "until=168h" >/dev/null 2>&1 || true

log "deployed $APP_IMAGE successfully"
docker compose "${COMPOSE_FILES[@]}" ps
