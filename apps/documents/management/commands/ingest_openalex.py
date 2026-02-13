from __future__ import annotations

import json
import logging
from datetime import date
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from django.utils import timezone

from apps.documents.models import IngestionRun, IngestionStatus
from apps.documents.openalex import (
    OpenAlexClient,
    OpenAlexIngestionError,
    OpenAlexIngestionService,
)

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = (
        "Ingest works from OpenAlex and upsert them into local "
        "Paper/Author/Topic models."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--query",
            required=True,
            type=str,
            help="OpenAlex search query.",
        )
        parser.add_argument(
            "--limit",
            type=int,
            default=500,
            help="Maximum number of works to ingest.",
        )
        parser.add_argument(
            "--since",
            type=str,
            help="Only include works from this date (YYYY-MM-DD).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        query = options["query"].strip()
        limit = int(options["limit"])
        since_raw = options.get("since")

        if not query:
            raise CommandError("--query cannot be empty.")
        if limit <= 0:
            raise CommandError("--limit must be greater than 0.")

        since = self._parse_since(since_raw)

        ingestion_run = IngestionRun.objects.create(
            query=query,
            status=IngestionStatus.RUNNING,
            counts={"requested_limit": limit},
        )

        logger.info(
            json.dumps(
                {
                    "event": "openalex.ingest_started",
                    "query": query,
                    "limit": limit,
                    "since": since.isoformat() if since else None,
                    "ingestion_run_id": ingestion_run.id,
                },
                sort_keys=True,
            )
        )

        try:
            client = OpenAlexClient(
                base_url=settings.OPENALEX_BASE_URL,
                timeout_seconds=settings.OPENALEX_HTTP_TIMEOUT_SECONDS,
                max_retries=settings.OPENALEX_MAX_RETRIES,
                backoff_seconds=settings.OPENALEX_BACKOFF_SECONDS,
                rate_limit_rps=settings.OPENALEX_RATE_LIMIT_RPS,
                page_size=settings.OPENALEX_PAGE_SIZE,
            )
            service = OpenAlexIngestionService(
                client=client,
                security_level_ratios=settings.OPENALEX_SECURITY_LEVEL_RATIOS,
            )
            counts = service.ingest(query=query, limit=limit, since=since)
        except (OpenAlexIngestionError, DatabaseError) as exc:
            self._mark_failed(ingestion_run=ingestion_run, error_message=str(exc))
            raise CommandError(f"OpenAlex ingestion failed: {exc}") from exc
        except Exception as exc:  # noqa: BLE001
            self._mark_failed(
                ingestion_run=ingestion_run,
                error_message=f"Unexpected failure: {exc}",
            )
            raise CommandError(f"OpenAlex ingestion failed unexpectedly: {exc}") from exc

        ingestion_run.status = IngestionStatus.SUCCESS
        ingestion_run.finished_at = timezone.now()
        ingestion_run.error_message = ""
        ingestion_run.counts = counts
        ingestion_run.save(update_fields=["status", "finished_at", "error_message", "counts"])

        logger.info(
            json.dumps(
                {
                    "event": "openalex.ingest_finished",
                    "query": query,
                    "limit": limit,
                    "since": since.isoformat() if since else None,
                    "ingestion_run_id": ingestion_run.id,
                    "counts": counts,
                },
                sort_keys=True,
            )
        )

        self.stdout.write(
            self.style.SUCCESS(f"OpenAlex ingestion completed. Counts: {json.dumps(counts)}")
        )

    @staticmethod
    def _parse_since(raw_value: str | None) -> date | None:
        if raw_value is None:
            return None

        value = raw_value.strip()
        if not value:
            return None

        try:
            return date.fromisoformat(value)
        except ValueError as exc:
            raise CommandError("--since must be in YYYY-MM-DD format.") from exc

    @staticmethod
    def _mark_failed(*, ingestion_run: IngestionRun, error_message: str) -> None:
        ingestion_run.status = IngestionStatus.FAILED
        ingestion_run.finished_at = timezone.now()
        ingestion_run.error_message = error_message[:5000]
        ingestion_run.save(update_fields=["status", "finished_at", "error_message"])
