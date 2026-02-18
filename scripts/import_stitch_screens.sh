#!/usr/bin/env bash
set -euo pipefail

if [[ $# -ne 1 ]]; then
	echo "Usage: $0 /path/to/stitch-zip-folder"
	exit 1
fi

INPUT_PATH="$1"
ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TARGET_DIR="$ROOT_DIR/frontend/lovable-src/public/stitch-screens"
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

SOURCE_DIR="$(normalize_windows_path "$INPUT_PATH")"

if [[ ! -d "$SOURCE_DIR" ]]; then
	echo "Folder not found: $INPUT_PATH"
	echo "Resolved path: $SOURCE_DIR"
	exit 1
fi

if command -v unzip >/dev/null 2>&1; then
	EXTRACT_TOOL="unzip"
elif command -v jar >/dev/null 2>&1; then
	EXTRACT_TOOL="jar"
else
	echo "No zip extractor found. Install unzip or ensure jar is available."
	exit 1
fi

mapfile -t ZIP_FILES < <(find "$SOURCE_DIR" -maxdepth 1 -type f -name "*.zip" | sort -V)
if [[ ${#ZIP_FILES[@]} -eq 0 ]]; then
	echo "No .zip files found in: $SOURCE_DIR"
	exit 1
fi

mkdir -p "$TARGET_DIR"
find "$TARGET_DIR" -mindepth 1 -maxdepth 1 -exec rm -rf {} +

MANIFEST_FILE="$TARGET_DIR/manifest.json"
printf "[\n" > "$MANIFEST_FILE"

screen_index=0
for zip_file in "${ZIP_FILES[@]}"; do
	screen_index=$((screen_index + 1))
	screen_id="$(printf "screen-%02d" "$screen_index")"
	screen_dir="$TARGET_DIR/$screen_id"
	extract_dir="$TMP_DIR/$screen_id"
	zip_name="$(basename "$zip_file")"

	mkdir -p "$screen_dir" "$extract_dir"

	if [[ "$EXTRACT_TOOL" == "unzip" ]]; then
		unzip -q "$zip_file" -d "$extract_dir"
	else
		(
			cd "$extract_dir"
			jar xf "$zip_file"
		)
	fi

	code_file="$(find "$extract_dir" -maxdepth 4 -type f -name "code.html" | head -n 1 || true)"
	screen_png="$(find "$extract_dir" -maxdepth 4 -type f -name "screen.png" | head -n 1 || true)"

	title="Stitch Screen $screen_index"
	has_code=false

	if [[ -n "$code_file" && -f "$code_file" ]]; then
		cp "$code_file" "$screen_dir/code.html"
		has_code=true
		page_title="$(sed -n 's:.*<title>\(.*\)</title>.*:\1:p' "$code_file" | head -n 1 || true)"
		if [[ -n "$page_title" ]]; then
			title="$page_title"
		fi
	fi

	if [[ -n "$screen_png" && -f "$screen_png" ]]; then
		cp "$screen_png" "$screen_dir/screen.png"
	fi

	if [[ "$has_code" == "false" ]]; then
		cat > "$screen_dir/code.html" <<'EOF'
<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>Stitch Screen</title>
    <style>
      body { margin: 0; font-family: Inter, Arial, sans-serif; background: #0f172a; color: #e2e8f0; }
      .wrap { padding: 24px; max-width: 1200px; margin: 0 auto; }
      .note { margin-bottom: 16px; color: #94a3b8; }
      img { width: 100%; border-radius: 8px; border: 1px solid #334155; }
    </style>
  </head>
  <body>
    <div class="wrap">
      <p class="note">This ZIP did not contain a code.html file. Showing the exported screenshot only.</p>
      <img src="./screen.png" alt="Stitch screen preview" />
    </div>
  </body>
</html>
EOF
	fi

	escaped_title="$(printf '%s' "$title" | tr '\n' ' ' | sed 's/"/\\"/g')"
	escaped_zip="$(printf '%s' "$zip_name" | sed 's/"/\\"/g')"

	if [[ "$screen_index" -gt 1 ]]; then
		printf ",\n" >> "$MANIFEST_FILE"
	fi

	cat >> "$MANIFEST_FILE" <<EOF
  {
    "id": "$screen_id",
    "zip_name": "$escaped_zip",
    "title": "$escaped_title",
    "has_code": $has_code,
    "code_path": "stitch-screens/$screen_id/code.html",
    "image_path": "stitch-screens/$screen_id/screen.png"
  }
EOF
done

printf "\n]\n" >> "$MANIFEST_FILE"

echo "Imported ${#ZIP_FILES[@]} stitch ZIP file(s) into: $TARGET_DIR"
echo "Manifest: $MANIFEST_FILE"
echo "Next: ./scripts/build_lovable_frontend.sh"
