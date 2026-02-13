"""OpenAlex HTTP client with retries, rate limiting, and normalization helpers."""

from __future__ import annotations

import json
import logging
import random
import time
from dataclasses import dataclass
from datetime import date
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

logger = logging.getLogger(__name__)


class OpenAlexClientError(Exception):
    """Raised when OpenAlex communication or payload parsing fails."""


@dataclass(frozen=True)
class OpenAlexAuthorRecord:
    external_id: str
    name: str
    institution_name: str
    author_order: int | None = None


@dataclass(frozen=True)
class OpenAlexConceptRecord:
    external_id: str
    name: str


@dataclass(frozen=True)
class OpenAlexWorkRecord:
    external_id: str
    title: str
    abstract: str
    published_date: date | None
    doi: str | None
    authors: tuple[OpenAlexAuthorRecord, ...]
    concepts: tuple[OpenAlexConceptRecord, ...]


class RateLimiter:
    """Simple limiter enforcing a minimum delay between requests."""

    def __init__(self, requests_per_second: int) -> None:
        if requests_per_second <= 0:
            raise ValueError("requests_per_second must be greater than 0.")
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
            time.sleep(sleep_for)

        self._last_request_at = time.monotonic()


class OpenAlexClient:
    """HTTP client for OpenAlex works/authors retrieval."""

    DEFAULT_WORK_SELECT_FIELDS: tuple[str, ...] = (
        "id",
        "display_name",
        "publication_date",
        "doi",
        "abstract_inverted_index",
        "authorships",
        "concepts",
    )
    DEFAULT_AUTHOR_SELECT_FIELDS: tuple[str, ...] = (
        "id",
        "display_name",
        "last_known_institutions",
    )

    def __init__(
        self,
        *,
        base_url: str,
        api_key: str,
        mailto: str,
        timeout_seconds: int,
        max_retries: int,
        backoff_seconds: int,
        rate_limit_rps: int,
        page_size: int,
    ) -> None:
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds must be greater than 0.")
        if max_retries < 0:
            raise ValueError("max_retries must be zero or greater.")
        if backoff_seconds < 0:
            raise ValueError("backoff_seconds must be zero or greater.")
        if page_size <= 0:
            raise ValueError("page_size must be greater than 0.")

        api_key_value = api_key.strip()
        if not api_key_value:
            raise ValueError("OPENALEX_API_KEY is required for OpenAlex requests.")

        self._base_url = base_url.rstrip("/")
        self._api_key = api_key_value
        self._mailto = (mailto or "").strip()
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
        since: date | None = None,
        filter_expression: str | None = None,
        select_fields: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        works: list[dict[str, Any]] = []
        cursor = "*"
        while len(works) < limit:
            per_page = min(self._page_size, limit - len(works))
            payload = self.get_works(
                query=query,
                filter_expression=self._merge_filters(
                    since=since,
                    filter_expression=filter_expression,
                ),
                per_page=per_page,
                cursor=cursor,
                select_fields=select_fields or self.DEFAULT_WORK_SELECT_FIELDS,
            )

            results = payload.get("results")
            if not isinstance(results, list):
                raise OpenAlexClientError("OpenAlex works payload missing list field 'results'.")

            for item in results:
                if isinstance(item, dict):
                    works.append(item)
                if len(works) >= limit:
                    break

            meta = payload.get("meta")
            next_cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
            if not next_cursor or not results:
                break
            cursor = str(next_cursor)

        return works

    def iter_authors(
        self,
        *,
        query: str,
        limit: int,
        filter_expression: str | None = None,
        select_fields: tuple[str, ...] | None = None,
    ) -> list[dict[str, Any]]:
        if limit <= 0:
            return []

        authors: list[dict[str, Any]] = []
        cursor = "*"
        while len(authors) < limit:
            per_page = min(self._page_size, limit - len(authors))
            payload = self.get_authors(
                query=query,
                filter_expression=filter_expression,
                per_page=per_page,
                cursor=cursor,
                select_fields=select_fields or self.DEFAULT_AUTHOR_SELECT_FIELDS,
            )

            results = payload.get("results")
            if not isinstance(results, list):
                raise OpenAlexClientError("OpenAlex authors payload missing list field 'results'.")

            for item in results:
                if isinstance(item, dict):
                    authors.append(item)
                if len(authors) >= limit:
                    break

            meta = payload.get("meta")
            next_cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
            if not next_cursor or not results:
                break
            cursor = str(next_cursor)

        return authors

    def get_works(
        self,
        *,
        query: str | None = None,
        filter_expression: str | None = None,
        per_page: int | None = None,
        page: int | None = None,
        cursor: str | None = None,
        select_fields: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        params = self._base_query_params(
            query=query,
            filter_expression=filter_expression,
            per_page=per_page,
            page=page,
            cursor=cursor,
            select_fields=select_fields or self.DEFAULT_WORK_SELECT_FIELDS,
        )
        return self.request(path="/works", params=params)

    def get_authors(
        self,
        *,
        query: str | None = None,
        filter_expression: str | None = None,
        per_page: int | None = None,
        page: int | None = None,
        cursor: str | None = None,
        select_fields: tuple[str, ...] | None = None,
    ) -> dict[str, Any]:
        params = self._base_query_params(
            query=query,
            filter_expression=filter_expression,
            per_page=per_page,
            page=page,
            cursor=cursor,
            select_fields=select_fields or self.DEFAULT_AUTHOR_SELECT_FIELDS,
        )
        return self.request(path="/authors", params=params)

    def request(self, *, path: str, params: dict[str, str]) -> dict[str, Any]:
        query_params = {key: value for key, value in params.items() if value != ""}
        query_params["api_key"] = self._api_key
        query_params["mailto"] = self._mailto

        if "per-page" not in query_params:
            query_params["per-page"] = str(self._page_size)

        url = f"{self._base_url}/{path.lstrip('/')}?{urlencode(query_params)}"
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
                with urlopen(request, timeout=self._timeout_seconds) as response:
                    raw_body = response.read().decode("utf-8")
                payload = json.loads(raw_body)
                if not isinstance(payload, dict):
                    raise OpenAlexClientError("OpenAlex returned non-object JSON payload.")
                return payload
            except HTTPError as exc:
                if self._should_retry(status_code=exc.code, attempt=attempt):
                    sleep_for = self._calculate_backoff(
                        attempt=attempt,
                        retry_after_header=exc.headers.get("Retry-After"),
                    )
                    logger.warning(
                        "OpenAlex HTTP error %s for %s. Retrying in %.2fs (attempt %s/%s).",
                        exc.code,
                        path,
                        sleep_for,
                        attempt + 1,
                        self._max_retries + 1,
                    )
                    time.sleep(sleep_for)
                    continue
                raise OpenAlexClientError(
                    f"OpenAlex HTTP error {exc.code}: {exc.reason}"
                ) from exc
            except (URLError, TimeoutError, json.JSONDecodeError) as exc:
                if attempt < self._max_retries:
                    sleep_for = self._calculate_backoff(attempt=attempt, retry_after_header=None)
                    logger.warning(
                        "OpenAlex request failure for %s. Retrying in %.2fs (attempt %s/%s): %s",
                        path,
                        sleep_for,
                        attempt + 1,
                        self._max_retries + 1,
                        exc,
                    )
                    time.sleep(sleep_for)
                    continue
                raise OpenAlexClientError(f"OpenAlex request failed after retries: {exc}") from exc

        raise OpenAlexClientError("OpenAlex request exhausted retries unexpectedly.")

    @staticmethod
    def normalize_work(raw_work: dict[str, Any]) -> OpenAlexWorkRecord:
        external_id = OpenAlexClient._as_non_empty_string(raw_work.get("id"))
        if external_id is None:
            raise OpenAlexClientError("Work payload missing 'id'.")

        title = OpenAlexClient._as_non_empty_string(raw_work.get("display_name")) or "Untitled"
        abstract = OpenAlexClient.decode_abstract(raw_work.get("abstract_inverted_index"))
        published_date = OpenAlexClient._parse_date(raw_work.get("publication_date"))
        doi = OpenAlexClient._normalize_doi(raw_work.get("doi"))

        return OpenAlexWorkRecord(
            external_id=external_id,
            title=title,
            abstract=abstract,
            published_date=published_date,
            doi=doi,
            authors=tuple(OpenAlexClient.extract_authors_from_work(raw_work)),
            concepts=tuple(OpenAlexClient.extract_concepts_from_work(raw_work)),
        )

    @staticmethod
    def normalize_author(raw_author: dict[str, Any]) -> OpenAlexAuthorRecord:
        external_id = OpenAlexClient._as_non_empty_string(raw_author.get("id"))
        if external_id is None:
            raise OpenAlexClientError("Author payload missing 'id'.")

        name = OpenAlexClient._as_non_empty_string(raw_author.get("display_name")) or "Unknown"
        institution_name = "unknown"
        institutions = raw_author.get("last_known_institutions")
        if isinstance(institutions, list) and institutions:
            first = institutions[0]
            if isinstance(first, dict):
                institution_name = (
                    OpenAlexClient._as_non_empty_string(first.get("display_name")) or "unknown"
                )

        return OpenAlexAuthorRecord(
            external_id=external_id,
            name=name,
            institution_name=institution_name,
            author_order=None,
        )

    @staticmethod
    def extract_authors_from_work(raw_work: dict[str, Any]) -> list[OpenAlexAuthorRecord]:
        raw_authorships = raw_work.get("authorships")
        if not isinstance(raw_authorships, list):
            return []

        records: list[OpenAlexAuthorRecord] = []
        for index, authorship in enumerate(raw_authorships, start=1):
            if not isinstance(authorship, dict):
                continue
            author_payload = authorship.get("author")
            if not isinstance(author_payload, dict):
                continue

            external_id = OpenAlexClient._as_non_empty_string(author_payload.get("id"))
            if external_id is None:
                continue
            name = (
                OpenAlexClient._as_non_empty_string(author_payload.get("display_name"))
                or "Unknown"
            )

            institution_name = "unknown"
            institutions = authorship.get("institutions")
            if isinstance(institutions, list) and institutions:
                first = institutions[0]
                if isinstance(first, dict):
                    institution_name = (
                        OpenAlexClient._as_non_empty_string(first.get("display_name")) or "unknown"
                    )

            records.append(
                OpenAlexAuthorRecord(
                    external_id=external_id,
                    name=name,
                    institution_name=institution_name,
                    author_order=index,
                )
            )

        return records

    @staticmethod
    def extract_concepts_from_work(raw_work: dict[str, Any]) -> list[OpenAlexConceptRecord]:
        raw_concepts = raw_work.get("concepts")
        if not isinstance(raw_concepts, list):
            return []

        concepts: list[OpenAlexConceptRecord] = []
        for concept in raw_concepts:
            if not isinstance(concept, dict):
                continue
            external_id = OpenAlexClient._as_non_empty_string(concept.get("id"))
            name = OpenAlexClient._as_non_empty_string(concept.get("display_name"))
            if external_id is None or name is None:
                continue
            concepts.append(OpenAlexConceptRecord(external_id=external_id, name=name))

        return concepts[:20]

    @staticmethod
    def decode_abstract(raw_index: Any) -> str:
        if not isinstance(raw_index, dict):
            return ""

        token_by_position: dict[int, str] = {}
        for token, positions in raw_index.items():
            if not isinstance(token, str) or not isinstance(positions, list):
                continue
            for position in positions:
                if (
                    isinstance(position, int)
                    and position >= 0
                    and position < 50000
                    and position not in token_by_position
                ):
                    token_by_position[position] = token

        if not token_by_position:
            return ""

        return " ".join(token_by_position[position] for position in sorted(token_by_position))

    @staticmethod
    def _merge_filters(*, since: date | None, filter_expression: str | None) -> str | None:
        filters: list[str] = []
        if since is not None:
            filters.append(f"from_publication_date:{since.isoformat()}")
        if filter_expression:
            filters.append(filter_expression)
        return ",".join(filters) if filters else None

    @staticmethod
    def _base_query_params(
        *,
        query: str | None,
        filter_expression: str | None,
        per_page: int | None,
        page: int | None,
        cursor: str | None,
        select_fields: tuple[str, ...],
    ) -> dict[str, str]:
        params: dict[str, str] = {}
        if query:
            params["search"] = query.strip()
        if filter_expression:
            params["filter"] = filter_expression
        if per_page is not None:
            params["per-page"] = str(per_page)
        if cursor:
            params["cursor"] = cursor
        elif page is not None:
            params["page"] = str(page)
        if select_fields:
            params["select"] = ",".join(select_fields)
        return params

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
        base = min(self._backoff_seconds * float(2**attempt), 60.0)
        jitter = random.uniform(0.0, min(1.0, base / 4.0))
        return min(base + jitter, 60.0)

    def _should_retry(self, *, status_code: int, attempt: int) -> bool:
        retryable = {429, 500, 502, 503, 504}
        return attempt < self._max_retries and status_code in retryable

    @staticmethod
    def _as_non_empty_string(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized if normalized else None

    @staticmethod
    def _parse_date(raw_value: Any) -> date | None:
        value = OpenAlexClient._as_non_empty_string(raw_value)
        if value is None:
            return None
        try:
            return date.fromisoformat(value)
        except ValueError:
            return None

    @staticmethod
    def _normalize_doi(raw_value: Any) -> str | None:
        value = OpenAlexClient._as_non_empty_string(raw_value)
        if value is None:
            return None
        return value.removeprefix("https://doi.org/").removeprefix("http://doi.org/")
