# expert-graph-rag

A recruiter-friendly Graph RAG demo built with Django + Postgres/pgvector + Neo4j + Redis/Celery.

- Runs without any API keys
- Uses deterministic/extractive fallback behavior when OpenAI is not configured
- Includes one-command demo data seeding

## Run in 60 Seconds

```bash
git clone <YOUR_REPO_URL> && cd expert-graph-rag
cp .env.example .env
docker compose up --build
```

The app is available at:

- `http://localhost:8000/`

### Required Environment Variables

These are required (or required with default placeholders in `.env.example`):

- `DJANGO_SECRET_KEY`
- `DEBUG`
- `DATABASE_URL`
- `NEO4J_URI`
- `NEO4J_USER`
- `NEO4J_PASSWORD`
- `OPENAI_API_KEY` (optional)

Optional host port overrides (if defaults are busy):

- `WEB_PORT` (default `8000`)
- `POSTGRES_PORT` (default `5432`)
- `REDIS_PORT` (default `6379`)
- `NEO4J_HTTP_PORT` (default `7474`)
- `NEO4J_BOLT_PORT` (default `7687`)

## Load Demo Data

In a new terminal, run:

```bash
docker compose exec web python manage.py seed_demo_data
```

This command will:

- load a small OpenAlex-style fixture dataset
- generate chunks + embeddings
- sync Author/Paper/Topic graph into Neo4j

## Optional: Enable AI Answers

Add your key to `.env`:

```bash
OPENAI_API_KEY=your_key_here
```

Then restart services:

```bash
docker compose up --build
```

If `OPENAI_API_KEY` is empty, `/api/ask` automatically uses extractive summarization,
and the UI shows:

- `Demo Mode (LLM Disabled)`

## Example Queries to Try

- federated learning for telecom optimization
- RAN optimization with graph retrieval
- knowledge graph incident triage
- 5G capacity planning workflows
- explainable retrieval for network operations
- multi-agent scheduling in wireless networks

## Health Endpoint

```bash
curl -s http://localhost:8000/healthz
```

`/healthz` returns JSON checks for:

- database
- neo4j
- embeddings presence

## Startup Diagnostics

On container startup, the app runs `python manage.py startup_check` and prints warnings if:

- embeddings are missing
- graph data is missing/incomplete

## Data Integration Verification (Optional)

```bash
docker compose exec web python manage.py verify_data_pipeline
```

or:

```bash
make verify_data_pipeline
```

## Useful Commands

- `docker compose exec web python manage.py createsuperuser`
- `docker compose exec web python manage.py verify_data_pipeline`
- `docker compose exec web python manage.py startup_check`
- `docker compose run --rm seed`
