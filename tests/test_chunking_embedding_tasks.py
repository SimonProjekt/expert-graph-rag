from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from apps.documents.models import Embedding, Paper, SecurityLevel
from apps.documents.services import chunk_text
from apps.documents.tasks import chunk_papers, embed_chunks


class FakeBackend:
    def __init__(self, dimensions: int) -> None:
        self._dimensions = dimensions

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        vectors: list[list[float]] = []
        for text in texts:
            base = float((len(text) % 7) + 1)
            vectors.append([base] * self._dimensions)
        return vectors


def test_chunk_text_is_deterministic() -> None:
    text = "one two three four five six seven eight nine"

    first = chunk_text(text, chunk_size=4, overlap=1)
    second = chunk_text(text, chunk_size=4, overlap=1)

    assert first == second
    assert first == [
        "one two three four",
        "four five six seven",
        "seven eight nine",
    ]


@pytest.mark.django_db
def test_chunk_and_embed_tasks_save_vectors(settings: Any) -> None:
    paper = Paper.objects.create(
        title="Graph RAG Architecture",
        abstract="A practical chunking and embedding pipeline for enterprise search.",
        external_id="paper:chunk-test-001",
        security_level=SecurityLevel.INTERNAL,
    )

    chunk_stats = chunk_papers(paper_ids=[paper.id], chunk_size=5, chunk_overlap=2)

    assert chunk_stats["papers_processed"] == 1
    assert chunk_stats["chunks_created"] >= 1

    chunks = list(Embedding.objects.filter(paper=paper).order_by("chunk_id"))
    assert chunks
    assert all(chunk.embedding is None for chunk in chunks)

    with patch(
        "apps.documents.services.get_embedding_backend",
        return_value=FakeBackend(settings.EMBEDDING_DIM),
    ):
        embed_stats = embed_chunks(paper_ids=[paper.id], batch_size=64, backend_name="local")

    assert embed_stats["chunks_embedded"] == len(chunks)

    saved = list(Embedding.objects.filter(paper=paper).order_by("chunk_id"))
    assert all(chunk.embedding is not None for chunk in saved)
    assert all(len(chunk.embedding) == settings.EMBEDDING_DIM for chunk in saved)
