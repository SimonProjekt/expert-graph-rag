# Deployment (Single Ubuntu VPS)

This guide deploys the demo on one Ubuntu server with Docker Compose and Caddy-managed HTTPS.

## 1) Provision The VPS

Use Ubuntu 22.04 or 24.04 with:

- Public static IP
- DNS name (example: `demo.example.com`) pointing to that IP
- Ports `80` and `443` open to the internet

Recommended minimum for a smoother first build:

- 2 vCPU
- 4 GB RAM
- 30+ GB disk

## 2) Install Docker + Compose Plugin

```bash
sudo apt-get update
sudo apt-get install -y ca-certificates curl gnupg
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
echo \
  "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu \
  $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | \
  sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt-get update
sudo apt-get install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo usermod -aG docker "$USER"
```

Log out and back in so group changes apply.

## 3) Clone Repo + Configure Env

```bash
git clone <YOUR_REPO_URL>
cd expert-graph-rag
./scripts/init_prod_env.sh --domain demo.example.com --email ops@example.com
```

`init_prod_env.sh` generates `.env.prod` and auto-fills secure random secrets.
Use `--force` if you intentionally want to regenerate secrets.

Then edit `.env.prod` and set/verify:

- `DEMO_DOMAIN` to your real domain
- `CADDY_EMAIL` for certificate notifications
- `DJANGO_SECRET_KEY` to a long random value
- `DJANGO_ALLOWED_HOSTS` to your exact host(s)
- `DATABASE_URL`, `POSTGRES_PASSWORD`, `NEO4J_PASSWORD`
- `DEBUG=false`
- `OPENALEX_API_KEY` and `OPENALEX_MAILTO` (recommended for live data)

`OPENAI_API_KEY` is optional. Leave it blank to keep Demo Mode (LLM Disabled).

## 4) Bring Up The Stack

Use the deployment script:

```bash
./scripts/prod_deploy.sh
```

The script:

1. Pulls latest git changes
2. Builds images
3. Starts data services
4. Runs migrations + collectstatic
5. Starts `web`, `worker`, and `caddy`

After DNS propagation, open:

- `https://<your-domain>/` (landing page)
- `https://<your-domain>/demo/` (interactive demo)
- `https://<your-domain>/app` (redirects to stable UI at `/` in interview-safe mode)

## 4.1) Lovable Frontend (Optional, Same Domain)

This deployment is pre-wired for a Lovable-generated frontend:

- API: `https://<your-domain>/api/*`
- Frontend app assets: `frontend/static` (served by Caddy)
- Existing Django UI: `https://<your-domain>/`

Default production config uses interview-safe mode and redirects `/app` to `/`.
To re-enable direct SPA serving at `/app`, replace the `/app` redirect in `Caddyfile`
with the previous `handle_path /app* { ... }` block and redeploy.

When you have exported frontend zip from Lovable, import it with:

```bash
./scripts/import_lovable_export.sh /path/to/lovable-export.zip
```

Then build and deploy:

```bash
./scripts/build_lovable_frontend.sh
./scripts/prod_deploy.sh
```

The deploy script auto-builds frontend assets when `frontend/lovable-src/package.json` exists.

## 5) Load Demo Data

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod exec web python manage.py seed_demo_data
```

OpenAlex-backed seed (recommended if `OPENALEX_API_KEY` is set):

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod exec web \
  python manage.py seed_openalex --works 50 --authors 30 --query "machine learning" --years 2022-2026
```

Or deploy and seed in one command:

```bash
./scripts/prod_deploy.sh --seed
```

## 6) Update Procedure

```bash
./scripts/prod_deploy.sh
```

This is your standard rolling update path for this single-node demo.

## 7) Logs And Health

Tail all logs:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f
```

Service-specific logs:

```bash
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f web
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f worker
docker compose -f docker-compose.prod.yml --env-file .env.prod logs -f caddy
```

Health endpoint:

```bash
curl -i https://<your-domain>/healthz
```

## 8) Backups / Restore

Create backup:

```bash
./scripts/prod_backup.sh
```

Restore:

```bash
./scripts/prod_restore.sh backups/<TIMESTAMP_DIR>
```

## 9) Keep Neo4j Private

The production compose file does **not** publish Neo4j/Postgres/Redis ports.

Recommended UFW policy:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw enable
sudo ufw status
```

This ensures only Caddy-facing ports are public.

## 10) Optional Basic Auth For Admin/Debug Through Caddy

Generate hash:

```bash
docker run --rm caddy:2.9-alpine caddy hash-password --plaintext "CHANGE_ME"
```

Then set `BASIC_AUTH_USER` and `BASIC_AUTH_PASSWORD_HASH` in `.env.prod`, and uncomment the `basicauth` block in `Caddyfile`.
