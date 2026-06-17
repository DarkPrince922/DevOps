#!/usr/bin/env bash
set -euo pipefail

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Install Docker first: https://docs.docker.com/engine/install/"
  exit 1
fi

if ! docker compose version >/dev/null 2>&1; then
  echo "Docker Compose plugin is not installed. Install it first."
  exit 1
fi

[ -f .env ] || cp .env.example .env
mkdir -p data data/.ssh
[ -f data/servers.json ] || echo '{}' > data/servers.json
[ -f data/proxies.json ] || cp data/proxies.example.json data/proxies.json
[ -f data/settings.json ] || cp data/settings.example.json data/settings.json
[ -f data/sessions.json ] || echo '{"sessions": {}, "updated": {}}' > data/sessions.json
chmod 600 .env data/*.json 2>/dev/null || true

echo "Готово. Заполните .env и при необходимости data/servers.json, затем запустите: docker compose up -d --build"
