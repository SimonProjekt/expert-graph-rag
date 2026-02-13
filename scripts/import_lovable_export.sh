#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
	echo "Usage: $0 /path/to/lovable-export.zip|/path/to/lovable-export-folder"
	exit 1
fi

INPUT_PATH="$1"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$ROOT_DIR/frontend/lovable-src"
TMP_DIR="$(mktemp -d)"
trap 'rm -rf "$TMP_DIR"' EXIT

normalize_windows_path() {
	local raw_path="$1"
	if [[ "$raw_path" =~ ^([A-Za-z]):\\.* ]]; then
		local drive_letter
		drive_letter="$(echo "${BASH_REMATCH[1]}" | tr '[:upper:]' '[:lower:]')"
		local tail_path="${raw_path:3}"
		tail_path="${tail_path//\\//}"
		echo "/mnt/${drive_letter}/${tail_path}"
		return
	fi
	echo "$raw_path"
}

SOURCE_PATH="$(normalize_windows_path "$INPUT_PATH")"

rm -rf "$TARGET_DIR"
mkdir -p "$TARGET_DIR"

if [[ -d "$SOURCE_PATH" ]]; then
	cp -R "$SOURCE_PATH/." "$TARGET_DIR/"
elif [[ -f "$SOURCE_PATH" ]]; then
	unzip -q "$SOURCE_PATH" -d "$TMP_DIR/raw"
	TOP_ITEMS=("$TMP_DIR/raw"/*)
	if [[ ${#TOP_ITEMS[@]} -eq 1 && -d "${TOP_ITEMS[0]}" ]]; then
		cp -R "${TOP_ITEMS[0]}/." "$TARGET_DIR/"
	else
		cp -R "$TMP_DIR/raw/." "$TARGET_DIR/"
	fi
else
	echo "Path not found: $INPUT_PATH"
	echo "Resolved path: $SOURCE_PATH"
	exit 1
fi

if [[ ! -f "$TARGET_DIR/package.json" ]]; then
	# Flatten one nested directory if the export has wrapper folder(s)
	mapfile -t top_level_dirs < <(find "$TARGET_DIR" -mindepth 1 -maxdepth 1 -type d)
	if [[ ${#top_level_dirs[@]} -eq 1 && -f "${top_level_dirs[0]}/package.json" ]]; then
		cp -R "${top_level_dirs[0]}/." "$TMP_DIR/flattened"
		rm -rf "$TARGET_DIR"
		mkdir -p "$TARGET_DIR"
		cp -R "$TMP_DIR/flattened/." "$TARGET_DIR/"
	fi
fi

if [[ ! -f "$TARGET_DIR/package.json" ]]; then
	echo "Imported export does not contain package.json in frontend/lovable-src."
	echo "Check the export contents and try again."
	exit 1
fi

echo "Lovable export imported into: $TARGET_DIR"
echo "Next: ./scripts/build_lovable_frontend.sh"
