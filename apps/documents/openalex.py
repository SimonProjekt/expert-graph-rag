"""OpenAlex ingestion pipeline.

Includes retry, rate limiting, and deterministic security classification.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from django.db import DatabaseError, IntegrityError, transaction

from apps.documents.models import (
    Author,
    Authorship,
    Paper,
    PaperTopic,
    SecurityLevel,
    Topic,
)

logger = logging.getLogger(__name__)


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


class RateLimiter:
    """Simple token-less limiter that enforces minimum delay between outbound requests."""

    def __init__(self, requests_per_second: int) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be greater than 0")
        self._min_interval_seconds = 1.0 / float(requests_per_second)
        self._last_request_at: float | None = None

    def wait(self) -> None:
        now = time.monotonic()
        if self._last_request_at is None:
            self._last_request_at = now
            return

        elapsed = now - self._last_request_at
        sleep_for = self._min_interval_seconds - elapsed
        if sleep_for > 0:
            _log_event("openalex.rate_limit_sleep", sleep_seconds=round(sleep_for, 4))
            time.sleep(sleep_for)
        self._last_request_at = time.monotonic()


class OpenAlexClient:
    """HTTP client for paginated OpenAlex work retrieval."""

    def __init__(
        self,
        *,
        base_url: str,
        timeout_seconds: int,
        max_retries: int,
        backoff_seconds: int,
        rate_limit_rps: int,
        page_size: int,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0")
        if max_retries < 0:
            raise ValueError("max_retries must be 0 or greater")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must be 0 or greater")
        if page_size <= 0:
            raise ValueError("page_size must be greater than 0")

        self._base_url = base_url.rstrip("/")
        self._timeout_seconds = timeout_seconds
        self._max_retries = max_retries
        self._backoff_seconds = float(backoff_seconds)
        self._page_size = page_size
        self._rate_limiter = RateLimiter(rate_limit_rps)

    def iter_works(
        self,
        *,
        query: str,
        limit: int,
        since: date | None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        works: list[dict[str, Any]] = []
        cursor = "*"

        while len(works) < limit:
            per_page = min(self._page_size, limit - len(works))
            url = self._build_works_url(
                query=query,
                per_page=per_page,
                since=since,
                cursor=cursor,
            )
            payload = self._request_json(url)

            results = payload.get("results")
            if not isinstance(results, list):
                raise OpenAlexIngestionError(
                    "OpenAlex payload is missing a list 'results' field."
                )

            for item in results:
                if not isinstance(item, dict):
                    continue
                works.append(item)
                if len(works) >= limit:
                    break

            meta = payload.get("meta")
            next_cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
            _log_event(
                "openalex.page_processed",
                fetched_this_page=len(results),
                fetched_total=len(works),
                has_next_cursor=bool(next_cursor),
            )
            if not next_cursor or not results:
                break
            cursor = str(next_cursor)

        return works

    def _build_works_url(
        self,
        *,
        query: str,
        per_page: int,
        since: date | None,
        cursor: str,
    ) -> str:
        filters: list[str] = []
        if since is not None:
            filters.append(f"from_publication_date:{since.isoformat()}")

        params = {
            "search": query,
            "per-page": str(per_page),
            "cursor": cursor,
        }
        if filters:
            params["filter"] = ",".join(filters)

        return f"{self._base_url}/works?{urlencode(params)}"

    def _request_json(self, url: str) -> dict[str, Any]:
        request = Request(
            url=url,
            headers={
                "Accept": "application/json",
                "User-Agent": "expert-graph-rag/0.1",
            },
        )

        for attempt in range(self._max_retries + 1):
            self._rate_limiter.wait()
            try:
                started = time.monotonic()
                with urlopen(request, timeout=self._timeout_seconds) as response:
                    body = response.read().decode("utf-8")
                elapsed_ms = int((time.monotonic() - started) * 1000)
                payload = json.loads(body)
                if not isinstance(payload, dict):
                    raise OpenAlexIngestionError("OpenAlex returned a non-object JSON response.")

                _log_event(
                    "openalex.http_success",
                    url=url,
                    attempt=attempt + 1,
                    elapsed_ms=elapsed_ms,
                )
                return payload
            except HTTPError as exc:
                if self._should_retry_http(exc.code, attempt=attempt):
                    sleep_for = self._calculate_backoff(
                        attempt=attempt,
                        retry_after_header=exc.headers.get("Retry-After"),
                    )
                    _log_event(
                        "openalex.http_retry",
                        url=url,
                        attempt=attempt + 1,
                        status_code=exc.code,
                        sleep_seconds=sleep_for,
                    )
                    time.sleep(sleep_for)
                    continue
                raise OpenAlexIngestionError(
                    f"OpenAlex HTTP error {exc.code}: {exc.reason}"
                ) from exc
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt < self._max_retries:
                    sleep_for = self._calculate_backoff(attempt=attempt, retry_after_header=None)
                    _log_event(
                        "openalex.http_retry",
                        url=url,
                        attempt=attempt + 1,
                        error=str(exc),
                        sleep_seconds=sleep_for,
                    )
                    time.sleep(sleep_for)
                    continue
                raise OpenAlexIngestionError(
                    f"OpenAlex request failed after retries: {exc}"
                ) from exc

        raise OpenAlexIngestionError("OpenAlex request failed unexpectedly after retries.")

    def _should_retry_http(self, status_code: int, *, attempt: int) -> bool:
        retryable = {429, 500, 502, 503, 504}
        return attempt < self._max_retries and status_code in retryable

    def _calculate_backoff(self, *, attempt: int, retry_after_header: str | None) -> float:
        if retry_after_header:
            try:
                retry_after_seconds = float(retry_after_header)
                if retry_after_seconds >= 0:
                    return min(retry_after_seconds, 60.0)
            except ValueError:
                pass

        if self._backoff_seconds == 0:
            return 0.0
        return min(self._backoff_seconds * float(2**attempt), 60.0)


class OpenAlexIngestionService:
    """Transforms OpenAlex works into local paper/author/topic entities with idempotent upserts."""

    def __init__(
        self,
        *,
        client: OpenAlexClient,
        security_level_ratios: tuple[int, int, int],
    ) -> None:
        if len(security_level_ratios) != 3:
            raise ValueError(
                "security_level_ratios must include PUBLIC, INTERNAL, "
                "CONFIDENTIAL percentages"
            )
        if sum(security_level_ratios) != 100:
            raise ValueError("security_level_ratios must sum to 100")

        self._client = client
        self._public_ratio, self._internal_ratio, _ = security_level_ratios

    def ingest(self, *, query: str, limit: int, since: date | None) -> dict[str, int]:
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

        works = self._client.iter_works(query=query, limit=limit, since=since)
        for work in works:
            try:
                result = self._upsert_work(work)
            except OpenAlexIngestionError as exc:
                counters["works_skipped"] += 1
                _log_event("openalex.work_skipped", reason=str(exc))
                continue
            except DatabaseError as exc:
                raise OpenAlexIngestionError(
                    f"Database error while ingesting OpenAlex work: {exc}"
                ) from exc

            counters["works_processed"] += 1
            for key, value in result.items():
                counters[key] += value

        _log_event(
            "openalex.ingest_complete",
            query=query,
            since=since,
            limit=limit,
            counters=counters,
        )
        return counters

    def _upsert_work(self, work: dict[str, Any]) -> dict[str, int]:
        external_id = self._as_non_empty_string(work.get("id"))
        if external_id is None:
            raise OpenAlexIngestionError("Work payload missing 'id'.")

        title = self._as_non_empty_string(work.get("display_name")) or "Untitled"
        abstract = self._decode_abstract(work.get("abstract_inverted_index"))
        published_date = self._parse_date(work.get("publication_date"))
        doi = self._normalize_doi(work.get("doi"))
        security_level = self._assign_security_level(external_id)

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
                external_id=external_id,
                title=title,
                abstract=abstract,
                published_date=published_date,
                doi=doi,
                security_level=security_level,
            )
            counters["papers_created" if paper_created else "papers_updated"] += 1

            authorship_links, author_counter_delta = self._replace_authorships(
                paper=paper,
                authors=author_inputs,
            )
            counters["authorship_links"] += authorship_links
            counters["authors_created"] += author_counter_delta["authors_created"]
            counters["authors_updated"] += author_counter_delta["authors_updated"]

            paper_topic_links, topic_counter_delta = self._replace_paper_topics(
                paper=paper,
                topics=topic_inputs,
            )
            counters["paper_topic_links"] += paper_topic_links
            counters["topics_created"] += topic_counter_delta["topics_created"]
            counters["topics_updated"] += topic_counter_delta["topics_updated"]

        return counters

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
    ) -> tuple[int, dict[str, int]]:
        Authorship.objects.filter(paper=paper).delete()

        counters = {"authors_created": 0, "authors_updated": 0}
        authorships: list[Authorship] = []
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
            authorships.append(
                Authorship(
                    author=author_row,
                    paper=paper,
                    author_order=author.author_order,
                )
            )

        Authorship.objects.bulk_create(authorships)
        return len(authorships), counters

    def _replace_paper_topics(
        self,
        *,
        paper: Paper,
        topics: list[OpenAlexTopicInput],
    ) -> tuple[int, dict[str, int]]:
        PaperTopic.objects.filter(paper=paper).delete()

        counters = {"topics_created": 0, "topics_updated": 0}
        paper_topics: list[PaperTopic] = []
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
            paper_topics.append(PaperTopic(paper=paper, topic=topic_row))

        PaperTopic.objects.bulk_create(paper_topics)
        return len(paper_topics), counters

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
    def _extract_authors(work: dict[str, Any]) -> list[OpenAlexAuthorInput]:
        raw_authorships = work.get("authorships")
        if not isinstance(raw_authorships, list):
            return []

        authors: list[OpenAlexAuthorInput] = []
        for index, authorship in enumerate(raw_authorships, start=1):
            if not isinstance(authorship, dict):
                continue

            author_payload = authorship.get("author")
            if not isinstance(author_payload, dict):
                continue

            external_id = OpenAlexIngestionService._as_non_empty_string(
                author_payload.get("id")
            )
            if external_id is None:
                continue

            name = (
                OpenAlexIngestionService._as_non_empty_string(
                    author_payload.get("display_name")
                )
                or "Unknown"
            )

            institution_name = "unknown"
            institutions = authorship.get("institutions")
            if isinstance(institutions, list) and institutions:
                first_institution = institutions[0]
                if isinstance(first_institution, dict):
                    institution_name = (
                        OpenAlexIngestionService._as_non_empty_string(
                            first_institution.get("display_name")
                        )
                        or "unknown"
                    )

            authors.append(
                OpenAlexAuthorInput(
                    external_id=external_id,
                    name=name,
                    institution_name=institution_name,
                    author_order=index,
                )
            )

        return authors

    @staticmethod
    def _extract_topics(work: dict[str, Any]) -> list[OpenAlexTopicInput]:
        raw_concepts = work.get("concepts")
        if not isinstance(raw_concepts, list):
            return []

        topics: list[OpenAlexTopicInput] = []
        for concept in raw_concepts:
            if not isinstance(concept, dict):
                continue

            external_id = OpenAlexIngestionService._as_non_empty_string(concept.get("id"))
            name = OpenAlexIngestionService._as_non_empty_string(concept.get("display_name"))
            if external_id is None or name is None:
                continue

            topics.append(OpenAlexTopicInput(external_id=external_id, name=name))

        return topics[:20]

    @staticmethod
    def _decode_abstract(raw_index: Any) -> str:
        if not isinstance(raw_index, dict):
            return ""

        token_by_position: dict[int, str] = {}
        for token, positions in raw_index.items():
            if not isinstance(token, str) or not isinstance(positions, list):
                continue

            for position in positions:
                if (
                    isinstance(position, int)
                    and 0 <= position < 50000
                    and position not in token_by_position
                ):
                    token_by_position[position] = token

        if not token_by_position:
            return ""

        ordered_tokens = [token_by_position[position] for position in sorted(token_by_position)]
        return " ".join(ordered_tokens)

    @staticmethod
    def _as_non_empty_string(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized if normalized else None

    @staticmethod
    def _parse_date(raw_value: Any) -> date | None:
        value = OpenAlexIngestionService._as_non_empty_string(raw_value)
        if value is None:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _normalize_doi(raw_value: Any) -> str | None:
        value = OpenAlexIngestionService._as_non_empty_string(raw_value)
        if value is None:
            return None

        normalized = value.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
        normalized = normalized.removeprefix("http://dx.doi.org/").strip()
        return normalized or None
