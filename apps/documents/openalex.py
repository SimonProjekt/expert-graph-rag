"""OpenAlex ingestion and live read-through services."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

from django.conf import settings
from django.db import DatabaseError, IntegrityError, transaction
from django.utils import timezone

from apps.documents.models import (
    Author,
    Authorship,
    Embedding,
    IngestionRun,
    IngestionStatus,
    Paper,
    PaperTopic,
    SecurityLevel,
    Topic,
)
from apps.documents.openalex_client import (
    OpenAlexClient,
    OpenAlexClientError,
    OpenAlexWorkRecord,
)
from apps.documents.services import (
    ChunkingError,
    EmbeddingError,
    EmbeddingService,
    PaperChunkingService,
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
    "slice",
    "slicing",
    "orchestration",
    "telecom",
    "telecommunications",
    "wireless",
    "radio",
    "core",
    "gnodeb",
}
_OFFTOPIC_CONCEPTS = {
    "art",
    "arts",
    "visual arts",
    "music",
    "musical",
    "history",
    "philosophy",
    "literature",
    "business",
}


def _log_event(event: str, **fields: Any) -> None:
    payload = {"event": event, **fields}
    logger.info(json.dumps(payload, sort_keys=True, default=str))


class OpenAlexIngestionError(Exception):
    """Raised when OpenAlex fetch or persistence operations fail."""


@dataclass(frozen=True)
class OpenAlexAuthorInput:
    external_id: str
    name: str
    institution_name: str
    author_order: int


@dataclass(frozen=True)
class OpenAlexTopicInput:
    external_id: str
    name: str


@dataclass(frozen=True)
class OpenAlexIngestionSummary:
    counts: dict[str, int]
    paper_ids: list[int]
    author_ids: list[int]
    topic_ids: list[int]


@dataclass(frozen=True)
class _WorkUpsertResult:
    counters: dict[str, int]
    paper_id: int
    author_ids: list[int]
    topic_ids: list[int]


@dataclass(frozen=True)
class OpenAlexReadThroughResult:
    enabled: bool
    attempted: bool
    reason: str
    works_processed: int = 0
    papers_touched: int = 0
    chunks_embedded: int = 0
    duration_ms: int = 0
    error: str | None = None

    @property
    def should_rerun_search(self) -> bool:
        return self.attempted and self.papers_touched > 0

    def to_payload(self) -> dict[str, object]:
        return {
            "enabled": self.enabled,
            "attempted": self.attempted,
            "reason": self.reason,
            "works_processed": self.works_processed,
            "papers_touched": self.papers_touched,
            "chunks_embedded": self.chunks_embedded,
            "duration_ms": self.duration_ms,
            "error": self.error,
        }


class OpenAlexIngestionService:
    """Transforms OpenAlex works into local paper/author/topic entities with idempotent upserts."""

    def __init__(
        self,
        *,
        client: OpenAlexClient,
        security_level_ratios: tuple[int, int, int],
        min_query_coverage: float | None = None,
        max_topics_per_work: int | None = None,
    ) -> None:
        if len(security_level_ratios) != 3:
            raise ValueError(
                "security_level_ratios must include PUBLIC, INTERNAL, CONFIDENTIAL percentages."
            )
        if sum(security_level_ratios) != 100:
            raise ValueError("security_level_ratios must sum to 100.")

        self._client = client
        self._public_ratio, self._internal_ratio, _ = security_level_ratios
        self._min_query_coverage = (
            float(min_query_coverage)
            if min_query_coverage is not None
            else float(getattr(settings, "OPENALEX_MIN_QUERY_COVERAGE", 0.18))
        )
        self._max_topics_per_work = (
            int(max_topics_per_work)
            if max_topics_per_work is not None
            else int(getattr(settings, "OPENALEX_MAX_TOPICS_PER_WORK", 8))
        )
        if self._min_query_coverage < 0 or self._min_query_coverage > 1:
            raise ValueError("min_query_coverage must be between 0 and 1.")
        if self._max_topics_per_work <= 0:
            raise ValueError("max_topics_per_work must be greater than zero.")

    def ingest(
        self,
        *,
        query: str,
        limit: int,
        since: date | None,
        filter_expression: str | None = None,
    ) -> dict[str, int]:
        return self.ingest_with_details(
            query=query,
            limit=limit,
            since=since,
            filter_expression=filter_expression,
        ).counts

    def ingest_with_details(
        self,
        *,
        query: str,
        limit: int,
        since: date | None,
        filter_expression: str | None = None,
    ) -> OpenAlexIngestionSummary:
        counters: dict[str, int] = {
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
        }

        paper_ids: set[int] = set()
        author_ids: set[int] = set()
        topic_ids: set[int] = set()

        try:
            works = self._client.iter_works(
                query=query,
                limit=limit,
                since=since,
                filter_expression=filter_expression,
            )
        except OpenAlexClientError as exc:
            raise OpenAlexIngestionError(str(exc)) from exc

        query_terms = self._tokenize(query)
        for raw_work in works:
            try:
                normalized_work = self._client.normalize_work(raw_work)
                if not self._is_relevant_work(work=normalized_work, query_terms=query_terms):
                    counters["works_skipped"] += 1
                    _log_event(
                        "openalex.work_skipped",
                        reason="low_query_alignment",
                        external_id=normalized_work.external_id,
                    )
                    continue
                result = self._upsert_work(normalized_work)
            except (OpenAlexIngestionError, OpenAlexClientError) as exc:
                counters["works_skipped"] += 1
                _log_event("openalex.work_skipped", reason=str(exc))
                continue
            except DatabaseError as exc:
                raise OpenAlexIngestionError(
                    f"Database error while ingesting OpenAlex work: {exc}"
                ) from exc

            counters["works_processed"] += 1
            for key, value in result.counters.items():
                counters[key] += value
            paper_ids.add(result.paper_id)
            author_ids.update(result.author_ids)
            topic_ids.update(result.topic_ids)

        _log_event(
            "openalex.ingest_complete",
            query=query,
            since=since,
            limit=limit,
            filter_expression=filter_expression,
            counters=counters,
        )
        return OpenAlexIngestionSummary(
            counts=counters,
            paper_ids=sorted(paper_ids),
            author_ids=sorted(author_ids),
            topic_ids=sorted(topic_ids),
        )

    def upsert_authors(self, *, raw_authors: list[dict[str, Any]], limit: int) -> dict[str, int]:
        if limit <= 0:
            return {"authors_created": 0, "authors_updated": 0, "authors_processed": 0}

        counters = {"authors_created": 0, "authors_updated": 0, "authors_processed": 0}
        seen_external_ids: set[str] = set()

        for raw_author in raw_authors:
            if counters["authors_processed"] >= limit:
                break

            try:
                author_record = self._client.normalize_author(raw_author)
            except OpenAlexClientError:
                continue

            if author_record.external_id in seen_external_ids:
                continue
            seen_external_ids.add(author_record.external_id)

            author_row, created = Author.objects.update_or_create(
                external_id=author_record.external_id,
                defaults={
                    "name": author_record.name,
                    "institution_name": author_record.institution_name,
                },
            )
            counters["authors_created" if created else "authors_updated"] += 1
            counters["authors_processed"] += 1

            _ = author_row.id

        return counters

    def _upsert_work(self, work: OpenAlexWorkRecord) -> _WorkUpsertResult:
        security_level = self._assign_security_level(work.external_id)
        author_inputs = self._extract_authors(work)
        topic_inputs = self._extract_topics(work)

        counters = {
            "papers_created": 0,
            "papers_updated": 0,
            "authors_created": 0,
            "authors_updated": 0,
            "topics_created": 0,
            "topics_updated": 0,
            "authorship_links": 0,
            "paper_topic_links": 0,
        }

        with transaction.atomic():
            paper, paper_created = self._upsert_paper(
                external_id=work.external_id,
                title=work.title,
                abstract=work.abstract,
                published_date=work.published_date,
                doi=work.doi,
                security_level=security_level,
            )
            counters["papers_created" if paper_created else "papers_updated"] += 1

            authorship_links, author_counter_delta, author_ids = self._replace_authorships(
                paper=paper,
                authors=author_inputs,
            )
            counters["authorship_links"] += authorship_links
            counters["authors_created"] += author_counter_delta["authors_created"]
            counters["authors_updated"] += author_counter_delta["authors_updated"]

            paper_topic_links, topic_counter_delta, topic_ids = self._replace_paper_topics(
                paper=paper,
                topics=topic_inputs,
            )
            counters["paper_topic_links"] += paper_topic_links
            counters["topics_created"] += topic_counter_delta["topics_created"]
            counters["topics_updated"] += topic_counter_delta["topics_updated"]

        return _WorkUpsertResult(
            counters=counters,
            paper_id=paper.id,
            author_ids=author_ids,
            topic_ids=topic_ids,
        )

    def _upsert_paper(
        self,
        *,
        external_id: str,
        title: str,
        abstract: str,
        published_date: date | None,
        doi: str | None,
        security_level: str,
    ) -> tuple[Paper, bool]:
        defaults = {
            "title": title,
            "abstract": abstract,
            "published_date": published_date,
            "doi": doi,
            "security_level": security_level,
        }

        try:
            paper, created = Paper.objects.update_or_create(
                external_id=external_id,
                defaults=defaults,
            )
            return paper, created
        except IntegrityError as exc:
            if doi is None:
                raise OpenAlexIngestionError(
                    "Paper upsert failed for "
                    f"external_id={external_id} due to unique constraint conflict."
                ) from exc

            paper = Paper.objects.filter(doi=doi).first()
            if paper is None:
                raise OpenAlexIngestionError(
                    "Paper upsert failed for "
                    f"external_id={external_id}; DOI conflict could not be resolved."
                ) from exc

            paper.external_id = external_id
            paper.title = title
            paper.abstract = abstract
            paper.published_date = published_date
            paper.security_level = security_level
            paper.save(
                update_fields=[
                    "external_id",
                    "title",
                    "abstract",
                    "published_date",
                    "security_level",
                    "doi",
                ]
            )
            return paper, False

    def _replace_authorships(
        self,
        *,
        paper: Paper,
        authors: list[OpenAlexAuthorInput],
    ) -> tuple[int, dict[str, int], list[int]]:
        Authorship.objects.filter(paper=paper).delete()

        counters = {"authors_created": 0, "authors_updated": 0}
        authorships: list[Authorship] = []
        author_ids: list[int] = []
        seen_external_ids: set[str] = set()

        for author in authors:
            if author.external_id in seen_external_ids:
                continue
            seen_external_ids.add(author.external_id)

            author_row, created = Author.objects.update_or_create(
                external_id=author.external_id,
                defaults={
                    "name": author.name,
                    "institution_name": author.institution_name,
                },
            )
            counters["authors_created" if created else "authors_updated"] += 1
            author_ids.append(author_row.id)
            authorships.append(
                Authorship(
                    author=author_row,
                    paper=paper,
                    author_order=author.author_order,
                )
            )

        Authorship.objects.bulk_create(authorships)
        return len(authorships), counters, author_ids

    def _replace_paper_topics(
        self,
        *,
        paper: Paper,
        topics: list[OpenAlexTopicInput],
    ) -> tuple[int, dict[str, int], list[int]]:
        PaperTopic.objects.filter(paper=paper).delete()

        counters = {"topics_created": 0, "topics_updated": 0}
        paper_topics: list[PaperTopic] = []
        topic_ids: list[int] = []
        seen_external_ids: set[str] = set()

        for topic in topics:
            if topic.external_id in seen_external_ids:
                continue
            seen_external_ids.add(topic.external_id)

            topic_row, created = Topic.objects.update_or_create(
                external_id=topic.external_id,
                defaults={"name": topic.name},
            )
            counters["topics_created" if created else "topics_updated"] += 1
            topic_ids.append(topic_row.id)
            paper_topics.append(PaperTopic(paper=paper, topic=topic_row))

        PaperTopic.objects.bulk_create(paper_topics)
        return len(paper_topics), counters, topic_ids

    def _assign_security_level(self, external_id: str) -> str:
        bucket = int(hashlib.sha1(external_id.encode("utf-8")).hexdigest(), 16) % 100
        public_limit = self._public_ratio
        internal_limit = public_limit + self._internal_ratio

        if bucket < public_limit:
            return SecurityLevel.PUBLIC
        if bucket < internal_limit:
            return SecurityLevel.INTERNAL
        return SecurityLevel.CONFIDENTIAL

    @staticmethod
    def _extract_authors(work: OpenAlexWorkRecord) -> list[OpenAlexAuthorInput]:
        result: list[OpenAlexAuthorInput] = []
        for index, author in enumerate(work.authors, start=1):
            result.append(
                OpenAlexAuthorInput(
                    external_id=author.external_id,
                    name=author.name,
                    institution_name=author.institution_name,
                    author_order=author.author_order or index,
                )
            )
        return result

    def _extract_topics(self, work: OpenAlexWorkRecord) -> list[OpenAlexTopicInput]:
        paper_terms = self._tokenize(f"{work.title} {work.abstract}")
        topics: list[OpenAlexTopicInput] = []
        for concept in work.concepts:
            concept_name = concept.name.strip()
            concept_terms = self._tokenize(concept_name)
            if not concept_terms:
                continue
            if concept_name.lower() in _OFFTOPIC_CONCEPTS:
                continue
            if (
                concept_terms & _TELECOM_TERMS
                or concept_terms & paper_terms
                or len(topics) < 3
            ):
                topics.append(
                    OpenAlexTopicInput(external_id=concept.external_id, name=concept_name)
                )
                if len(topics) >= self._max_topics_per_work:
                    break
        return topics

    def _is_relevant_work(self, *, work: OpenAlexWorkRecord, query_terms: set[str]) -> bool:
        if not query_terms:
            return True

        corpus_terms = self._tokenize(
            " ".join([work.title, work.abstract, *(concept.name for concept in work.concepts)])
        )
        if not corpus_terms:
            return False

        overlap = query_terms & corpus_terms
        overlap_count = len(overlap)
        if overlap_count == 0:
            return False

        coverage = overlap_count / float(len(query_terms))
        if coverage < self._min_query_coverage and overlap_count < 2:
            return False

        telecom_query_terms = {term for term in query_terms if term in _TELECOM_TERMS}
        if telecom_query_terms and not (telecom_query_terms & corpus_terms):
            return False

        return True

    @staticmethod
    def _tokenize(text: str) -> set[str]:
        return {token for token in re.findall(r"[a-zA-Z0-9]+", text.lower()) if len(token) >= 3}


class OpenAlexReadThroughService:
    """Fetches live OpenAlex data when local search results are sparse."""

    def __init__(
        self,
        *,
        enabled: bool | None = None,
        min_results: int | None = None,
        fetch_limit: int | None = None,
        cooldown_seconds: int | None = None,
    ) -> None:
        self._enabled = (
            bool(enabled)
            if enabled is not None
            else bool(getattr(settings, "OPENALEX_LIVE_FETCH", True))
        )
        self._min_results = (
            int(min_results)
            if min_results is not None
            else int(getattr(settings, "OPENALEX_LIVE_MIN_RESULTS", 10))
        )
        self._fetch_limit = (
            int(fetch_limit)
            if fetch_limit is not None
            else int(getattr(settings, "OPENALEX_LIVE_FETCH_LIMIT", 40))
        )
        self._cooldown_seconds = (
            int(cooldown_seconds)
            if cooldown_seconds is not None
            else int(getattr(settings, "OPENALEX_LIVE_FETCH_COOLDOWN_SECONDS", 900))
        )

        if self._min_results <= 0:
            raise ValueError("min_results must be greater than zero.")
        if self._fetch_limit <= 0:
            raise ValueError("fetch_limit must be greater than zero.")
        if self._cooldown_seconds < 0:
            raise ValueError("cooldown_seconds must be zero or greater.")

    def fetch_if_needed(
        self,
        *,
        query: str,
        current_result_count: int,
        page: int,
    ) -> OpenAlexReadThroughResult:
        query_text = query.strip()
        if not self._enabled:
            return OpenAlexReadThroughResult(
                enabled=False,
                attempted=False,
                reason="disabled",
            )
        if not query_text:
            return OpenAlexReadThroughResult(
                enabled=True,
                attempted=False,
                reason="empty_query",
            )
        if page != 1:
            return OpenAlexReadThroughResult(
                enabled=True,
                attempted=False,
                reason="page_not_supported",
            )
        if current_result_count >= self._min_results:
            return OpenAlexReadThroughResult(
                enabled=True,
                attempted=False,
                reason="sufficient_local_results",
            )
        if not settings.OPENALEX_API_KEY:
            return OpenAlexReadThroughResult(
                enabled=True,
                attempted=False,
                reason="missing_api_key",
            )
        if self._is_in_cooldown(query_text):
            return OpenAlexReadThroughResult(
                enabled=True,
                attempted=False,
                reason="cooldown",
            )

        run_query = self._run_query(query_text)
        run = IngestionRun.objects.create(
            query=run_query,
            status=IngestionStatus.RUNNING,
            counts={
                "source": "live_read_through",
                "requested_limit": self._fetch_limit,
                "result_count_before": current_result_count,
            },
        )
        started = timezone.now()

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
                cache_enabled=settings.OPENALEX_CACHE_ENABLED,
                cache_ttl_seconds=settings.OPENALEX_CACHE_TTL_SECONDS,
            )
            service = OpenAlexIngestionService(
                client=client,
                security_level_ratios=settings.OPENALEX_SECURITY_LEVEL_RATIOS,
            )
            summary = service.ingest_with_details(
                query=query_text,
                limit=self._fetch_limit,
                since=None,
            )

            chunk_stats = PaperChunkingService().chunk_papers(summary.paper_ids)
            chunks_embedded = self._embed_with_fallback(summary.paper_ids)

            finished = timezone.now()
            duration_ms = int((finished - started).total_seconds() * 1000)
            counts = dict(summary.counts)
            counts.update(
                {
                    "source": "live_read_through",
                    "requested_limit": self._fetch_limit,
                    "result_count_before": current_result_count,
                    "papers_touched": len(summary.paper_ids),
                    "authors_touched": len(summary.author_ids),
                    "topics_touched": len(summary.topic_ids),
                    "chunks_generated": chunk_stats["chunks_generated"],
                    "chunks_embedded": chunks_embedded,
                    "duration_ms": duration_ms,
                }
            )

            run.status = IngestionStatus.SUCCESS
            run.finished_at = finished
            run.error_message = ""
            run.counts = counts
            run.save(update_fields=["status", "finished_at", "error_message", "counts"])

            return OpenAlexReadThroughResult(
                enabled=True,
                attempted=True,
                reason="fetched",
                works_processed=summary.counts["works_processed"],
                papers_touched=len(summary.paper_ids),
                chunks_embedded=chunks_embedded,
                duration_ms=duration_ms,
            )
        except (
            OpenAlexClientError,
            OpenAlexIngestionError,
            DatabaseError,
            ChunkingError,
            EmbeddingError,
            ValueError,
        ) as exc:
            _log_event("openalex.live_fetch_failed", query=query_text, error=str(exc))
            self._mark_failed(run=run, error_message=str(exc))
            return OpenAlexReadThroughResult(
                enabled=True,
                attempted=True,
                reason="failed",
                error=str(exc),
            )

    def _embed_with_fallback(self, paper_ids: list[int]) -> int:
        if not paper_ids:
            return 0
        try:
            return EmbeddingService().embed_pending_chunks(
                paper_ids=paper_ids,
                batch_size=128,
                backend_name=settings.EMBEDDING_BACKEND,
            )
        except EmbeddingError:
            return self._deterministic_embed(paper_ids)

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
            row.embedding = OpenAlexReadThroughService._hash_vector(row.text_chunk)
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

    def _is_in_cooldown(self, query: str) -> bool:
        if self._cooldown_seconds <= 0:
            return False
        window_start = timezone.now() - timedelta(seconds=self._cooldown_seconds)
        return IngestionRun.objects.filter(
            query=self._run_query(query),
            status=IngestionStatus.SUCCESS,
            finished_at__gte=window_start,
        ).exists()

    @staticmethod
    def _run_query(query: str) -> str:
        return f"live_fetch:{query}"

    @staticmethod
    def _mark_failed(*, run: IngestionRun, error_message: str) -> None:
        run.status = IngestionStatus.FAILED
        run.finished_at = timezone.now()
        run.error_message = error_message[:5000]
        run.save(update_fields=["status", "finished_at", "error_message"])
