from __future__ import annotations

import hashlib
from datetime import date
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError
from django.utils import timezone

from apps.documents.models import Embedding, IngestionRun, IngestionStatus
from apps.documents.openalex import OpenAlexIngestionError, OpenAlexIngestionService
from apps.documents.openalex_client import OpenAlexClient, OpenAlexClientError
from apps.documents.services import (
    ChunkingError,
    EmbeddingError,
    EmbeddingService,
    PaperChunkingService,
)
from apps.graphsync.services import GraphSyncError, GraphSyncService

DEFAULT_TOPIC_QUERIES = {
    "telecom": "telecom RAN optimization",
    "rag": "retrieval augmented generation",
    "knowledge-graph": "knowledge graph expert discovery",
}


class Command(BaseCommand):
    help = (
        "Seed local Postgres + embeddings (+ optional Neo4j sync) from OpenAlex so the demo "
        "starts with usable data."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--works",
            type=int,
            default=50,
            help="Target number of OpenAlex works to ingest (default: 50).",
        )
        parser.add_argument(
            "--authors",
            type=int,
            default=30,
            help="Target number of OpenAlex authors to upsert (default: 30).",
        )
        parser.add_argument(
            "--query",
            type=str,
            default="machine learning",
            help="Primary OpenAlex search query.",
        )
        parser.add_argument(
            "--years",
            type=str,
            default="2022-2026",
            help="Year range, e.g. 2022-2026 or single year 2025.",
        )
        parser.add_argument(
            "--topic",
            action="append",
            dest="topics",
            default=[],
            help=(
                "Optional seed topic. Can be passed multiple times. "
                "Recognized shortcuts: telecom, rag, knowledge-graph."
            ),
        )
        parser.add_argument(
            "--backend",
            type=str,
            choices=["auto", "local", "openai"],
            default="auto",
            help="Embedding backend selection (default: auto).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=128,
            help="Embedding batch size (default: 128).",
        )
        parser.add_argument(
            "--skip-graph-sync",
            action="store_true",
            help="Skip Neo4j synchronization after ingest + embeddings.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        works_target = int(options["works"])
        authors_target = int(options["authors"])
        query = str(options["query"]).strip()
        years = str(options["years"]).strip()
        topics = [str(item).strip() for item in options.get("topics") or [] if str(item).strip()]
        backend = str(options["backend"])
        batch_size = int(options["batch_size"])
        skip_graph_sync = bool(options["skip_graph_sync"])

        if works_target <= 0:
            raise CommandError("--works must be greater than 0.")
        if authors_target <= 0:
            raise CommandError("--authors must be greater than 0.")
        if not query:
            raise CommandError("--query cannot be empty.")
        if batch_size <= 0:
            raise CommandError("--batch-size must be greater than 0.")
        if not settings.OPENALEX_API_KEY:
            raise CommandError("OPENALEX_API_KEY is required for seed_openalex.")

        since, until = self._parse_years(years)
        filter_expression = self._build_year_filter(since=since, until=until)
        queries = self._build_queries(primary_query=query, topics=topics)

        run = IngestionRun.objects.create(
            query=self._build_run_query(queries=queries, years=years),
            status=IngestionStatus.RUNNING,
            counts={
                "source": "seed_openalex",
                "works_target": works_target,
                "authors_target": authors_target,
                "queries": queries,
                "years": years,
            },
        )

        try:
            client = OpenAlexClient(
                base_url=settings.OPENALEX_BASE_URL,
                api_key=settings.OPENALEX_API_KEY,
                mailto=settings.OPENALEX_MAILTO,
                timeout_seconds=settings.OPENALEX_HTTP_TIMEOUT_SECONDS,
                max_retries=settings.OPENALEX_MAX_RETRIES,
                backoff_seconds=settings.OPENALEX_BACKOFF_SECONDS,
                rate_limit_rps=settings.OPENALEX_RATE_LIMIT_RPS,
                page_size=settings.OPENALEX_PAGE_SIZE,
            )
            ingestion = OpenAlexIngestionService(
                client=client,
                security_level_ratios=settings.OPENALEX_SECURITY_LEVEL_RATIOS,
            )
        except (OpenAlexClientError, ValueError) as exc:
            self._mark_failed(run=run, error_message=str(exc))
            raise CommandError(f"OpenAlex client setup failed: {exc}") from exc

        totals = {
            "works_processed": 0,
            "works_skipped": 0,
            "papers_created": 0,
            "papers_updated": 0,
            "authors_created": 0,
            "authors_updated": 0,
            "topics_created": 0,
            "topics_updated": 0,
            "authorship_links": 0,
            "paper_topic_links": 0,
            "authors_processed": 0,
        }
        seeded_paper_ids: set[int] = set()

        try:
            for query_text in queries:
                remaining_works = works_target - len(seeded_paper_ids)
                if remaining_works <= 0:
                    break

                summary = ingestion.ingest_with_details(
                    query=query_text,
                    limit=remaining_works,
                    since=since,
                    filter_expression=filter_expression,
                )
                seeded_paper_ids.update(summary.paper_ids)
                for key, value in summary.counts.items():
                    if key in totals:
                        totals[key] += value

            remaining_authors = max(authors_target - totals["authors_created"], 0)
            if remaining_authors > 0:
                for query_text in queries:
                    if remaining_authors <= 0:
                        break
                    raw_authors = client.iter_authors(
                        query=query_text,
                        limit=remaining_authors,
                    )
                    author_totals = ingestion.upsert_authors(
                        raw_authors=raw_authors,
                        limit=remaining_authors,
                    )
                    totals["authors_created"] += int(author_totals["authors_created"])
                    totals["authors_updated"] += int(author_totals["authors_updated"])
                    totals["authors_processed"] += int(author_totals["authors_processed"])
                    remaining_authors -= int(author_totals["authors_processed"])

            chunk_stats = PaperChunkingService().chunk_papers(sorted(seeded_paper_ids))
            try:
                chunks_embedded = EmbeddingService().embed_pending_chunks(
                    paper_ids=sorted(seeded_paper_ids),
                    batch_size=batch_size,
                    backend_name=backend,
                )
                embedding_mode = backend
            except EmbeddingError as exc:
                embedding_mode = "deterministic-fallback"
                self.stdout.write(
                    self.style.WARNING(
                        "Embedding backend unavailable during seed_openalex; "
                        f"using deterministic fallback vectors. Reason: {exc}"
                    )
                )
                chunks_embedded = self._deterministic_embed(sorted(seeded_paper_ids))

            graph_message = "graph sync skipped"
            if not skip_graph_sync:
                try:
                    graph_result = GraphSyncService().sync_to_neo4j(include_collaborators=True)
                    graph_message = (
                        f"papers={graph_result.papers_synced}/{graph_result.papers_total}, "
                        f"relations={graph_result.relationships_synced}, "
                        f"collaborators={graph_result.collaborators_synced}"
                    )
                except GraphSyncError as exc:
                    raise CommandError(f"Graph sync failed during seed_openalex: {exc}") from exc

            finished = timezone.now()
            counts = dict(totals)
            counts.update(
                {
                    "source": "seed_openalex",
                    "works_target": works_target,
                    "authors_target": authors_target,
                    "works_seeded_unique": len(seeded_paper_ids),
                    "queries": queries,
                    "years": years,
                    "chunks_generated": chunk_stats["chunks_generated"],
                    "chunks_created": chunk_stats["chunks_created"],
                    "chunks_updated": chunk_stats["chunks_updated"],
                    "chunks_embedded": chunks_embedded,
                    "embedding_mode": embedding_mode,
                    "graph": graph_message,
                }
            )

            run.status = IngestionStatus.SUCCESS
            run.finished_at = finished
            run.error_message = ""
            run.counts = counts
            run.save(update_fields=["status", "finished_at", "error_message", "counts"])

            self.stdout.write(
                self.style.SUCCESS(
                    "OpenAlex seed complete: "
                    f"works_seeded={len(seeded_paper_ids)}, "
                    f"works_processed={totals['works_processed']}, "
                    f"authors_processed={totals['authors_processed'] + totals['authors_created']}, "
                    f"chunks_embedded={chunks_embedded}, "
                    f"embedding_mode={embedding_mode}, "
                    f"graph={graph_message}"
                )
            )
        except (
            OpenAlexIngestionError,
            OpenAlexClientError,
            DatabaseError,
            ChunkingError,
            EmbeddingError,
            CommandError,
        ) as exc:
            self._mark_failed(run=run, error_message=str(exc))
            if isinstance(exc, CommandError):
                raise
            raise CommandError(f"seed_openalex failed: {exc}") from exc

    @staticmethod
    def _parse_years(raw_value: str) -> tuple[date | None, date | None]:
        value = raw_value.strip()
        if not value:
            return None, None

        parts = [part.strip() for part in value.split("-", maxsplit=1)]
        if len(parts) == 1:
            year = Command._parse_year(parts[0], label="year")
            return date(year, 1, 1), date(year, 12, 31)

        start_year = Command._parse_year(parts[0], label="start year")
        end_year = Command._parse_year(parts[1], label="end year")
        if start_year > end_year:
            raise CommandError("--years start must be <= end (e.g. 2022-2026).")
        return date(start_year, 1, 1), date(end_year, 12, 31)

    @staticmethod
    def _parse_year(value: str, *, label: str) -> int:
        if len(value) != 4 or not value.isdigit():
            raise CommandError(f"Invalid {label} {value!r}; use YYYY.")
        year = int(value)
        if year < 1900 or year > 2100:
            raise CommandError(f"Invalid {label} {value!r}; must be between 1900 and 2100.")
        return year

    @staticmethod
    def _build_year_filter(*, since: date | None, until: date | None) -> str | None:
        filters: list[str] = []
        if since is not None:
            filters.append(f"from_publication_date:{since.isoformat()}")
        if until is not None:
            filters.append(f"to_publication_date:{until.isoformat()}")
        return ",".join(filters) if filters else None

    @staticmethod
    def _build_queries(*, primary_query: str, topics: list[str]) -> list[str]:
        queries: list[str] = [primary_query]
        for topic in topics:
            normalized = topic.strip().lower()
            if not normalized:
                continue
            queries.append(DEFAULT_TOPIC_QUERIES.get(normalized, f"{normalized} research"))

        deduped: list[str] = []
        seen: set[str] = set()
        for item in queries:
            key = item.strip().lower()
            if key in seen or not key:
                continue
            seen.add(key)
            deduped.append(item.strip())
        return deduped

    @staticmethod
    def _build_run_query(*, queries: list[str], years: str) -> str:
        rendered_queries = " | ".join(queries)
        return f"seed_openalex years={years} queries={rendered_queries}"

    @staticmethod
    def _mark_failed(*, run: IngestionRun, error_message: str) -> None:
        run.status = IngestionStatus.FAILED
        run.finished_at = timezone.now()
        run.error_message = error_message[:5000]
        run.save(update_fields=["status", "finished_at", "error_message"])

    @staticmethod
    def _deterministic_embed(paper_ids: list[int]) -> int:
        rows = list(
            Embedding.objects.filter(paper_id__in=paper_ids, embedding__isnull=True).only(
                "id",
                "text_chunk",
            )
        )
        if not rows:
            return 0

        for row in rows:
            row.embedding = Command._hash_vector(row.text_chunk)
        Embedding.objects.bulk_update(rows, ["embedding"])
        return len(rows)

    @staticmethod
    def _hash_vector(text: str) -> list[float]:
        digest = hashlib.sha256(text.encode("utf-8")).digest()
        values: list[float] = []
        for idx in range(settings.EMBEDDING_DIM):
            left = digest[(idx * 2) % len(digest)]
            right = digest[(idx * 2 + 1) % len(digest)]
            packed = (left << 8) | right
            values.append(packed / 65535.0)
        return values

