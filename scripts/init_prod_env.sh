#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
EXAMPLE_FILE="$ROOT_DIR/.env.prod.example"
ENV_FILE="$ROOT_DIR/.env.prod"
FORCE=0

DEMO_DOMAIN_VALUE="${DEMO_DOMAIN:-demo.example.com}"
CADDY_EMAIL_VALUE="${CADDY_EMAIL:-ops@example.com}"

usage() {
	echo "Usage: $0 [--force] [--domain your-domain] [--email your-email]"
	echo ""
	echo "Examples:"
	echo "  $0"
	echo "  $0 --domain demo.yourcompany.com --email you@yourcompany.com"
}

while [[ "$#" -gt 0 ]]; do
	case "$1" in
		--force)
			FORCE=1
			shift
			;;
		--domain)
			DEMO_DOMAIN_VALUE="${2:-}"
			shift 2
			;;
		--email)
			CADDY_EMAIL_VALUE="${2:-}"
			shift 2
			;;
		-h|--help)
			usage
			exit 0
			;;
		*)
			echo "Unknown argument: $1"
			usage
			exit 1
			;;
	esac
done

if [[ ! -f "$EXAMPLE_FILE" ]]; then
	echo "Missing template: $EXAMPLE_FILE"
	exit 1
fi

if [[ -f "$ENV_FILE" && "$FORCE" -ne 1 ]]; then
	echo "$ENV_FILE already exists. Use --force to regenerate."
	exit 1
fi

if [[ -z "$DEMO_DOMAIN_VALUE" || -z "$CADDY_EMAIL_VALUE" ]]; then
	echo "Both --domain and --email values must be non-empty."
	exit 1
fi

readarray -t GENERATED < <(python3 - <<'PY'
import secrets
print(secrets.token_urlsafe(64))  # DJANGO_SECRET_KEY
print(secrets.token_urlsafe(24))  # POSTGRES_PASSWORD
print(secrets.token_urlsafe(24))  # NEO4J_PASSWORD
PY
)

DJANGO_SECRET_KEY_VALUE="${GENERATED[0]}"
POSTGRES_PASSWORD_VALUE="${GENERATED[1]}"
NEO4J_PASSWORD_VALUE="${GENERATED[2]}"
POSTGRES_USER_VALUE="expert_user"
POSTGRES_DB_VALUE="expert_graph_rag"
DATABASE_URL_VALUE="postgresql://${POSTGRES_USER_VALUE}:${POSTGRES_PASSWORD_VALUE}@postgres:5432/${POSTGRES_DB_VALUE}"
CSRF_TRUSTED_ORIGINS_VALUE="https://${DEMO_DOMAIN_VALUE}"

DEMO_DOMAIN_VALUE="$DEMO_DOMAIN_VALUE" \
CADDY_EMAIL_VALUE="$CADDY_EMAIL_VALUE" \
DJANGO_SECRET_KEY_VALUE="$DJANGO_SECRET_KEY_VALUE" \
CSRF_TRUSTED_ORIGINS_VALUE="$CSRF_TRUSTED_ORIGINS_VALUE" \
POSTGRES_PASSWORD_VALUE="$POSTGRES_PASSWORD_VALUE" \
DATABASE_URL_VALUE="$DATABASE_URL_VALUE" \
NEO4J_PASSWORD_VALUE="$NEO4J_PASSWORD_VALUE" \
python3 - "$EXAMPLE_FILE" "$ENV_FILE" <<'PY'
from pathlib import Path
import os
import sys

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
lines = src.read_text(encoding="utf-8").splitlines()

replacements = {
    "DEMO_DOMAIN": os.environ["DEMO_DOMAIN_VALUE"],
    "CADDY_EMAIL": os.environ["CADDY_EMAIL_VALUE"],
    "DJANGO_SECRET_KEY": os.environ["DJANGO_SECRET_KEY_VALUE"],
    "DJANGO_ALLOWED_HOSTS": os.environ["DEMO_DOMAIN_VALUE"],
    "DJANGO_CSRF_TRUSTED_ORIGINS": os.environ["CSRF_TRUSTED_ORIGINS_VALUE"],
    "POSTGRES_PASSWORD": os.environ["POSTGRES_PASSWORD_VALUE"],
    "DATABASE_URL": os.environ["DATABASE_URL_VALUE"],
    "NEO4J_PASSWORD": os.environ["NEO4J_PASSWORD_VALUE"],
}

output = []
for line in lines:
    if "=" not in line or line.lstrip().startswith("#"):
        output.append(line)
        continue

    key, _value = line.split("=", 1)
    if key in replacements:
        output.append(f"{key}={replacements[key]}")
    else:
        output.append(line)

dst.write_text("\n".join(output) + "\n", encoding="utf-8")
PY

echo "Generated $ENV_FILE"
echo ""
echo "Updated automatically:"
echo "  - DJANGO_SECRET_KEY"
echo "  - POSTGRES_PASSWORD"
echo "  - NEO4J_PASSWORD"
echo "  - DATABASE_URL"
echo "  - DJANGO_ALLOWED_HOSTS"
echo "  - DJANGO_CSRF_TRUSTED_ORIGINS"
echo "  - DEMO_DOMAIN"
echo "  - CADDY_EMAIL"
echo ""
echo "If needed, edit OPENAI_API_KEY in .env.prod (optional)."

if [[ "$DEMO_DOMAIN_VALUE" == "demo.example.com" || "$CADDY_EMAIL_VALUE" == "ops@example.com" ]]; then
	echo ""
	echo "WARNING: You are using placeholder domain/email."
	echo "Edit DEMO_DOMAIN and CADDY_EMAIL in .env.prod before public deployment."
fi
