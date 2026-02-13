#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
COMPOSE_FILE="${COMPOSE_FILE:-docker-compose.prod.yml}"
ENV_FILE="${ENV_FILE:-.env.prod}"
BACKUP_DIR="${1:-}"

usage() {
	echo "Usage: $0 <backup_dir>"
	echo "Example: $0 backups/20260212T230000Z"
}

if [[ -z "$BACKUP_DIR" ]]; then
	usage
	exit 1
fi

cd "$ROOT_DIR"

if [[ ! -f "$ENV_FILE" ]]; then
	echo "Missing $ENV_FILE. Create it from .env.prod.example first."
	exit 1
fi

if [[ ! -f "$BACKUP_DIR/postgres.sql.gz" ]]; then
	echo "Missing Postgres backup: $BACKUP_DIR/postgres.sql.gz"
	exit 1
fi

if [[ ! -f "$BACKUP_DIR/neo4j_data.tar.gz" ]]; then
	echo "Missing Neo4j backup: $BACKUP_DIR/neo4j_data.tar.gz"
	exit 1
fi

compose() {
	docker compose -f "$COMPOSE_FILE" --env-file "$ENV_FILE" "$@"
}

wait_for_postgres() {
	for _ in $(seq 1 60); do
		if compose exec -T postgres sh -c 'pg_isready -U "$POSTGRES_USER" -d "$POSTGRES_DB"' >/dev/null 2>&1; then
			return 0
		fi
		sleep 2
	done
	echo "Postgres did not become ready in time."
	return 1
}

wait_for_neo4j() {
	for _ in $(seq 1 60); do
		if compose exec -T neo4j sh -c 'cypher-shell -u "$NEO4J_USER" -p "$NEO4J_PASSWORD" "RETURN 1" >/dev/null' >/dev/null 2>&1; then
			return 0
		fi
		sleep 2
	done
	echo "Neo4j did not become ready in time."
	return 1
}

echo "Stopping app services before restore..."
compose stop caddy web worker || true

echo "Starting data services..."
compose up -d postgres redis neo4j
wait_for_postgres
wait_for_neo4j

echo "Restoring Postgres database..."
compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB" -c "DROP SCHEMA public CASCADE; CREATE SCHEMA public;"'
gunzip -c "$BACKUP_DIR/postgres.sql.gz" \
	| compose exec -T postgres sh -c 'psql -v ON_ERROR_STOP=1 -U "$POSTGRES_USER" -d "$POSTGRES_DB"'

echo "Restoring Neo4j data volume..."
compose stop neo4j
compose run --rm --no-deps -T neo4j sh -c 'find /data -mindepth 1 -maxdepth 1 -exec rm -rf {} +'
cat "$BACKUP_DIR/neo4j_data.tar.gz" \
	| compose run --rm --no-deps -T neo4j sh -c 'tar -xzf - -C /data'

compose up -d neo4j
wait_for_neo4j

echo "Running migrations + collectstatic after restore..."
compose run --rm migrate

echo "Starting app services..."
compose up -d web worker caddy

echo "Restore complete."
compose ps
