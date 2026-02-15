from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date
from statistics import mean

from django.conf import settings
from django.db import DatabaseError
from django.db.models import Prefetch, QuerySet
from django.utils import timezone
from pgvector.django import CosineDistance

from apps.api.query_optimizer import optimize_query
from apps.documents.embedding_backends import EmbeddingBackendError, get_embedding_backend
from apps.documents.models import (
    Authorship,
    Embedding,
    Paper,
    PaperTopic,
    SearchAudit,
    SecurityLevel,
)

logger = logging.getLogger(__name__)

_TELECOM_TERMS = {
    "5g",
    "6g",
    "ran",
    "o-ran",
    "ric",
    "xapp",
    "network",
    "networks",
    "slicing",
    "orchestration",
    "core",
    "telecom",
    "telecommunications",
    "wireless",
    "radio",
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


class ExpertRankingError(Exception):
    """Raised when expert ranking cannot be computed."""


class ExpertRankingBackendError(ExpertRankingError):
    """Raised when query embedding backend fails."""


@dataclass(frozen=True)
class PaperMatch:
    paper_id: int
    distance: float


@dataclass(frozen=True)
class ExpertPaperSummary:
    paper_id: int
    title: str
    published_date: date | None
    semantic_score: float


@dataclass
class ExpertAccumulator:
    author_id: int
    name: str
    institution: str
    papers: list[ExpertPaperSummary] = field(default_factory=list)
    topic_counts: dict[str, int] = field(default_factory=dict)
    centrality_score: float | None = None


class ExpertRankingService:
    def __init__(
        self,
        *,
        top_experts: int | None = None,
        top_papers: int | None = None,
        top_topics: int | None = None,
        max_chunk_scan: int | None = None,
        topic_target: int | None = None,
        graph_centrality_enabled: bool | None = None,
    ) -> None:
        self._top_experts = top_experts if top_experts is not None else settings.EXPERTS_TOP_EXPERTS
        self._top_papers = top_papers if top_papers is not None else settings.EXPERTS_TOP_PAPERS
        self._top_topics = top_topics if top_topics is not None else settings.EXPERTS_TOP_TOPICS
        self._max_chunk_scan = (
            max_chunk_scan if max_chunk_scan is not None else settings.EXPERTS_MAX_CHUNK_SCAN
        )
        self._topic_target = (
            topic_target if topic_target is not None else settings.EXPERTS_TOPIC_DIVERSITY_TARGET
        )
        self._graph_centrality_enabled = (
            graph_centrality_enabled
            if graph_centrality_enabled is not None
            else settings.EXPERTS_ENABLE_GRAPH_CENTRALITY
        )

        if self._top_experts <= 0:
            raise ExpertRankingError("EXPERTS_TOP_EXPERTS must be greater than 0.")
        if self._top_papers <= 0:
            raise ExpertRankingError("EXPERTS_TOP_PAPERS must be greater than 0.")
        if self._top_topics <= 0:
            raise ExpertRankingError("EXPERTS_TOP_TOPICS must be greater than 0.")
        if self._max_chunk_scan <= 0:
            raise ExpertRankingError("EXPERTS_MAX_CHUNK_SCAN must be greater than 0.")
        if self._topic_target <= 0:
            raise ExpertRankingError("EXPERTS_TOPIC_DIVERSITY_TARGET must be greater than 0.")
        self._max_ranked_experts = min(10, self._top_experts)
        self._min_ranked_score = 0.22

    def rank(
        self,
        *,
        query: str,
        clearance: str,
        endpoint: str,
        client_id: str | None,
        user_role: str | None = None,
        audit: bool = True,
    ) -> dict[str, object]:
        query_text = query.strip()
        if not query_text:
            raise ExpertRankingError("query cannot be empty.")
        if clearance not in SecurityLevel.values:
            raise ExpertRankingError(
                f"Invalid clearance: {clearance!r}. Allowed: {list(SecurityLevel.values)}"
            )

        optimized = optimize_query(query_text)
        retrieval_query = optimized.optimized_query or optimized.normalized_query or query_text
        query_vector = self._embed_query(retrieval_query)
        allowed_levels = _CLEARANCE_ALLOWED_LEVELS[clearance]

        paper_matches = self._collect_best_paper_matches(
            query_vector=query_vector,
            allowed_levels=allowed_levels,
        )
        if not paper_matches:
            if audit:
                self._save_audit(
                    endpoint=endpoint,
                    query=query_text,
                    clearance=clearance,
                    user_role=(user_role or clearance),
                    redacted_count=0,
                    client_id=client_id,
                )
            return {
                "query": query_text,
                "clearance": clearance,
                "experts": [],
            }

        query_terms = set(optimized.expanded_terms) if optimized.expanded_terms else self._tokenize(query_text)
        experts = self._build_expert_rows(
            paper_matches=paper_matches,
            query_terms=query_terms,
        )
        experts.sort(key=lambda row: row["_score"], reverse=True)
        experts = self._trim_low_relevance_rows(experts)
        ranked_limit = min(self._max_ranked_experts, len(experts))
        ranked = experts[:ranked_limit]

        for row in ranked:
            row.pop("_score", None)

        if audit:
            self._save_audit(
                endpoint=endpoint,
                query=query_text,
                clearance=clearance,
                user_role=(user_role or clearance),
                redacted_count=0,
                client_id=client_id,
            )

        return {
            "query": query_text,
            "optimized_query": retrieval_query,
            "clearance": clearance,
            "experts": ranked,
        }

    def _embed_query(self, query: str) -> list[float]:
        primary_backend = settings.EMBEDDING_BACKEND
        try:
            return self._embed_query_with_backend(query=query, backend_name=primary_backend)
        except EmbeddingBackendError as exc:
            if not self._should_try_local_fallback(primary_backend):
                raise ExpertRankingBackendError(str(exc)) from exc

            logger.warning(
                "Primary embedding backend failed for experts; retrying with local fallback."
            )
            try:
                return self._embed_query_with_backend(query=query, backend_name="local")
            except EmbeddingBackendError as fallback_exc:
                raise ExpertRankingBackendError(
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
            normalized = [float(value) for value in vectors[0]]
        except (TypeError, ValueError) as exc:
            raise EmbeddingBackendError(
                "Embedding backend returned non-numeric query vector values."
            ) from exc

        expected = settings.EMBEDDING_DIM
        if len(normalized) == expected:
            return normalized
        if len(normalized) > expected:
            return normalized[:expected]
        return normalized + [0.0] * (expected - len(normalized))

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

    def _collect_best_paper_matches(
        self,
        *,
        query_vector: list[float],
        allowed_levels: tuple[str, ...],
    ) -> dict[int, PaperMatch]:
        matches_by_paper: dict[int, PaperMatch] = {}

        queryset: QuerySet[Embedding] = (
            Embedding.objects.filter(
                embedding__isnull=False,
                paper__security_level__in=allowed_levels,
            )
            .annotate(distance=CosineDistance("embedding", query_vector))
            .only("id", "paper_id")
            .order_by("distance", "id")
        )

        scanned = 0
        for row in queryset.iterator(chunk_size=200):
            scanned += 1
            if scanned > self._max_chunk_scan:
                break

            distance = float(row.distance)
            existing = matches_by_paper.get(row.paper_id)
            if existing is None or distance < existing.distance:
                matches_by_paper[row.paper_id] = PaperMatch(
                    paper_id=row.paper_id,
                    distance=distance,
                )

        return matches_by_paper

    def _build_expert_rows(
        self,
        *,
        paper_matches: dict[int, PaperMatch],
        query_terms: set[str],
    ) -> list[dict[str, object]]:
        paper_ids = sorted(paper_matches)

        authorships_qs = Authorship.objects.select_related("author").order_by("author_order", "id")
        paper_topics_qs = PaperTopic.objects.select_related("topic").order_by("topic__name", "id")

        papers = list(
            Paper.objects.filter(id__in=paper_ids)
            .only("id", "title", "published_date")
            .prefetch_related(
                Prefetch("authorships", queryset=authorships_qs),
                Prefetch("paper_topics", queryset=paper_topics_qs),
            )
        )

        accumulators: dict[int, ExpertAccumulator] = {}

        for paper in papers:
            match = paper_matches.get(paper.id)
            if match is None:
                continue

            semantic_score = self._semantic_score(match.distance)
            topic_names = [paper_topic.topic.name for paper_topic in paper.paper_topics.all()]
            authorships = list(paper.authorships.all())

            for authorship in authorships:
                author = authorship.author
                accumulator = accumulators.get(author.id)
                if accumulator is None:
                    accumulator = ExpertAccumulator(
                        author_id=author.id,
                        name=author.name,
                        institution=author.institution_name,
                    )
                    accumulators[author.id] = accumulator
                if author.centrality_score is not None:
                    accumulator.centrality_score = float(author.centrality_score)

                accumulator.papers.append(
                    ExpertPaperSummary(
                        paper_id=paper.id,
                        title=paper.title,
                        published_date=paper.published_date,
                        semantic_score=semantic_score,
                    )
                )
                for topic_name in topic_names:
                    accumulator.topic_counts[topic_name] = (
                        accumulator.topic_counts.get(topic_name, 0) + 1
                    )

        max_stored_centrality = max(
            (
                accumulator.centrality_score
                for accumulator in accumulators.values()
                if accumulator.centrality_score is not None
            ),
            default=0,
        )

        rows: list[dict[str, object]] = []
        for accumulator in accumulators.values():
            rows.append(
                self._build_expert_payload(
                    accumulator=accumulator,
                    max_stored_centrality=float(max_stored_centrality),
                    query_terms=query_terms,
                )
            )

        return rows

    def _build_expert_payload(
        self,
        *,
        accumulator: ExpertAccumulator,
        max_stored_centrality: float,
        query_terms: set[str],
    ) -> dict[str, object]:
        paper_ranked = sorted(
            accumulator.papers,
            key=lambda paper: (
                paper.semantic_score,
                paper.published_date or date.min,
                paper.paper_id,
            ),
            reverse=True,
        )
        top_papers = paper_ranked[: self._top_papers]

        semantic_relevance = self._semantic_relevance(top_papers)
        recency_boost = self._recency_boost(top_papers)
        topic_coverage = self._topic_coverage(accumulator.topic_counts)
        query_alignment = self._query_alignment(
            query_terms=query_terms,
            top_papers=top_papers,
            topic_counts=accumulator.topic_counts,
        )
        graph_centrality = self._graph_centrality(
            accumulator=accumulator,
            max_stored_centrality=max_stored_centrality,
        )
        graph_proximity = max(
            0.0,
            min(1.0, (0.65 * query_alignment) + (0.35 * topic_coverage)),
        )
        citation_authority = graph_centrality

        total_score = self._total_score(
            semantic_relevance=semantic_relevance,
            recency_boost=recency_boost,
            graph_proximity=graph_proximity,
            citation_authority=citation_authority,
        )

        top_topics = [
            name
            for name, _count in sorted(
                accumulator.topic_counts.items(),
                key=lambda item: (-item[1], item[0]),
            )[: self._top_topics]
        ]

        top_paper_payload = [
            {
                "title": paper.title,
                "published_date": (
                    paper.published_date.isoformat() if paper.published_date is not None else None
                ),
            }
            for paper in top_papers
        ]

        return {
            "author_id": accumulator.author_id,
            "name": accumulator.name,
            "institution": accumulator.institution,
            "top_topics": top_topics,
            "top_papers": top_paper_payload,
            "score_breakdown": {
                "semantic_relevance": round(semantic_relevance, 4),
                "recency_boost": round(recency_boost, 4),
                "topic_coverage": round(topic_coverage, 4),
                "query_alignment": round(query_alignment, 4),
                "graph_proximity": round(graph_proximity, 4),
                "citation_authority": round(citation_authority, 4),
                "graph_centrality": round(graph_centrality, 4),
            },
            "matched_paper_count": len(accumulator.papers),
            "why_ranked": self._why_ranked(
                top_papers=top_papers,
                top_topics=top_topics,
                semantic_relevance=semantic_relevance,
                recency_boost=recency_boost,
                query_alignment=query_alignment,
                graph_proximity=graph_proximity,
                citation_authority=citation_authority,
                graph_centrality=graph_centrality,
                matched_paper_count=len(accumulator.papers),
            ),
            "_score": round(total_score, 6),
        }

    @staticmethod
    def _semantic_score(distance: float) -> float:
        bounded = max(0.0, float(distance))
        return 1.0 / (1.0 + bounded)

    @staticmethod
    def _semantic_relevance(papers: list[ExpertPaperSummary]) -> float:
        if not papers:
            return 0.0
        return float(mean(paper.semantic_score for paper in papers))

    def _recency_boost(self, papers: list[ExpertPaperSummary]) -> float:
        if not papers:
            return 0.0

        weighted_numerator = 0.0
        weighted_denominator = 0.0
        for paper in papers:
            recency = self._paper_recency_score(paper.published_date)
            weighted_numerator += recency * paper.semantic_score
            weighted_denominator += paper.semantic_score

        if weighted_denominator <= 0:
            return 0.0
        return weighted_numerator / weighted_denominator

    def _topic_coverage(self, topic_counts: dict[str, int]) -> float:
        if not topic_counts:
            return 0.0
        unique_topics = len(topic_counts)
        return min(1.0, unique_topics / float(self._topic_target))

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-zA-Z0-9]+", text.lower()) if len(token) >= 3}

    def _query_alignment(
        self,
        *,
        query_terms: set[str],
        top_papers: list[ExpertPaperSummary],
        topic_counts: dict[str, int],
    ) -> float:
        if not query_terms:
            return 0.0

        corpus_terms: set[str] = set()
        for paper in top_papers:
            corpus_terms |= self._tokenize(paper.title)
        for topic_name in topic_counts:
            corpus_terms |= self._tokenize(topic_name)

        if not corpus_terms:
            return 0.0

        base_overlap = len(query_terms & corpus_terms) / float(len(query_terms))
        telecom_query_terms = {term for term in query_terms if term in _TELECOM_TERMS}
        if telecom_query_terms:
            telecom_overlap = len(telecom_query_terms & corpus_terms) / float(
                len(telecom_query_terms)
            )
            return max(0.0, min(1.0, (0.75 * base_overlap) + (0.25 * telecom_overlap)))

        return max(0.0, min(1.0, base_overlap))

    def _graph_centrality(
        self,
        *,
        accumulator: ExpertAccumulator,
        max_stored_centrality: float,
    ) -> float:
        if not self._graph_centrality_enabled:
            return 0.0
        if accumulator.centrality_score is None:
            return 0.0
        if max_stored_centrality <= 0:
            return 0.0
        normalized = accumulator.centrality_score / max_stored_centrality
        return max(0.0, min(1.0, normalized))

    def _total_score(
        self,
        *,
        semantic_relevance: float,
        recency_boost: float,
        graph_proximity: float,
        citation_authority: float,
    ) -> float:
        return (
            (0.58 * semantic_relevance)
            + (0.24 * graph_proximity)
            + (0.12 * citation_authority)
            + (0.06 * recency_boost)
        )

    def _paper_recency_score(self, published_date: date | None) -> float:
        if published_date is None:
            return 0.0

        today = timezone.now().date()
        age_days = max(0, (today - published_date).days)
        five_years_days = 365 * 5
        return max(0.0, 1.0 - (age_days / float(five_years_days)))

    @staticmethod
    def _why_ranked(
        *,
        top_papers: list[ExpertPaperSummary],
        top_topics: list[str],
        semantic_relevance: float,
        recency_boost: float,
        query_alignment: float,
        graph_proximity: float,
        citation_authority: float,
        graph_centrality: float,
        matched_paper_count: int,
    ) -> str:
        if not top_papers:
            return "Ranked due to broad author relevance across matched papers."

        lead_paper = top_papers[0].title
        semantic_label = (
            "high semantic relevance"
            if semantic_relevance >= 0.75
            else "solid semantic relevance"
        )
        recency_label = (
            "recent publications" if recency_boost >= 0.50 else "historical publications"
        )
        alignment_label = (
            "strong query alignment" if query_alignment >= 0.50 else "moderate query alignment"
        )

        if top_topics:
            return (
                f"Ranked for {semantic_label} via '{lead_paper}', "
                f"{alignment_label}, {recency_label}, "
                f"graph proximity {graph_proximity:.2f}, "
                f"citation authority {citation_authority:.2f}, "
                f"{matched_paper_count} matched papers, and coverage of topics like "
                f"{', '.join(top_topics[:2])}."
            )

        return (
            f"Ranked for {semantic_label} via '{lead_paper}', {alignment_label}, "
            f"{recency_label}, graph proximity {graph_proximity:.2f}, "
            f"citation authority {citation_authority:.2f}, and {matched_paper_count} matched papers."
        )

    def _trim_low_relevance_rows(self, rows: list[dict[str, object]]) -> list[dict[str, object]]:
        if not rows:
            return []
        filtered = [
            row for row in rows if float(row.get("_score", 0.0) or 0.0) >= self._min_ranked_score
        ]
        if filtered:
            return filtered
        return rows[: min(len(rows), self._max_ranked_experts)]

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
            logger.exception("Failed to persist SearchAudit row for experts endpoint.")
