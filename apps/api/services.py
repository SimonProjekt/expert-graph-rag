from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from statistics import mean

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
_CLEARANCE_ALLOWED_LEVELS = {
    SecurityLevel.PUBLIC: (SecurityLevel.PUBLIC,),
    SecurityLevel.INTERNAL: (SecurityLevel.PUBLIC, SecurityLevel.INTERNAL),
    SecurityLevel.CONFIDENTIAL: (
        SecurityLevel.PUBLIC,
        SecurityLevel.INTERNAL,
        SecurityLevel.CONFIDENTIAL,
    ),
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
    source: str = "semantic"
    hop_distance: int = 0


@dataclass(frozen=True)
class GraphPathHint:
    hop_distance: int
    via_type: str
    via_label: str
    seed_paper_id: int
    intermediate_paper_id: int | None = None


@dataclass(frozen=True)
class ScoredPaperHit:
    hit: RankedPaperHit
    semantic_relevance: float
    graph_authority: float
    graph_centrality: float
    total_score: float
    why_matched: str
    graph_path: str


class SearchService:
    def __init__(
        self,
        *,
        page_size: int | None = None,
        scan_batch_size: int | None = None,
        max_chunk_scan: int | None = None,
        snippet_max_chars: int | None = None,
        graph_seed_papers: int | None = None,
        graph_expansion_limit: int | None = None,
        graph_hop_limit: int | None = None,
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
        self._graph_seed_papers = (
            graph_seed_papers
            if graph_seed_papers is not None
            else settings.SEARCH_GRAPH_SEED_PAPERS
        )
        self._graph_expansion_limit = (
            graph_expansion_limit
            if graph_expansion_limit is not None
            else settings.SEARCH_GRAPH_EXPANSION_LIMIT
        )
        self._graph_hop_limit = (
            graph_hop_limit if graph_hop_limit is not None else settings.SEARCH_GRAPH_HOP_LIMIT
        )

        if self._page_size <= 0:
            raise SearchExecutionError("page_size must be greater than zero.")
        if self._scan_batch_size <= 0:
            raise SearchExecutionError("scan_batch_size must be greater than zero.")
        if self._max_chunk_scan <= 0:
            raise SearchExecutionError("max_chunk_scan must be greater than zero.")
        if self._snippet_max_chars <= 0:
            raise SearchExecutionError("snippet_max_chars must be greater than zero.")
        if self._graph_seed_papers <= 0:
            raise SearchExecutionError("graph_seed_papers must be greater than zero.")
        if self._graph_expansion_limit < 0:
            raise SearchExecutionError("graph_expansion_limit must be zero or greater.")
        if self._graph_hop_limit not in {1, 2}:
            raise SearchExecutionError("graph_hop_limit must be 1 or 2.")

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

        started = time.monotonic()
        query_vector = self._embed_query(query_text)
        target_unique = page * self._page_size
        semantic_target = max(target_unique, self._graph_seed_papers)
        ranked_hits, redacted_count = self._collect_ranked_hits(
            query_vector=query_vector,
            clearance=clearance,
            target_unique_papers=semantic_target,
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
                target_unique_papers=semantic_target,
            )

        ranked_by_paper = {hit.paper_id: hit for hit in ranked_hits}
        seed_paper_ids = [hit.paper_id for hit in ranked_hits[: self._graph_seed_papers]]

        path_hints = self._expand_graph_paths(
            seed_paper_ids=seed_paper_ids,
            allowed_levels=_CLEARANCE_ALLOWED_LEVELS[clearance],
            limit=self._graph_expansion_limit,
        )
        expanded_ids = [paper_id for paper_id in path_hints if paper_id not in ranked_by_paper]
        expanded_hits = self._load_best_hits_for_papers(
            query_vector=query_vector,
            paper_ids=expanded_ids,
        )

        for paper_id, expanded_hit in expanded_hits.items():
            hint = path_hints.get(paper_id)
            hop_distance = hint.hop_distance if hint is not None else 1
            ranked_by_paper[paper_id] = RankedPaperHit(
                paper_id=paper_id,
                best_distance=expanded_hit.best_distance,
                best_chunk_id=expanded_hit.best_chunk_id,
                source=f"graph_hop_{hop_distance}",
                hop_distance=hop_distance,
            )

        scored_hits = self._score_hits(
            query_text=query_text,
            hits_by_paper=ranked_by_paper,
            path_hints=path_hints,
        )

        page_start = (page - 1) * self._page_size
        page_scored_hits = scored_hits[page_start : page_start + self._page_size]
        paper_ids = [scored.hit.paper_id for scored in page_scored_hits]
        chunk_ids = [scored.hit.best_chunk_id for scored in page_scored_hits]

        paper_lookup = self._load_papers(paper_ids)
        snippet_lookup = self._load_snippets(chunk_ids)
        results: list[dict[str, object]] = []
        for scored_hit in page_scored_hits:
            paper = paper_lookup.get(scored_hit.hit.paper_id)
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
                    "relevance_score": round(scored_hit.total_score, 4),
                    "semantic_relevance_score": round(scored_hit.semantic_relevance, 4),
                    "snippet": snippet_lookup.get(scored_hit.hit.best_chunk_id, ""),
                    "topics": topics,
                    "authors": authors,
                    "source": scored_hit.hit.source,
                    "graph_hop_distance": scored_hit.hit.hop_distance,
                    "score_breakdown": {
                        "semantic_relevance": round(scored_hit.semantic_relevance, 4),
                        "graph_authority": round(scored_hit.graph_authority, 4),
                        "graph_centrality": round(scored_hit.graph_centrality, 4),
                    },
                    "why_matched": scored_hit.why_matched,
                    "graph_path": scored_hit.graph_path,
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
        took_ms = int((time.monotonic() - started) * 1000)

        return {
            "query": query_text,
            "clearance": clearance,
            "page": page,
            "page_size": self._page_size,
            "redacted_count": redacted_count,
            "hidden_count": redacted_count,
            "result_count": len(results),
            "took_ms": took_ms,
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
        primary_backend = settings.EMBEDDING_BACKEND
        try:
            return self._embed_query_with_backend(query=query, backend_name=primary_backend)
        except EmbeddingBackendError as exc:
            if not self._should_try_local_fallback(primary_backend):
                raise SearchBackendError(str(exc)) from exc

            logger.warning(
                "Primary embedding backend failed for search; retrying with local fallback."
            )
            try:
                return self._embed_query_with_backend(query=query, backend_name="local")
            except EmbeddingBackendError as fallback_exc:
                raise SearchBackendError(
                    f"{exc} (local fallback failed: {fallback_exc})"
                ) from fallback_exc

    def _embed_query_with_backend(self, *, query: str, backend_name: str) -> list[float]:
        backend = get_embedding_backend(
            backend_name=backend_name,
            embedding_dim=settings.EMBEDDING_DIM,
            local_model_name=settings.LOCAL_EMBEDDING_MODEL,
            openai_api_key=settings.OPENAI_API_KEY,
            openai_model_name=settings.OPENAI_EMBEDDING_MODEL,
            allow_hash_fallback=settings.ALLOW_DETERMINISTIC_EMBEDDING_FALLBACK,
        )
        vectors = backend.embed_texts([query])
        if not vectors:
            raise EmbeddingBackendError("Embedding backend returned no vectors.")
        try:
            return self._normalize_vector(vectors[0])
        except SearchBackendError as exc:
            raise EmbeddingBackendError(str(exc)) from exc

    @staticmethod
    def _should_try_local_fallback(backend_name: str) -> bool:
        if not settings.ALLOW_DETERMINISTIC_EMBEDDING_FALLBACK:
            return False

        normalized = (backend_name or "auto").strip().lower()
        if normalized == "local":
            return False
        if normalized == "openai":
            return True
        return bool(settings.OPENAI_API_KEY)

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

    def _expand_graph_paths(
        self,
        *,
        seed_paper_ids: list[int],
        allowed_levels: tuple[str, ...],
        limit: int,
    ) -> dict[int, GraphPathHint]:
        if not seed_paper_ids or limit <= 0:
            return {}

        seed_set = set(seed_paper_ids)
        hints: dict[int, GraphPathHint] = {}

        seed_authorships = list(
            Authorship.objects.filter(paper_id__in=seed_set)
            .select_related("author")
            .values_list("paper_id", "author_id", "author__name")
        )
        seed_topics = list(
            PaperTopic.objects.filter(paper_id__in=seed_set)
            .select_related("topic")
            .values_list("paper_id", "topic_id", "topic__name")
        )

        author_seed_info: dict[int, tuple[int, str]] = {}
        for paper_id, author_id, author_name in seed_authorships:
            author_seed_info.setdefault(author_id, (paper_id, author_name or "unknown author"))

        topic_seed_info: dict[int, tuple[int, str]] = {}
        for paper_id, topic_id, topic_name in seed_topics:
            topic_seed_info.setdefault(topic_id, (paper_id, topic_name or "unknown topic"))

        self._expand_hop_one_via_authors(
            hints=hints,
            seed_set=seed_set,
            author_seed_info=author_seed_info,
            allowed_levels=allowed_levels,
            limit=limit,
        )
        if len(hints) >= limit:
            return hints

        self._expand_hop_one_via_topics(
            hints=hints,
            seed_set=seed_set,
            topic_seed_info=topic_seed_info,
            allowed_levels=allowed_levels,
            limit=limit,
        )
        if len(hints) >= limit or self._graph_hop_limit <= 1:
            return hints

        hop_one_paper_ids = [paper_id for paper_id, hint in hints.items() if hint.hop_distance == 1]
        if not hop_one_paper_ids:
            return hints

        self._expand_hop_two_via_authors(
            hints=hints,
            seed_set=seed_set,
            hop_one_paper_ids=hop_one_paper_ids,
            allowed_levels=allowed_levels,
            limit=limit,
        )
        if len(hints) >= limit:
            return hints

        self._expand_hop_two_via_topics(
            hints=hints,
            seed_set=seed_set,
            hop_one_paper_ids=hop_one_paper_ids,
            allowed_levels=allowed_levels,
            limit=limit,
        )
        return hints

    def _expand_hop_one_via_authors(
        self,
        *,
        hints: dict[int, GraphPathHint],
        seed_set: set[int],
        author_seed_info: dict[int, tuple[int, str]],
        allowed_levels: tuple[str, ...],
        limit: int,
    ) -> None:
        if not author_seed_info:
            return

        rows = (
            Authorship.objects.filter(
                author_id__in=list(author_seed_info.keys()),
                paper__security_level__in=allowed_levels,
            )
            .order_by("paper_id", "author_id")
            .values_list("paper_id", "author_id")
        )
        for paper_id, author_id in rows:
            if paper_id in seed_set or paper_id in hints:
                continue
            seed_paper_id, author_name = author_seed_info[author_id]
            hints[paper_id] = GraphPathHint(
                hop_distance=1,
                via_type="author",
                via_label=author_name,
                seed_paper_id=seed_paper_id,
            )
            if len(hints) >= limit:
                return

    def _expand_hop_one_via_topics(
        self,
        *,
        hints: dict[int, GraphPathHint],
        seed_set: set[int],
        topic_seed_info: dict[int, tuple[int, str]],
        allowed_levels: tuple[str, ...],
        limit: int,
    ) -> None:
        if not topic_seed_info:
            return

        rows = (
            PaperTopic.objects.filter(
                topic_id__in=list(topic_seed_info.keys()),
                paper__security_level__in=allowed_levels,
            )
            .order_by("paper_id", "topic_id")
            .values_list("paper_id", "topic_id")
        )
        for paper_id, topic_id in rows:
            if paper_id in seed_set or paper_id in hints:
                continue
            seed_paper_id, topic_name = topic_seed_info[topic_id]
            hints[paper_id] = GraphPathHint(
                hop_distance=1,
                via_type="topic",
                via_label=topic_name,
                seed_paper_id=seed_paper_id,
            )
            if len(hints) >= limit:
                return

    def _expand_hop_two_via_authors(
        self,
        *,
        hints: dict[int, GraphPathHint],
        seed_set: set[int],
        hop_one_paper_ids: list[int],
        allowed_levels: tuple[str, ...],
        limit: int,
    ) -> None:
        hop_one_authorships = list(
            Authorship.objects.filter(paper_id__in=hop_one_paper_ids)
            .select_related("author")
            .values_list("paper_id", "author_id", "author__name")
        )
        if not hop_one_authorships:
            return

        author_hop_one_info: dict[int, tuple[int, str]] = {}
        for paper_id, author_id, author_name in hop_one_authorships:
            author_hop_one_info.setdefault(author_id, (paper_id, author_name or "unknown author"))

        rows = (
            Authorship.objects.filter(
                author_id__in=list(author_hop_one_info.keys()),
                paper__security_level__in=allowed_levels,
            )
            .order_by("paper_id", "author_id")
            .values_list("paper_id", "author_id")
        )
        for paper_id, author_id in rows:
            if paper_id in seed_set or paper_id in hints:
                continue

            intermediate_paper_id, author_name = author_hop_one_info[author_id]
            root_hint = hints.get(intermediate_paper_id)
            seed_paper_id = (
                root_hint.seed_paper_id if root_hint is not None else intermediate_paper_id
            )

            hints[paper_id] = GraphPathHint(
                hop_distance=2,
                via_type="author",
                via_label=author_name,
                seed_paper_id=seed_paper_id,
                intermediate_paper_id=intermediate_paper_id,
            )
            if len(hints) >= limit:
                return

    def _expand_hop_two_via_topics(
        self,
        *,
        hints: dict[int, GraphPathHint],
        seed_set: set[int],
        hop_one_paper_ids: list[int],
        allowed_levels: tuple[str, ...],
        limit: int,
    ) -> None:
        hop_one_topics = list(
            PaperTopic.objects.filter(paper_id__in=hop_one_paper_ids)
            .select_related("topic")
            .values_list("paper_id", "topic_id", "topic__name")
        )
        if not hop_one_topics:
            return

        topic_hop_one_info: dict[int, tuple[int, str]] = {}
        for paper_id, topic_id, topic_name in hop_one_topics:
            topic_hop_one_info.setdefault(topic_id, (paper_id, topic_name or "unknown topic"))

        rows = (
            PaperTopic.objects.filter(
                topic_id__in=list(topic_hop_one_info.keys()),
                paper__security_level__in=allowed_levels,
            )
            .order_by("paper_id", "topic_id")
            .values_list("paper_id", "topic_id")
        )
        for paper_id, topic_id in rows:
            if paper_id in seed_set or paper_id in hints:
                continue

            intermediate_paper_id, topic_name = topic_hop_one_info[topic_id]
            root_hint = hints.get(intermediate_paper_id)
            seed_paper_id = (
                root_hint.seed_paper_id if root_hint is not None else intermediate_paper_id
            )

            hints[paper_id] = GraphPathHint(
                hop_distance=2,
                via_type="topic",
                via_label=topic_name,
                seed_paper_id=seed_paper_id,
                intermediate_paper_id=intermediate_paper_id,
            )
            if len(hints) >= limit:
                return

    def _load_best_hits_for_papers(
        self,
        *,
        query_vector: list[float],
        paper_ids: list[int],
    ) -> dict[int, RankedPaperHit]:
        if not paper_ids:
            return {}

        best_by_paper: dict[int, RankedPaperHit] = {}
        queryset = (
            Embedding.objects.filter(embedding__isnull=False, paper_id__in=paper_ids)
            .annotate(distance=CosineDistance("embedding", query_vector))
            .only("id", "paper_id")
            .order_by("paper_id", "distance", "id")
        )

        for row in queryset.iterator(chunk_size=200):
            if row.paper_id in best_by_paper:
                continue
            best_by_paper[row.paper_id] = RankedPaperHit(
                paper_id=row.paper_id,
                best_distance=float(row.distance),
                best_chunk_id=row.id,
            )
        return best_by_paper

    def _score_hits(
        self,
        *,
        query_text: str,
        hits_by_paper: dict[int, RankedPaperHit],
        path_hints: dict[int, GraphPathHint],
    ) -> list[ScoredPaperHit]:
        if not hits_by_paper:
            return []

        papers = self._load_papers(list(hits_by_paper.keys()))
        if not papers:
            return []

        paper_author_ids: dict[int, set[int]] = {}
        paper_topics_lower: dict[int, set[str]] = {}
        paper_topics_display: dict[int, list[str]] = {}
        paper_avg_centrality: dict[int, float] = {}
        papers_by_author: dict[int, set[int]] = {}
        papers_by_topic: dict[str, set[int]] = {}

        for paper_id, paper in papers.items():
            author_ids: set[int] = set()
            centrality_values: list[float] = []
            for authorship in paper.authorships.all():
                author = authorship.author
                author_ids.add(author.id)
                papers_by_author.setdefault(author.id, set()).add(paper_id)
                if author.centrality_score is not None:
                    centrality_values.append(float(author.centrality_score))

            topic_display = [link.topic.name for link in paper.paper_topics.all()]
            topic_lower = {topic.lower() for topic in topic_display}
            for topic_name in topic_lower:
                papers_by_topic.setdefault(topic_name, set()).add(paper_id)

            paper_author_ids[paper_id] = author_ids
            paper_topics_lower[paper_id] = topic_lower
            paper_topics_display[paper_id] = topic_display
            paper_avg_centrality[paper_id] = (
                float(mean(centrality_values)) if centrality_values else 0.0
            )

        max_authority_raw = 0.0
        max_centrality = max(paper_avg_centrality.values(), default=0.0)
        authority_raw_by_paper: dict[int, float] = {}
        for paper_id, hit in hits_by_paper.items():
            shared_author_links = sum(
                1
                for author_id in paper_author_ids.get(paper_id, set())
                if len(papers_by_author.get(author_id, set()) - {paper_id}) > 0
            )
            shared_topic_links = sum(
                1
                for topic_name in paper_topics_lower.get(paper_id, set())
                if len(papers_by_topic.get(topic_name, set()) - {paper_id}) > 0
            )
            hop_bonus = 0.25
            if hit.hop_distance == 1:
                hop_bonus = 0.18
            elif hit.hop_distance >= 2:
                hop_bonus = 0.10

            raw_authority = (1.2 * shared_author_links) + (1.0 * shared_topic_links) + hop_bonus
            authority_raw_by_paper[paper_id] = raw_authority
            if raw_authority > max_authority_raw:
                max_authority_raw = raw_authority

        query_terms = self._tokenize(query_text)
        scored: list[ScoredPaperHit] = []
        for paper_id, hit in hits_by_paper.items():
            paper = papers.get(paper_id)
            if paper is None:
                continue

            semantic_relevance = self._semantic_score(hit.best_distance)
            raw_authority = authority_raw_by_paper.get(paper_id, 0.0)
            graph_authority = (raw_authority / max_authority_raw) if max_authority_raw > 0 else 0.0
            centrality_raw = paper_avg_centrality.get(paper_id, 0.0)
            graph_centrality = (centrality_raw / max_centrality) if max_centrality > 0 else 0.0

            total_score = (
                (0.60 * semantic_relevance)
                + (0.25 * graph_authority)
                + (0.15 * graph_centrality)
            )

            hint = path_hints.get(paper_id)
            graph_path = self._graph_path_for_paper(paper_id=paper_id, hint=hint)
            keywords = self._matched_keywords(
                query_terms=query_terms,
                title=paper.title,
                topics=paper_topics_display.get(paper_id, []),
            )
            why_matched = self._why_matched(
                semantic_relevance=semantic_relevance,
                graph_authority=graph_authority,
                graph_centrality=graph_centrality,
                source=hit.source,
                keywords=keywords,
                hint=hint,
            )

            scored.append(
                ScoredPaperHit(
                    hit=hit,
                    semantic_relevance=semantic_relevance,
                    graph_authority=graph_authority,
                    graph_centrality=graph_centrality,
                    total_score=total_score,
                    why_matched=why_matched,
                    graph_path=graph_path,
                )
            )

        scored.sort(
            key=lambda item: (
                item.total_score,
                item.semantic_relevance,
                -item.hit.hop_distance,
                -item.hit.best_distance,
                -item.hit.paper_id,
            ),
            reverse=True,
        )
        return scored

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
    def _tokenize(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-zA-Z0-9]+", text.lower()) if len(token) >= 3}

    @staticmethod
    def _matched_keywords(
        *,
        query_terms: set[str],
        title: str,
        topics: list[str],
    ) -> list[str]:
        if not query_terms:
            return []
        corpus_terms = SearchService._tokenize(title)
        for topic in topics:
            corpus_terms |= SearchService._tokenize(topic)
        matched = sorted(query_terms & corpus_terms)
        return matched[:3]

    @staticmethod
    def _graph_path_for_paper(*, paper_id: int, hint: GraphPathHint | None) -> str:
        if hint is None:
            return f"query -> paper:{paper_id}"
        if hint.hop_distance <= 1:
            return (
                f"query -> seed_paper:{hint.seed_paper_id} -> "
                f"{hint.via_type}:{hint.via_label} -> paper:{paper_id}"
            )
        intermediate = hint.intermediate_paper_id or "unknown"
        return (
            f"query -> seed_paper:{hint.seed_paper_id} -> paper:{intermediate} -> "
            f"{hint.via_type}:{hint.via_label} -> paper:{paper_id}"
        )

    @staticmethod
    def _why_matched(
        *,
        semantic_relevance: float,
        graph_authority: float,
        graph_centrality: float,
        source: str,
        keywords: list[str],
        hint: GraphPathHint | None,
    ) -> str:
        parts = [
            f"semantic={semantic_relevance:.2f}",
            f"graph_authority={graph_authority:.2f}",
            f"centrality={graph_centrality:.2f}",
        ]
        if keywords:
            parts.append(f"keywords={', '.join(keywords)}")
        if hint is not None:
            parts.append(
                f"{hint.hop_distance}-hop via {hint.via_type} '{hint.via_label}' "
                f"from seed paper {hint.seed_paper_id}"
            )
        else:
            parts.append("direct semantic match")
        parts.append(f"source={source}")
        return "Ranked because " + "; ".join(parts) + "."

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
