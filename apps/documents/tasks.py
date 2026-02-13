from __future__ import annotations

from typing import Sequence

from celery import shared_task

from apps.documents.models import Paper
from apps.documents.services import EmbeddingService, PaperChunkingService


@shared_task(name="documents.chunk_papers")
def chunk_papers(
    paper_ids: Sequence[int] | None = None,
    chunk_size: int | None = None,
    chunk_overlap: int | None = None,
) -> dict[str, int]:
    target_ids = list(paper_ids) if paper_ids is not None else list(
        Paper.objects.order_by("id").values_list("id", flat=True)
    )

    service = PaperChunkingService(
        chunk_size=chunk_size,
        overlap=chunk_overlap,
    )
    return service.chunk_papers(target_ids)


@shared_task(name="documents.embed_chunks")
def embed_chunks(
    paper_ids: Sequence[int] | None = None,
    batch_size: int = 128,
    backend_name: str | None = None,
) -> dict[str, int]:
    service = EmbeddingService()
    embedded = service.embed_pending_chunks(
        paper_ids=paper_ids,
        batch_size=batch_size,
        backend_name=backend_name,
    )
    return {"chunks_embedded": embedded}


@shared_task(name="documents.embed_pending")
def embed_pending_documents(limit: int | None = None) -> int:
    service = EmbeddingService()
    return service.embed_pending(limit=limit)
