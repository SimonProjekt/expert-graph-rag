from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.db.models import QuerySet

from apps.documents.models import Paper
from apps.documents.services import EmbeddingError, EmbeddingService, PaperChunkingService


class Command(BaseCommand):
    help = "Chunk papers and embed pending chunks using configured embedding backend."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--limit", type=int, help="Only embed up to N papers.")

    def handle(self, *args: Any, **options: Any) -> None:
        limit = options.get("limit")

        queryset: QuerySet[Paper] = Paper.objects.order_by("id")
        if limit is not None:
            queryset = queryset[:limit]
        paper_ids = list(queryset.values_list("id", flat=True))

        chunking = PaperChunkingService()
        embedding = EmbeddingService()
        try:
            chunk_result = chunking.chunk_papers(paper_ids)
            updated = embedding.embed_pending_chunks(
                paper_ids=paper_ids,
                batch_size=128,
                backend_name=settings.EMBEDDING_BACKEND,
            )
        except EmbeddingError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                f"Chunked {chunk_result['papers_processed']} papers and embedded {updated} chunks."
            )
        )
