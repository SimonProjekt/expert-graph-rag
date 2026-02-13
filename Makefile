COMPOSE = docker compose
WEB_RUN = $(COMPOSE) run --rm web
OPENALEX_LIMIT ?= 500

.PHONY: dev test lint migrate createsuperuser ingest ingest_openalex
.PHONY: embed_papers embed sync_to_neo4j sync_graph compute_graph_metrics verify_data_pipeline
.PHONY: seed_demo_data seed_openalex startup_check stats_openalex

dev:
	$(COMPOSE) up --build -d

test:
	$(WEB_RUN) pytest -q

lint:
	$(WEB_RUN) ruff check .

migrate:
	$(WEB_RUN) python manage.py migrate

createsuperuser:
	$(WEB_RUN) python manage.py createsuperuser

ingest:
	$(WEB_RUN) python manage.py ingest

ingest_openalex:
	$(WEB_RUN) python manage.py ingest_openalex --query "$(QUERY)" --limit $(OPENALEX_LIMIT) \
		$(if $(SINCE),--since $(SINCE),)

embed:
	$(WEB_RUN) python manage.py embed

embed_papers:
	$(WEB_RUN) python manage.py embed_papers --batch $${BATCH:-128} --workers $${WORKERS:-2} \
		$${BACKEND:+--backend $$BACKEND} $${CHUNK_SIZE:+--chunk-size $$CHUNK_SIZE} \
		$${OVERLAP:+--overlap $$OVERLAP}

sync_to_neo4j:
	$(WEB_RUN) python manage.py sync_to_neo4j $(if $(SYNC_LIMIT),--limit $(SYNC_LIMIT),) \
		$(if $(INCLUDE_COLLABORATORS),--include-collaborators,) \
		$(if $(PROGRESS_EVERY),--progress-every $(PROGRESS_EVERY),)

sync_graph:
	$(WEB_RUN) python manage.py sync_graph

compute_graph_metrics:
	$(WEB_RUN) python manage.py compute_graph_metrics $(if $(NO_RESET_MISSING),--no-reset-missing,)

verify_data_pipeline:
	$(WEB_RUN) python manage.py verify_data_pipeline $(if $(QUERY),--query "$(QUERY)",)

seed_demo_data:
	$(WEB_RUN) python manage.py seed_demo_data $(if $(BACKEND),--backend $(BACKEND),) \
		$(if $(SKIP_GRAPH_SYNC),--skip-graph-sync,)

seed_openalex:
	$(WEB_RUN) python manage.py seed_openalex --works $${WORKS:-50} --authors $${AUTHORS:-30} \
		--query "$${QUERY:-machine learning}" --years $${YEARS:-2022-2026} \
		$${TOPIC:+--topic $$TOPIC} $${BACKEND:+--backend $$BACKEND} \
		$${BATCH_SIZE:+--batch-size $$BATCH_SIZE} $${SKIP_GRAPH_SYNC:+--skip-graph-sync}

startup_check:
	$(WEB_RUN) python manage.py startup_check

stats_openalex:
	$(WEB_RUN) python manage.py stats_openalex
