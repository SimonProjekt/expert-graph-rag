from __future__ import annotations

import hashlib
import json
from datetime import date
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db import DatabaseError, IntegrityError, transaction

from apps.documents.models import (
    Author,
    Authorship,
    Embedding,
    Paper,
    PaperTopic,
    SecurityLevel,
    Topic,
)
from apps.documents.services import EmbeddingError, EmbeddingService, PaperChunkingService
from apps.graphsync.services import GraphSyncError, GraphSyncService


class Command(BaseCommand):
    help = (
        "Seed demo papers/authors/topics, generate embeddings, and sync the graph so the app "
        "is usable immediately."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--fixture",
            type=str,
            default=str(Path("apps/documents/fixtures/demo_openalex_sample.json")),
            help="Path to seed fixture JSON file.",
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
            help="Skip Neo4j synchronization step.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        fixture_path = Path(options["fixture"])
        backend = str(options["backend"])
        batch_size = int(options["batch_size"])
        skip_graph_sync = bool(options["skip_graph_sync"])

        if batch_size <= 0:
            raise CommandError("--batch-size must be greater than 0.")

        records = self._load_fixture(fixture_path)
        paper_ids = self._upsert_records(records)

        chunk_stats = PaperChunkingService().chunk_papers(paper_ids)

        embedding_mode = backend
        try:
            embedded = EmbeddingService().embed_pending_chunks(
                paper_ids=paper_ids,
                batch_size=batch_size,
                backend_name=backend,
            )
        except EmbeddingError as exc:
            embedding_mode = "deterministic-fallback"
            self.stdout.write(
                self.style.WARNING(
                    "Embedding backend unavailable; using deterministic fallback vectors. "
                    f"Reason: {exc}"
                )
            )
            embedded = self._deterministic_embed(paper_ids)

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
                raise CommandError(f"Graph sync failed during demo seed: {exc}") from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Demo seed complete: "
                f"papers={len(paper_ids)}, "
                f"chunks_generated={chunk_stats['chunks_generated']}, "
                f"chunks_created={chunk_stats['chunks_created']}, "
                f"chunks_updated={chunk_stats['chunks_updated']}, "
                f"chunks_embedded={embedded}, "
                f"embedding_mode={embedding_mode}, "
                f"graph={graph_message}"
            )
        )

    def _load_fixture(self, fixture_path: Path) -> list[dict[str, Any]]:
        if not fixture_path.exists():
            raise CommandError(f"Fixture file does not exist: {fixture_path}")

        try:
            payload = json.loads(fixture_path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CommandError(f"Could not read fixture file {fixture_path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON in fixture file {fixture_path}: {exc}") from exc

        if not isinstance(payload, list) or not payload:
            raise CommandError("Fixture JSON must contain a non-empty list of papers.")

        validated: list[dict[str, Any]] = []
        for index, row in enumerate(payload, start=1):
            if not isinstance(row, dict):
                raise CommandError(f"Fixture item {index} must be an object.")

            title = str(row.get("title", "")).strip()
            abstract = str(row.get("abstract", "")).strip()
            external_id = str(row.get("external_id", "")).strip()
            published_date = row.get("published_date")
            doi = row.get("doi")
            security_level = str(row.get("security_level", SecurityLevel.PUBLIC)).strip().upper()

            if not title:
                raise CommandError(f"Fixture item {index} is missing title.")
            if not external_id:
                raise CommandError(f"Fixture item {index} is missing external_id.")
            if security_level not in SecurityLevel.values:
                raise CommandError(
                    f"Fixture item {index} has invalid security_level {security_level!r}."
                )

            authors = row.get("authors")
            topics = row.get("topics")
            if not isinstance(authors, list) or not authors:
                raise CommandError(f"Fixture item {index} must contain a non-empty authors list.")
            if not isinstance(topics, list) or not topics:
                raise CommandError(f"Fixture item {index} must contain a non-empty topics list.")

            parsed_published_date: date | None = None
            if published_date not in (None, ""):
                if not isinstance(published_date, str):
                    raise CommandError(
                        f"Fixture item {index} has invalid published_date; use YYYY-MM-DD."
                    )
                try:
                    parsed_published_date = date.fromisoformat(published_date)
                except ValueError as exc:
                    raise CommandError(
                        f"Fixture item {index} has invalid published_date format."
                    ) from exc

            validated.append(
                {
                    "title": title,
                    "abstract": abstract,
                    "external_id": external_id,
                    "published_date": parsed_published_date,
                    "doi": doi,
                    "security_level": security_level,
                    "authors": authors,
                    "topics": topics,
                }
            )

        return validated

    def _upsert_records(self, records: list[dict[str, Any]]) -> list[int]:
        paper_ids: list[int] = []

        for row in records:
            defaults = {
                "title": row["title"],
                "abstract": row["abstract"],
                "published_date": row["published_date"],
                "doi": (str(row["doi"]).strip() if row["doi"] else None),
                "security_level": row["security_level"],
            }

            try:
                with transaction.atomic():
                    paper, _ = Paper.objects.update_or_create(
                        external_id=row["external_id"],
                        defaults=defaults,
                    )

                    self._replace_authorships(
                        paper=paper,
                        author_payloads=self._dedupe_payloads_by_external_id(row["authors"]),
                    )
                    self._replace_topics(
                        paper=paper,
                        topic_payloads=self._dedupe_payloads_by_external_id(row["topics"]),
                    )

                    paper_ids.append(paper.id)
            except (IntegrityError, DatabaseError) as exc:
                raise CommandError(
                    f"Failed to upsert paper {row['external_id']!r}: {exc}"
                ) from exc

        return paper_ids

    def _replace_authorships(
        self,
        *,
        paper: Paper,
        author_payloads: list[dict[str, Any]],
    ) -> None:
        authorships: list[Authorship] = []
        for author_order, author_payload in enumerate(author_payloads, start=1):
            author = self._upsert_author(author_payload)
            authorships.append(
                Authorship(
                    paper=paper,
                    author=author,
                    author_order=author_order,
                )
            )

        Authorship.objects.filter(paper=paper).delete()
        if authorships:
            Authorship.objects.bulk_create(authorships)

    def _replace_topics(
        self,
        *,
        paper: Paper,
        topic_payloads: list[dict[str, Any]],
    ) -> None:
        topic_rows: list[PaperTopic] = []
        topic_ids: set[int] = set()
        for topic_payload in topic_payloads:
            topic = self._upsert_topic(topic_payload)
            if topic.id in topic_ids:
                continue
            topic_ids.add(topic.id)
            topic_rows.append(PaperTopic(paper=paper, topic=topic))

        PaperTopic.objects.filter(paper=paper).delete()
        if topic_rows:
            PaperTopic.objects.bulk_create(topic_rows)

    @staticmethod
    def _dedupe_payloads_by_external_id(payloads: list[dict[str, Any]]) -> list[dict[str, Any]]:
        seen_external_ids: set[str] = set()
        deduped: list[dict[str, Any]] = []

        for payload in payloads:
            external_id = str(payload.get("external_id", "")).strip()
            if not external_id:
                deduped.append(payload)
                continue
            if external_id in seen_external_ids:
                continue
            seen_external_ids.add(external_id)
            deduped.append(payload)

        return deduped

    @staticmethod
    def _upsert_author(payload: dict[str, Any]) -> Author:
        external_id = str(payload.get("external_id", "")).strip()
        name = str(payload.get("name", "")).strip()
        institution_name = str(payload.get("institution_name", "unknown")).strip() or "unknown"

        if not external_id or not name:
            raise CommandError(
                "Each author in fixture must include non-empty name and external_id."
            )

        author, _ = Author.objects.update_or_create(
            external_id=external_id,
            defaults={
                "name": name,
                "institution_name": institution_name,
            },
        )
        return author

    @staticmethod
    def _upsert_topic(payload: dict[str, Any]) -> Topic:
        external_id = str(payload.get("external_id", "")).strip()
        name = str(payload.get("name", "")).strip()

        if not external_id or not name:
            raise CommandError("Each topic in fixture must include non-empty name and external_id.")

        topic, _ = Topic.objects.update_or_create(
            external_id=external_id,
            defaults={"name": name},
        )
        return topic

    def _deterministic_embed(self, paper_ids: list[int]) -> int:
        if not paper_ids:
            return 0

        rows = list(
            Embedding.objects.filter(paper_id__in=paper_ids, embedding__isnull=True).only(
                "id",
                "text_chunk",
            )
        )
        if not rows:
            return 0

        for row in rows:
            row.embedding = self._hash_vector(row.text_chunk)

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
