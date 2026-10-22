#!/usr/bin/env bash
# Pull latest code and (re)deploy the userbot via docker compose.
# Usage: ./deploy.sh   (run on the server, inside the repo)
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

echo "==> Pulling latest code"
git pull --ff-only

if [ ! -f .env ]; then
    echo "==> ERROR: .env not found in $(pwd) — create it before deploying." >&2
    exit 1
fi

if [ ! -f data/assistant.session ]; then
    echo "==> ERROR: not logged in yet. Run once:" >&2
    echo "    docker compose run --rm bot python -m bot.login" >&2
    exit 1
fi

echo "==> Building new image"
docker compose build

echo "==> Restarting"
docker compose up -d --remove-orphans

echo "==> Cleaning up dangling images"
docker image prune -f

echo "==> Done. Current status:"
docker compose ps
