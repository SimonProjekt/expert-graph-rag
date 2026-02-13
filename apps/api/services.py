from __future__ import annotations

import logging
from dataclasses import dataclass

from django.conf import settings
from django.db import DatabaseError
from django.db.models import Prefetch, QuerySet
from pgvector.django import CosineDistance

from apps.documents.embedding_backends import EmbeddingBackendError, get_embedding_backend
from apps.documents.models import (
    Authorship,
    Embedding,
    Paper,
    PaperTopic,
    SearchAudit,
    SecurityLevel,
)
from apps.documents.openalex import OpenAlexReadThroughResult, OpenAlexReadThroughService

logger = logging.getLogger(__name__)

_SECURITY_RANK = {
    SecurityLevel.PUBLIC: 0,
    SecurityLevel.INTERNAL: 1,
    SecurityLevel.CONFIDENTIAL: 2,
}


class SearchExecutionError(Exception):
    """Raised when search execution fails due to invalid input or storage issues."""


class SearchBackendError(SearchExecutionError):
    """Raised when embedding backend setup or inference fails."""


@dataclass(frozen=True)
class RankedPaperHit:
    paper_id: int
    best_distance: float
    best_chunk_id: int


class SearchService:
    def __init__(
        self,
        *,
        page_size: int | None = None,
        scan_batch_size: int | None = None,
        max_chunk_scan: int | None = None,
        snippet_max_chars: int | None = None,
    ) -> None:
        self._page_size = page_size if page_size is not None else settings.SEARCH_PAGE_SIZE
        self._scan_batch_size = (
            scan_batch_size if scan_batch_size is not None else settings.SEARCH_SCAN_BATCH_SIZE
        )
        self._max_chunk_scan = (
            max_chunk_scan if max_chunk_scan is not None else settings.SEARCH_MAX_CHUNK_SCAN
        )
        self._snippet_max_chars = (
            snippet_max_chars
            if snippet_max_chars is not None
            else settings.SEARCH_SNIPPET_MAX_CHARS
        )

        if self._page_size <= 0:
            raise SearchExecutionError("page_size must be greater than zero.")
        if self._scan_batch_size <= 0:
            raise SearchExecutionError("scan_batch_size must be greater than zero.")
        if self._max_chunk_scan <= 0:
            raise SearchExecutionError("max_chunk_scan must be greater than zero.")
        if self._snippet_max_chars <= 0:
            raise SearchExecutionError("snippet_max_chars must be greater than zero.")

    def search(
        self,
        *,
        query: str,
        clearance: str,
        page: int,
        endpoint: str,
        client_id: str | None,
        user_role: str | None = None,
    ) -> dict[str, object]:
        query_text = query.strip()
        if not query_text:
            raise SearchExecutionError("query cannot be empty.")
        if clearance not in SecurityLevel.values:
            raise SearchExecutionError(
                f"Invalid clearance: {clearance!r}. Allowed: {list(SecurityLevel.values)}"
            )
        if page <= 0:
            raise SearchExecutionError("page must be greater than zero.")

        query_vector = self._embed_query(query_text)
        target_unique = page * self._page_size
        ranked_hits, redacted_count = self._collect_ranked_hits(
            query_vector=query_vector,
            clearance=clearance,
            target_unique_papers=target_unique,
        )
        live_fetch = self._maybe_read_through_fetch(
            query=query_text,
            page=page,
            current_result_count=len(ranked_hits),
        )
        if live_fetch.should_rerun_search:
            ranked_hits, redacted_count = self._collect_ranked_hits(
                query_vector=query_vector,
                clearance=clearance,
                target_unique_papers=target_unique,
            )

        page_start = (page - 1) * self._page_size
        page_hits = ranked_hits[page_start : page_start + self._page_size]
        paper_ids = [hit.paper_id for hit in page_hits]
        chunk_ids = [hit.best_chunk_id for hit in page_hits]

        paper_lookup = self._load_papers(paper_ids)
        snippet_lookup = self._load_snippets(chunk_ids)
        results: list[dict[str, object]] = []
        for hit in page_hits:
            paper = paper_lookup.get(hit.paper_id)
            if paper is None:
                continue

            authors = [link.author.name for link in paper.authorships.all()]
            topics = [link.topic.name for link in paper.paper_topics.all()]

            results.append(
                {
                    "paper_id": paper.id,
                    "title": paper.title,
                    "published_date": (
                        paper.published_date.isoformat() if paper.published_date else None
                    ),
                    "relevance_score": round(self._semantic_score(hit.best_distance), 4),
                    "snippet": snippet_lookup.get(hit.best_chunk_id, ""),
                    "topics": topics,
                    "authors": authors,
                }
            )

        self._save_audit(
            endpoint=endpoint,
            query=query_text,
            clearance=clearance,
            user_role=(user_role or clearance),
            redacted_count=redacted_count,
            client_id=client_id,
        )

        return {
            "query": query_text,
            "clearance": clearance,
            "page": page,
            "page_size": self._page_size,
            "redacted_count": redacted_count,
            "live_fetch": live_fetch.to_payload(),
            "results": results,
        }

    def _maybe_read_through_fetch(
        self,
        *,
        query: str,
        page: int,
        current_result_count: int,
    ) -> OpenAlexReadThroughResult:
        live_enabled = bool(getattr(settings, "OPENALEX_LIVE_FETCH", True))
        if not live_enabled:
            return OpenAlexReadThroughResult(
                enabled=False,
                attempted=False,
                reason="disabled",
            )

        try:
            return OpenAlexReadThroughService().fetch_if_needed(
                query=query,
                current_result_count=current_result_count,
                page=page,
            )
        except (DatabaseError, ValueError) as exc:
            logger.warning("OpenAlex read-through fetch skipped due to error: %s", exc)
            return OpenAlexReadThroughResult(
                enabled=live_enabled,
                attempted=False,
                reason="error",
                error=str(exc),
            )

    def _embed_query(self, query: str) -> list[float]:
        try:
            backend = get_embedding_backend(
                backend_name=settings.EMBEDDING_BACKEND,
                embedding_dim=settings.EMBEDDING_DIM,
                local_model_name=settings.LOCAL_EMBEDDING_MODEL,
                openai_api_key=settings.OPENAI_API_KEY,
                openai_model_name=settings.OPENAI_EMBEDDING_MODEL,
                allow_hash_fallback=settings.ALLOW_DETERMINISTIC_EMBEDDING_FALLBACK,
            )
            vectors = backend.embed_texts([query])
        except EmbeddingBackendError as exc:
            raise SearchBackendError(str(exc)) from exc

        if not vectors:
            raise SearchBackendError("Embedding backend returned no vectors.")
        return self._normalize_vector(vectors[0])

    def _collect_ranked_hits(
        self,
        *,
        query_vector: list[float],
        clearance: str,
        target_unique_papers: int,
    ) -> tuple[list[RankedPaperHit], int]:
        allowed_by_paper: dict[int, RankedPaperHit] = {}
        redacted_count = 0

        scanned = 0
        offset = 0

        queryset: QuerySet[Embedding] = (
            Embedding.objects.filter(embedding__isnull=False)
            .select_related("paper")
            .only("id", "paper_id", "paper__security_level")
            .annotate(distance=CosineDistance("embedding", query_vector))
            .order_by("distance", "id")
        )

        while len(allowed_by_paper) < target_unique_papers and scanned < self._max_chunk_scan:
            take = min(self._scan_batch_size, self._max_chunk_scan - scanned)
            batch = list(queryset[offset : offset + take])
            if not batch:
                break

            offset += len(batch)
            scanned += len(batch)

            for row in batch:
                paper_rank = _SECURITY_RANK.get(
                    row.paper.security_level,
                    _SECURITY_RANK[SecurityLevel.CONFIDENTIAL],
                )
                if paper_rank > _SECURITY_RANK[clearance]:
                    redacted_count += 1
                    continue

                distance = float(row.distance)
                candidate = RankedPaperHit(
                    paper_id=row.paper_id,
                    best_distance=distance,
                    best_chunk_id=row.id,
                )
                existing = allowed_by_paper.get(row.paper_id)
                if existing is None or candidate.best_distance < existing.best_distance:
                    allowed_by_paper[row.paper_id] = candidate

        ranked_hits = sorted(
            allowed_by_paper.values(),
            key=lambda hit: (hit.best_distance, hit.paper_id),
        )
        return ranked_hits, redacted_count

    def _load_papers(self, paper_ids: list[int]) -> dict[int, Paper]:
        if not paper_ids:
            return {}

        authorships_qs = Authorship.objects.select_related("author").order_by("author_order", "id")
        paper_topics_qs = PaperTopic.objects.select_related("topic").order_by("topic__name", "id")

        papers = (
            Paper.objects.filter(id__in=paper_ids)
            .only("id", "title", "published_date")
            .prefetch_related(
                Prefetch("authorships", queryset=authorships_qs),
                Prefetch("paper_topics", queryset=paper_topics_qs),
            )
        )
        return {paper.id: paper for paper in papers}

    def _load_snippets(self, chunk_ids: list[int]) -> dict[int, str]:
        if not chunk_ids:
            return {}

        text_by_chunk_id = {
            chunk_id: self._build_snippet(text_chunk)
            for chunk_id, text_chunk in Embedding.objects.filter(id__in=chunk_ids).values_list(
                "id", "text_chunk"
            )
        }
        return text_by_chunk_id

    def _save_audit(
        self,
        *,
        endpoint: str,
        query: str,
        clearance: str,
        user_role: str,
        redacted_count: int,
        client_id: str | None,
    ) -> None:
        try:
            SearchAudit.objects.create(
                endpoint=endpoint,
                query=query,
                clearance=clearance,
                user_role=user_role,
                redacted_count=redacted_count,
                client_id=client_id,
            )
        except DatabaseError:
            logger.exception("Failed to persist SearchAudit row.")

    def _build_snippet(self, text: str) -> str:
        normalized = " ".join((text or "").split())
        if len(normalized) <= self._snippet_max_chars:
            return normalized
        if self._snippet_max_chars <= 3:
            return normalized[: self._snippet_max_chars]
        return normalized[: self._snippet_max_chars - 3].rstrip() + "..."

    @staticmethod
    def _semantic_score(distance: float) -> float:
        bounded = max(0.0, float(distance))
        return 1.0 / (1.0 + bounded)

    @staticmethod
    def _normalize_vector(vector: list[float]) -> list[float]:
        try:
            values = [float(value) for value in vector]
        except (TypeError, ValueError) as exc:
            raise SearchBackendError(
                "Embedding backend returned non-numeric query vector values."
            ) from exc

        expected = settings.EMBEDDING_DIM
        if len(values) == expected:
            return values
        if len(values) > expected:
            return values[:expected]
        return values + [0.0] * (expected - len(values))
