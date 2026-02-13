# expert-graph-rag

Recruiter-ready Graph RAG demo built with Django + DRF + Postgres/pgvector + Neo4j + Redis/Celery.

## Run in 60 Seconds

```bash
git clone <YOUR_REPO_URL> && cd expert-graph-rag
cp .env.example .env
docker compose up --build
```

App URL:

- `http://localhost:8000/`

## Load Demo Data

Fast local fixture seed (no API keys required):

```bash
docker compose exec web python manage.py seed_demo_data
```

Interview-safe one-command setup:

```bash
make demo_ready
```

OpenAlex seed (recommended when `OPENALEX_API_KEY` is configured):

```bash
docker compose exec web python manage.py seed_openalex --works 50 --authors 30 --query "machine learning" --years 2022-2026
```

Topic-based seed examples:

```bash
docker compose exec web python manage.py seed_openalex --query "telecom optimization" --topic telecom --topic rag --topic knowledge-graph
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
- `score_breakdown` (`semantic_relevance`, `graph_authority`, `graph_centrality`)
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
For interview stability, production currently redirects `/app` to `/` (stable Django UI).

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

## Example Recruiter Queries

- `federated learning for telecom networks`
- `RAN optimization with retrieval-augmented systems`
- `knowledge graph incident triage for operations`

## Useful Commands

- `docker compose exec web python manage.py createsuperuser`
- `docker compose exec web python manage.py ingest_openalex --query "graph rag" --limit 200 --since 2025-01-01`
- `docker compose exec web python manage.py seed_openalex --works 50 --authors 30 --query "machine learning" --years 2022-2026`
- `docker compose exec web python manage.py verify_data_pipeline`
- `docker compose exec web python manage.py stats_openalex`
