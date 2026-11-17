#!/bin/bash
# Runs on the server, as the only thing the GitHub deploy key is allowed to do.
set -euo pipefail

sha="${SSH_ORIGINAL_COMMAND:-${1:-}}"
if ! [[ "$sha" =~ ^[0-9a-f]{40}$ ]]; then
  echo "usage: deploy.sh <full commit sha>" >&2
  exit 64
fi

cd /opt/dakarbot
curl -fsSL "https://raw.githubusercontent.com/DakarRS/DakarBot/$sha/deploy/docker-compose.prod.yml" \
  -o docker-compose.yml.new

previous="$(cat .deployed 2>/dev/null || true)"
read -r registry_user || true
read -r registry_token || true
if [ -n "${registry_token:-}" ]; then
  printf '%s' "$registry_token" | docker login ghcr.io -u "$registry_user" --password-stdin >/dev/null
fi
trap 'docker logout ghcr.io >/dev/null 2>&1 || true' EXIT

mv docker-compose.yml.new docker-compose.yml
export IMAGE_TAG="$sha"

docker compose pull --quiet
docker compose up -d --wait --wait-timeout 120 --remove-orphans
echo "$sha" > .deployed
docker image prune -af --filter "until=168h" >/dev/null
echo "deployed $sha (previous: ${previous:-none})"
