#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.prod}"
SEED_DATA=0

usage() {
	echo "Usage: $0 [--seed]"
	echo "  --seed   run seed_demo_data after deployment"
}

if [[ "${1:-}" == "--seed" ]]; then
	SEED_DATA=1
elif [[ "$#" -gt 0 ]]; then
	usage
	exit 1
fi

cd "$ROOT_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
	echo "Missing $ENV_FILE. Create it from .env.prod.example first."
	exit 1
fi

detect_compose_cmd() {
	if docker compose version >/dev/null 2>&1; then
		echo "docker compose"
		return
	fi

	if command -v docker-compose >/dev/null 2>&1; then
		echo "docker-compose"
		return
	fi

	echo ""
}

COMPOSE_CMD="$(detect_compose_cmd)"
if [[ -z "$COMPOSE_CMD" ]]; then
	echo "No Docker Compose command found."
	echo "Install Docker Compose v2 plugin or docker-compose v1."
	exit 1
fi

compose() {
	if [[ "$COMPOSE_CMD" == "docker compose" ]]; then
		docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
	else
		docker-compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
	fi
}

if git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
	echo "Pulling latest code from git..."
	git fetch --all --prune
	git pull --ff-only
else
	echo "Not a git checkout; skipping git pull."
fi

echo "Building production images..."
compose build

echo "Starting data services..."
compose up -d postgres redis neo4j

echo "Running migrations + collectstatic..."
compose run --rm migrate

echo "Starting application services..."
compose up -d web worker caddy

if [[ "$SEED_DATA" -eq 1 ]]; then
	echo "Seeding demo data..."
	compose exec web sh -c 'if [ -n "${OPENALEX_API_KEY:-}" ]; then \
		python manage.py seed_openalex --works 50 --authors 30 --query "machine learning" --years 2022-2026; \
	else \
		python manage.py seed_demo_data; \
	fi'
fi

echo "Deployment complete."
compose ps
