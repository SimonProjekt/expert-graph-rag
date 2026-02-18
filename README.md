# Expert Finder (`expert-graph-rag`)

Knowledge discovery demo built with Django + DRF + Postgres/pgvector + Neo4j + Redis/Celery.

## Run in 60 Seconds

```bash
git clone <YOUR_REPO_URL> && cd expert-graph-rag
cp .env.example .env
docker compose up --build
```

App URL:

- `http://localhost:8000/` (landing page)
- `http://localhost:8000/demo/` (interactive demo)

## Load Demo Data

Fast local fixture seed (no API keys required):

```bash
docker compose exec web python manage.py seed_demo_data
```

The default fixture is telecom-focused and aligned to the built-in demo queries
(5G RAN optimization, network slicing reliability, O-RAN xApp, core anomaly detection, and energy efficiency).

Interview-safe one-command setup:

```bash
make demo_ready
```

For larger telecom-focused sample data (fixture + OpenAlex across all demo queries):

```bash
make seed_interview_data
```

Optional tuning:

```bash
WORKS_PER_QUERY=120 AUTHORS_PER_QUERY=60 YEARS=2021-2026 BACKEND=local make seed_interview_data
```

OpenAlex seed (recommended when `OPENALEX_API_KEY` is configured):

```bash
docker compose exec web python manage.py seed_openalex --works 50 --authors 30 --query "machine learning" --years 2022-2026
```

Topic-based seed examples:

```bash
docker compose exec web python manage.py seed_openalex --query "5G RAN optimization" --topic telecom
```

## OpenAlex Configuration

Add these to `.env`:

- `OPENALEX_API_KEY` (required for live OpenAlex ingestion/fetch)
- `OPENALEX_EMAIL` (recommended contact email)
- `OPENALEX_MAILTO` (optional alias; if set it overrides `OPENALEX_EMAIL`)
- `OPENALEX_LIVE_FETCH=true|false`
- `OPENALEX_RATE_LIMIT_PER_SEC` (alias for `OPENALEX_RATE_LIMIT_RPS`)
- `OPENALEX_CACHE_ENABLED=true|false`
- `OPENALEX_CACHE_TTL_SECONDS`
- `OPENALEX_MIN_QUERY_COVERAGE` (0-1 query alignment threshold for ingestion)
- `OPENALEX_MAX_TOPICS_PER_WORK` (caps noisy concept/topic expansion)

You can generate an OpenAlex key from your OpenAlex account/dashboard, then place it in `.env`.

All OpenAlex requests include:

- `api_key`
- `mailto`

and use resilient retries/backoff + rate limiting, with cache-backed response reuse.

## Live Read-Through Cache (Search)

`/api/search` behavior:

1. Search local chunks first.
2. If local results are below threshold, trigger live OpenAlex fetch (when enabled).
3. Upsert works/authors/topics.
4. Chunk + embed new papers.
5. Re-run local search and return updated results.

Controls:

- `OPENALEX_LIVE_FETCH=true|false`
- `OPENALEX_LIVE_MIN_RESULTS`
- `OPENALEX_LIVE_FETCH_LIMIT`
- `OPENALEX_LIVE_FETCH_COOLDOWN_SECONDS`
- `OPENALEX_RATE_LIMIT_PER_SEC`
- `OPENALEX_CACHE_ENABLED`
- `OPENALEX_CACHE_TTL_SECONDS`

Search responses also include:

- `hidden_count` (clearance-filtered hits)
- `took_ms` (search latency)
- `live_fetch` metadata (attempt/result/counts/duration)
- `score_breakdown` (`semantic_relevance`, `query_alignment`, `graph_authority`, `graph_centrality`)
- `why_matched` and `graph_path` explainability fields

## Hybrid Graph Retrieval (Step 3)

Search ranking combines:

- semantic chunk similarity
- graph authority from 1-2 hop expansions through author/topic links
- author centrality (when available)

Tuning env vars:

- `SEARCH_GRAPH_SEED_PAPERS`
- `SEARCH_GRAPH_EXPANSION_LIMIT`
- `SEARCH_GRAPH_HOP_LIMIT`

## Optional: Enable AI Answers

Add:

```bash
OPENAI_API_KEY=your_key_here
OPENAI_MODEL=gpt-4o-mini
OPENAI_BASE_URL=
OPENAI_TEMPERATURE=0.1
```

If `OPENAI_API_KEY` is omitted, `/api/ask` stays in deterministic extractive mode and UI shows `LLM Not Configured`.

With OpenAI enabled, Ask responses are grounded and formatted as:

1. concise answer
2. evidence bullets
3. citations
4. suggested follow-up questions

## Lovable Frontend Connection

This repo is pre-wired so a Lovable-generated frontend can run on the same domain.
The frontend is served directly at `/`, while the Django UI remains available at `/demo`.
Current default root experience serves the imported Stitch HTML (`/stitch-screens/screen-01/code.html`).

- Backend API remains at `/api/*`
- Lovable frontend build artifacts are stored in `frontend/static`
- Existing Django demo UI remains at `/`

Build/deploy flow:

```bash
# 1) Import Lovable export zip
./scripts/import_lovable_export.sh /path/to/lovable-export.zip

# 2) Build static assets
./scripts/build_lovable_frontend.sh

# 3) Deploy (prod script auto-builds frontend if lovable-src exists)
./scripts/prod_deploy.sh
```

Import Stitch multi-screen ZIP sets (one ZIP per screen):

```bash
./scripts/import_stitch_screens.sh "C:\Users\simon\Downloads\stich ui"
./scripts/build_lovable_frontend.sh
```

The imported screens are exposed as direct routes:

- `/` (landing)
- `/papers`
- `/experts`
- `/graph`
- `/ask`

Set API base in Lovable frontend to empty or root-relative paths (recommended):

```bash
VITE_API_BASE_URL=
```

## Health + Integration Proof

Health:

```bash
curl -s http://localhost:8000/healthz
curl -s http://localhost:8000/health
```

`/health` and `/healthz` include dependency checks plus metrics:

- papers/authors/topics counts
- last OpenAlex sync timestamp

Verification and stats:

```bash
docker compose exec web python manage.py verify_data_pipeline
docker compose exec web python manage.py stats_openalex
```

## Example Queries

- `federated learning for RAN optimization in 5G`
- `network slicing reliability in 5G core`
- `O-RAN xApp policy optimization with reinforcement learning`
- `core network anomaly detection with graph neural networks`
- `energy efficient base station sleep control`
- `closed-loop RAN scheduling with multi-agent AI`
- `knowledge graph retrieval for telecom incident triage`
- `open RAN multi-vendor orchestration reliability`

## 2-Minute Demo Flow

1. Open `/` and click **Try the Demo**.
2. Run `federated learning for RAN optimization in 5G` in the Papers tab.
3. Switch to Experts and explain score breakdown + why-ranked text.
4. Switch to Graph to show Author-Paper-Topic paths.
5. Ask: `Who are the best experts for O-RAN xApp policy optimization?` and read citations.

## Useful Commands

- `docker compose exec web python manage.py createsuperuser`
- `docker compose exec web python manage.py ingest_openalex --query "graph rag" --limit 200 --since 2025-01-01`
- `docker compose exec web python manage.py seed_openalex --works 50 --authors 30 --query "machine learning" --years 2022-2026`
- `docker compose exec web python manage.py verify_data_pipeline`
- `docker compose exec web python manage.py stats_openalex`
