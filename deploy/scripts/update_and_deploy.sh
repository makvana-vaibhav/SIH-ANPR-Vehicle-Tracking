#!/usr/bin/env bash
set -euo pipefail

if [[ $# -lt 1 || $# -gt 2 ]]; then
  echo "Usage: $0 <staging|main> [branch]"
  exit 1
fi

ENV_NAME="$1"
case "$ENV_NAME" in
  staging) DEFAULT_BRANCH="build/sentinel-gj" ;;
  main) DEFAULT_BRANCH="main" ;;
  *)
    echo "Invalid environment: $ENV_NAME (allowed: staging, main)"
    exit 1
    ;;
esac

BRANCH="${2:-$DEFAULT_BRANCH}"
APP_ROOT="/opt/sentinel-gj"
TARGET_DIR="$APP_ROOT/$ENV_NAME"
ENV_FILE="/etc/sentinel-gj/${ENV_NAME}.env"
REPO_URL="${REPO_URL:-https://github.com/${GITHUB_REPOSITORY:-makvana-vaibhav/SENTINEL-GJ}.git}"

if ! command -v docker >/dev/null 2>&1; then
  echo "docker is not installed"
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "docker compose plugin is not installed"
  exit 1
fi
if ! command -v git >/dev/null 2>&1; then
  echo "git is not installed"
  exit 1
fi

sudo mkdir -p "$APP_ROOT"

if [[ ! -d "$TARGET_DIR/.git" ]]; then
  echo "Cloning $REPO_URL into $TARGET_DIR"
  git clone --branch "$BRANCH" "$REPO_URL" "$TARGET_DIR"
fi

cd "$TARGET_DIR"
git fetch --prune origin
git checkout "$BRANCH"
git reset --hard "origin/$BRANCH"

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing environment file: $ENV_FILE"
  echo "Create it from deploy/env/${ENV_NAME}.env.example"
  exit 1
fi

set -a
# shellcheck source=/dev/null
source "$ENV_FILE"
set +a

COMPOSE_ARGS=(
  --env-file "$ENV_FILE"
  -f docker-compose.yml
  -f deploy/docker-compose.ec2.yml
)

echo "Running compose pull for $ENV_NAME"
docker compose "${COMPOSE_ARGS[@]}" pull

echo "Running compose up for $ENV_NAME"
docker compose "${COMPOSE_ARGS[@]}" up -d --build --remove-orphans

echo "Waiting for web health endpoint"
WEB_PORT="${WEB_HOST_PORT:-8080}"
for _ in {1..60}; do
  if curl -fsS "http://127.0.0.1:${WEB_PORT}/healthz" >/dev/null 2>&1; then
    echo "Deployment successful: $ENV_NAME on port $WEB_PORT"
    docker compose "${COMPOSE_ARGS[@]}" ps
    exit 0
  fi
  sleep 2
done

echo "Health check failed for $ENV_NAME"
docker compose "${COMPOSE_ARGS[@]}" ps
docker compose "${COMPOSE_ARGS[@]}" logs --tail 200 web api
exit 1
