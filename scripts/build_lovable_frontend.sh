#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_DIR="${1:-$ROOT_DIR/frontend/lovable-src}"
OUTPUT_DIR="${2:-$ROOT_DIR/frontend/static}"
API_BASE_URL="${VITE_API_BASE_URL:-}"

if [[ ! -d "$SOURCE_DIR" ]]; then
	echo "Source directory not found: $SOURCE_DIR"
	echo "Place exported Lovable frontend source there, then rerun."
	exit 1
fi

if [[ ! -f "$SOURCE_DIR/package.json" ]]; then
	echo "Missing package.json in $SOURCE_DIR"
	echo "Expected a Node frontend project exported from Lovable."
	exit 1
fi

mkdir -p "$OUTPUT_DIR"

echo "Building frontend from $SOURCE_DIR ..."
docker run --rm \
	-v "$SOURCE_DIR:/src" \
	-v "$OUTPUT_DIR:/out" \
	-w /src \
	-e VITE_API_BASE_URL="$API_BASE_URL" \
	node:20-alpine \
	sh -lc '
		set -e
		if [ -f package-lock.json ]; then
			if ! npm ci; then
				echo "npm ci failed (lock mismatch). Falling back to npm install ..."
				npm install
			fi
		else
			npm install
		fi
		npm run build
		if [ -d dist ]; then
			BUILD_DIR="dist"
		elif [ -d build ]; then
			BUILD_DIR="build"
		else
			echo "Build output not found (expected dist/ or build/)." >&2
			exit 1
		fi
		rm -rf /out/*
		cp -R "${BUILD_DIR}/." /out/
	'

echo "Frontend build complete. Output synced to $OUTPUT_DIR"
echo "Caddy serves this at /app"
