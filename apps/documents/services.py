"""Core services for ingestion, chunking, and embedding."""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from datetime import date
from typing import Iterable, Sequence

from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.utils.text import slugify

from apps.documents.embedding_backends import EmbeddingBackendError, get_embedding_backend
from apps.documents.models import (
    Author,
    Authorship,
    Embedding,
    Paper,
    PaperTopic,
    SecurityLevel,
    Topic,
)

logger = logging.getLogger(__name__)


class IngestionError(Exception):
    """Raised when ingestion input or persistence fails."""


class ChunkingError(Exception):
    """Raised when chunk generation or persistence fails."""


class EmbeddingError(Exception):
    """Raised when embedding generation or persistence fails."""


@dataclass(frozen=True)
class IngestInput:
    title: str
    abstract: str
    external_id: str
    published_date: date | None = None
    doi: str | None = None
    security_level: str = SecurityLevel.PUBLIC
    authors: tuple[str, ...] = ()
    topics: tuple[str, ...] = ()


class DocumentIngestionService:
    """Backwards-compatible service name for creating paper-centric records."""

    def ingest(self, items: Iterable[IngestInput]) -> list[Paper]:
        created: list[Paper] = []

        for item in items:
            if not item.title.strip():
                raise IngestionError("Paper title cannot be empty.")
            if not item.external_id.strip():
                raise IngestionError("Paper external_id cannot be empty.")

            security_level = self._validate_security_level(item.security_level)
            try:
                with transaction.atomic():
                    paper = Paper.objects.create(
                        title=item.title.strip(),
                        abstract=item.abstract.strip(),
                        published_date=item.published_date,
                        doi=(item.doi or "").strip() or None,
                        external_id=item.external_id.strip(),
                        security_level=security_level,
                    )
                    self._attach_authors(paper, item.authors)
                    self._attach_topics(paper, item.topics)
            except IntegrityError as exc:
                logger.exception(
                    "Unique constraint violation for paper external_id=%s",
                    item.external_id,
                )
                raise IngestionError(
                    "Duplicate external_id or DOI encountered during ingestion."
                ) from exc
            except DatabaseError as exc:
                logger.exception("Database write failed for paper external_id=%s", item.external_id)
                raise IngestionError("Database write failed while ingesting papers.") from exc

            created.append(paper)

        return created

    @staticmethod
    def _validate_security_level(value: str) -> str:
        if value in SecurityLevel.values:
            return value
        raise IngestionError(
            f"Invalid security_level: {value!r}. Allowed: {list(SecurityLevel.values)}"
        )

    def _attach_authors(self, paper: Paper, author_names: tuple[str, ...]) -> None:
        for order, raw_name in enumerate(author_names, start=1):
            name = raw_name.strip()
            if not name:
                raise IngestionError("Author names cannot be empty.")

            author_external_id = self._derived_external_id(prefix="author", value=name)
            author, _ = Author.objects.get_or_create(
                external_id=author_external_id,
                defaults={"name": name, "institution_name": "unknown"},
            )
            Authorship.objects.create(author=author, paper=paper, author_order=order)

    def _attach_topics(self, paper: Paper, topic_names: tuple[str, ...]) -> None:
        for raw_name in topic_names:
            name = raw_name.strip()
            if not name:
                raise IngestionError("Topic names cannot be empty.")

            topic_external_id = self._derived_external_id(prefix="topic", value=name)
            topic, _ = Topic.objects.get_or_create(
                external_id=topic_external_id,
                defaults={"name": name},
            )
            PaperTopic.objects.get_or_create(paper=paper, topic=topic)

    @staticmethod
    def _derived_external_id(*, prefix: str, value: str) -> str:
        slug = slugify(value)[:40] or "item"
        digest = hashlib.sha1(value.encode("utf-8")).hexdigest()[:8]
        return f"{prefix}:{slug}:{digest}"


def chunk_text(text: str, *, chunk_size: int, overlap: int) -> list[str]:
    """Split text into deterministic overlapping token chunks."""

    if chunk_size <= 0:
        raise ChunkingError("chunk_size must be greater than zero.")
    if overlap < 0:
        raise ChunkingError("overlap must be zero or greater.")
    if overlap >= chunk_size:
        raise ChunkingError("overlap must be smaller than chunk_size.")

    tokens = text.split()
    if not tokens:
        return []

    step = chunk_size - overlap
    chunks: list[str] = []
    for start in range(0, len(tokens), step):
        end = min(start + chunk_size, len(tokens))
        chunk = " ".join(tokens[start:end]).strip()
        if chunk:
            chunks.append(chunk)
        if end == len(tokens):
            break

    return chunks


