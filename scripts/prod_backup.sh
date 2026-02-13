#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.prod}"
BACKUP_ROOT="${BACKUP_ROOT:-$ROOT_DIR/backups}"
TIMESTAMP="$(date -u +%Y%m%dT%H%M%SZ)"
BACKUP_DIR="$BACKUP_ROOT/$TIMESTAMP"

cd "$ROOT_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
	echo "Missing $ENV_FILE. Create it from .env.prod.example first."
	exit 1
fi

mkdir -p "$BACKUP_DIR"

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

echo "Ensuring postgres and neo4j are running..."
compose up -d postgres neo4j

echo "Creating Postgres backup..."
compose exec -T postgres sh -c 'pg_dump -U "$POSTGRES_USER" -d "$POSTGRES_DB" --no-owner --no-privileges' \
	| gzip >"$BACKUP_DIR/postgres.sql.gz"

echo "Creating Neo4j data backup..."
compose exec -T neo4j sh -c 'tar -czf - -C /data .' >"$BACKUP_DIR/neo4j_data.tar.gz"

sha256sum "$BACKUP_DIR/postgres.sql.gz" "$BACKUP_DIR/neo4j_data.tar.gz" >"$BACKUP_DIR/SHA256SUMS"

echo "Backup complete: $BACKUP_DIR"
