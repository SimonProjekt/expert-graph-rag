COMPOSE = docker compose
WEB_RUN = $(COMPOSE) run --rm web
OPENALEX_LIMIT ?= 500

.PHONY: dev test lint migrate createsuperuser ingest ingest_openalex
.PHONY: embed_papers embed sync_to_neo4j sync_graph compute_graph_metrics verify_data_pipeline
.PHONY: seed_demo_data seed_openalex seed_interview_data startup_check stats_openalex build_lovable_frontend
.PHONY: import_lovable_export demo_ready

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
	$(COMPOSE) build web
	$(WEB_RUN) python manage.py seed_demo_data $(if $(BACKEND),--backend $(BACKEND),) \
		$(if $(SKIP_GRAPH_SYNC),--skip-graph-sync,)

seed_openalex:
	$(COMPOSE) build web
	$(WEB_RUN) python manage.py seed_openalex --works $${WORKS:-50} --authors $${AUTHORS:-30} \
		--query "$${QUERY:-machine learning}" --years $${YEARS:-2022-2026} \
		$${TOPIC:+--topic $$TOPIC} $${BACKEND:+--backend $$BACKEND} \
		$${BATCH_SIZE:+--batch-size $$BATCH_SIZE} $${SKIP_GRAPH_SYNC:+--skip-graph-sync}

seed_interview_data:
	$(COMPOSE) build web
	$(WEB_RUN) python manage.py seed_interview_data --works-per-query $${WORKS_PER_QUERY:-80} \
		--authors-per-query $${AUTHORS_PER_QUERY:-40} --years $${YEARS:-2021-2026} \
		--backend $${BACKEND:-local} --batch-size $${BATCH_SIZE:-128} \
		$${SKIP_OPENALEX:+--skip-openalex} $${SKIP_VERIFY:+--skip-verify}

startup_check:
	$(WEB_RUN) python manage.py startup_check

stats_openalex:
	$(WEB_RUN) python manage.py stats_openalex

build_lovable_frontend:
	./scripts/build_lovable_frontend.sh

import_lovable_export:
	./scripts/import_lovable_export.sh "$(ZIP)"

demo_ready:
	$(WEB_RUN) python manage.py migrate --noinput
	$(WEB_RUN) python manage.py startup_check
	$(WEB_RUN) python manage.py seed_demo_data --backend local
	$(WEB_RUN) python manage.py verify_data_pipeline --query "5G RAN optimization"
	@echo "Interview demo ready: use https://<domain>/"
