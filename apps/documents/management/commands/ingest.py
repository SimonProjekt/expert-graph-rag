from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.documents.models import SecurityLevel
from apps.documents.services import DocumentIngestionService, IngestInput, IngestionError


class Command(BaseCommand):
    help = "Ingest demo papers or load papers from a JSON file."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--file",
            type=str,
            help=(
                "Path to a JSON file containing a list of papers with "
                "title/abstract/published_date/doi/external_id/security_level/authors/topics."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        file_path = options.get("file")
        items = self._load_items(file_path)

        service = DocumentIngestionService()
        try:
            created = service.ingest(items)
        except IngestionError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(self.style.SUCCESS(f"Ingested {len(created)} papers."))

    def _load_items(self, file_path: str | None) -> list[IngestInput]:
        if file_path is None:
            return [
                IngestInput(
                    title="Graph RAG for Enterprise Knowledge Systems",
                    abstract=(
                        "Graph RAG combines vector retrieval with graph traversal "
                        "to improve explainability and factual grounding."
                    ),
                    published_date=date(2024, 11, 4),
                    doi="10.5555/graph-rag-enterprise-001",
                    external_id="paper:graph-rag-enterprise-001",
                    security_level=SecurityLevel.INTERNAL,
                    authors=("Alice Smith", "Bob Chen"),
                    topics=("graph-rag", "retrieval"),
                ),
                IngestInput(
                    title="Neo4j and pgvector in a Hybrid Retrieval Stack",
                    abstract=(
                        "Using Neo4j and Postgres pgvector together enables both semantic "
                        "similarity and relationship-aware traversal."
                    ),
                    published_date=date(2025, 1, 12),
                    doi="10.5555/hybrid-retrieval-002",
                    external_id="paper:hybrid-retrieval-002",
                    security_level=SecurityLevel.PUBLIC,
                    authors=("Clara Patel",),
                    topics=("neo4j", "pgvector"),
                ),
            ]

        path = Path(file_path)
        if not path.exists():
            raise CommandError(f"File does not exist: {path}")

        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except OSError as exc:
            raise CommandError(f"Could not read file {path}: {exc}") from exc
        except json.JSONDecodeError as exc:
            raise CommandError(f"Invalid JSON in file {path}: {exc}") from exc

        if not isinstance(payload, list):
            raise CommandError("JSON file must contain a list of papers.")

        items: list[IngestInput] = []
        for index, item in enumerate(payload, start=1):
            if not isinstance(item, dict):
                raise CommandError(f"Item {index} must be an object.")

            title = item.get("title")
            abstract = item.get("abstract", "")
            external_id = item.get("external_id")
            doi = item.get("doi")
            security_level = item.get("security_level", SecurityLevel.PUBLIC)
            published_date = self._parse_date(item.get("published_date"), index=index)
            authors = item.get("authors", [])
            topics = item.get("topics", [])

            if not isinstance(title, str) or not title.strip():
                raise CommandError(f"Item {index} has invalid 'title'.")
            if not isinstance(abstract, str):
                raise CommandError(f"Item {index} has invalid 'abstract'.")
            if not isinstance(external_id, str) or not external_id.strip():
                raise CommandError(f"Item {index} has invalid 'external_id'.")
            if doi is not None and not isinstance(doi, str):
                raise CommandError(f"Item {index} has invalid 'doi'.")
            if not isinstance(security_level, str) or security_level not in SecurityLevel.values:
                raise CommandError(
                    f"Item {index} has invalid 'security_level'. "
                    f"Allowed values: {list(SecurityLevel.values)}"
                )
            if not isinstance(authors, list) or any(not isinstance(a, str) for a in authors):
                raise CommandError(f"Item {index} has invalid 'authors'; expected list[str].")
            if not isinstance(topics, list) or any(not isinstance(t, str) for t in topics):
                raise CommandError(f"Item {index} has invalid 'topics'; expected list[str].")

            items.append(
                IngestInput(
                    title=title,
                    abstract=abstract,
                    published_date=published_date,
                    doi=doi,
                    external_id=external_id,
                    security_level=security_level,
                    authors=tuple(authors),
                    topics=tuple(topics),
                )
            )

        return items

    @staticmethod
    def _parse_date(raw: Any, *, index: int) -> date | None:
        if raw in (None, ""):
            return None
        if not isinstance(raw, str):
            raise CommandError(f"Item {index} has invalid 'published_date'; expected YYYY-MM-DD string.")
        try:
            return date.fromisoformat(raw)
        except ValueError as exc:
            raise CommandError(
                f"Item {index} has invalid 'published_date' format. Use YYYY-MM-DD."
            ) from exc
