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
- `OPENALEX_MAILTO` (recommended by OpenAlex polite pool guidance)

You can generate an OpenAlex key from your OpenAlex account/dashboard, then place it in `.env`.

All OpenAlex requests include:

- `api_key`
- `mailto`

and use resilient retries/backoff + rate limiting.

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

## Optional: Enable AI Answers

Add:

```bash
OPENAI_API_KEY=your_key_here
```

If omitted, `/api/ask` stays in deterministic extractive mode and UI shows `Demo Mode (LLM Disabled)`.

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