class PaperChunkingService:
    def __init__(self, *, chunk_size: int | None = None, overlap: int | None = None) -> None:
        self._chunk_size = chunk_size if chunk_size is not None else settings.CHUNK_SIZE
        self._overlap = overlap if overlap is not None else settings.CHUNK_OVERLAP

        if self._chunk_size <= 0:
            raise ChunkingError("chunk_size must be greater than zero.")
        if self._overlap < 0:
            raise ChunkingError("overlap must be zero or greater.")
        if self._overlap >= self._chunk_size:
            raise ChunkingError("overlap must be smaller than chunk_size.")

    def chunk_papers(self, paper_ids: Sequence[int]) -> dict[str, int]:
        unique_ids = sorted({int(paper_id) for paper_id in paper_ids})
        if not unique_ids:
            return {
                "papers_processed": 0,
                "chunks_generated": 0,
                "chunks_created": 0,
                "chunks_updated": 0,
                "chunks_deleted": 0,
            }

        papers = list(Paper.objects.filter(id__in=unique_ids).order_by("id"))

        totals = {
            "papers_processed": 0,
            "chunks_generated": 0,
            "chunks_created": 0,
            "chunks_updated": 0,
            "chunks_deleted": 0,
        }

        for paper in papers:
            text = self._paper_to_text(paper)
            chunks = chunk_text(
                text,
                chunk_size=self._chunk_size,
                overlap=self._overlap,
            )

            try:
                stats = self._upsert_chunks_for_paper(paper=paper, chunks=chunks)
            except DatabaseError as exc:
                logger.exception("Chunk persistence failed for paper id=%s", paper.id)
                raise ChunkingError("Database write failed during chunk persistence.") from exc

            totals["papers_processed"] += 1
            totals["chunks_generated"] += len(chunks)
            totals["chunks_created"] += stats["chunks_created"]
            totals["chunks_updated"] += stats["chunks_updated"]
            totals["chunks_deleted"] += stats["chunks_deleted"]

        return totals

    @staticmethod
    def _paper_to_text(paper: Paper) -> str:
        title = (paper.title or "").strip()
        abstract = (paper.abstract or "").strip()
        if title and abstract:
            return f"{title}\n\n{abstract}"
        return title or abstract

    @staticmethod
    def _upsert_chunks_for_paper(*, paper: Paper, chunks: list[str]) -> dict[str, int]:
        existing = {
            row.chunk_id: row
            for row in Embedding.objects.filter(paper=paper).order_by("chunk_id")
        }

        created = 0
        updated = 0
        keep_chunk_ids: set[int] = set()

        for chunk_id, text_chunk in enumerate(chunks):
            keep_chunk_ids.add(chunk_id)
            row = existing.get(chunk_id)
            if row is None:
                Embedding.objects.create(
                    paper=paper,
                    chunk_id=chunk_id,
                    text_chunk=text_chunk,
                    embedding=None,
                )
                created += 1
                continue

            update_fields: list[str] = []
            if row.text_chunk != text_chunk:
                row.text_chunk = text_chunk
                update_fields.append("text_chunk")
                if row.embedding is not None:
                    row.embedding = None
                    update_fields.append("embedding")

            if update_fields:
                row.save(update_fields=update_fields)
                updated += 1

        stale_chunk_ids = [chunk_id for chunk_id in existing if chunk_id not in keep_chunk_ids]
        deleted = 0
        if stale_chunk_ids:
            deleted, _ = Embedding.objects.filter(
                paper=paper,
                chunk_id__in=stale_chunk_ids,
            ).delete()

        return {
            "chunks_created": created,
            "chunks_updated": updated,
            "chunks_deleted": deleted,
        }


class EmbeddingService:
    def embed_pending_chunks(
        self,
        *,
        paper_ids: Sequence[int] | None = None,
        batch_size: int = 128,
        backend_name: str | None = None,
    ) -> int:
        if batch_size <= 0:
            raise EmbeddingError("batch_size must be greater than zero.")

        backend = self._resolve_backend(backend_name=backend_name)

        queryset = Embedding.objects.filter(embedding__isnull=True).order_by("id")
        if paper_ids is not None:
            unique_ids = sorted({int(paper_id) for paper_id in paper_ids})
            if not unique_ids:
                return 0
            queryset = queryset.filter(paper_id__in=unique_ids)

        pending = list(queryset.only("id", "text_chunk", "embedding"))
        if not pending:
            return 0

        embedded_total = 0
        for start in range(0, len(pending), batch_size):
            rows = pending[start : start + batch_size]
            texts = [row.text_chunk for row in rows]

            try:
                vectors = backend.embed_texts(texts)
            except EmbeddingBackendError as exc:
                raise EmbeddingError(str(exc)) from exc

            if len(vectors) != len(rows):
                raise EmbeddingError(
                    "Embedding backend returned mismatched vector count "
                    f"(got {len(vectors)}, expected {len(rows)})."
                )

            for row, vector in zip(rows, vectors):
                row.embedding = self._normalize_vector(vector)

            try:
                Embedding.objects.bulk_update(rows, ["embedding"])
            except DatabaseError as exc:
                logger.exception("Failed to persist embeddings for chunk batch")
                raise EmbeddingError("Database write failed while saving embeddings.") from exc

            embedded_total += len(rows)

        return embedded_total

    def embed_pending(self, *, limit: int | None = None) -> int:
        queryset = Paper.objects.order_by("id")
        if limit is not None:
            if limit <= 0:
                raise EmbeddingError("limit must be greater than zero.")
            queryset = queryset[:limit]

        paper_ids = list(queryset.values_list("id", flat=True))
        if not paper_ids:
            return 0

        chunking = PaperChunkingService()
        chunking.chunk_papers(paper_ids)
        return self.embed_pending_chunks(paper_ids=paper_ids, batch_size=128, backend_name=None)

    @staticmethod
    def _resolve_backend(*, backend_name: str | None):
        selected_backend = backend_name or settings.EMBEDDING_BACKEND
        return get_embedding_backend(
            backend_name=selected_backend,
            embedding_dim=settings.EMBEDDING_DIM,
            local_model_name=settings.LOCAL_EMBEDDING_MODEL,
            openai_api_key=settings.OPENAI_API_KEY,
            openai_model_name=settings.OPENAI_EMBEDDING_MODEL,
            allow_hash_fallback=settings.ALLOW_DETERMINISTIC_EMBEDDING_FALLBACK,
        )

    @staticmethod
    def _normalize_vector(vector: Sequence[float]) -> list[float]:
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise EmbeddingError("Embedding backend returned non-numeric vector values.") from exc

        expected = settings.EMBEDDING_DIM
        if len(values) == expected:
            return values
        if len(values) > expected:
            return values[:expected]
        return values + [0.0] * (expected - len(values))
