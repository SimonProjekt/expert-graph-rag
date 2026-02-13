from __future__ import annotations

import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

from django.test import override_settings

from apps.documents.openalex_client import OpenAlexClient


class FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False


def test_openalex_client_includes_auth_and_select_params() -> None:
    captured_urls: list[str] = []

    def fake_open(request, timeout):  # noqa: ANN001
        captured_urls.append(request.full_url)
        _ = timeout
        return FakeHTTPResponse({"meta": {"next_cursor": None}, "results": []})

    client = OpenAlexClient(
        base_url="https://api.openalex.org",
        api_key="oa-key",
        mailto="dev@example.com",
        timeout_seconds=2,
        max_retries=1,
        backoff_seconds=0,
        rate_limit_rps=1000,
        page_size=200,
    )

    with patch("apps.documents.openalex_client.urlopen", side_effect=fake_open):
        client.get_works(
            query="federated learning",
            per_page=50,
            cursor="*",
            select_fields=("id", "display_name"),
        )

    assert captured_urls
    query = parse_qs(urlparse(captured_urls[0]).query)
    assert query["api_key"] == ["oa-key"]
    assert query["mailto"] == ["dev@example.com"]
    assert query["search"] == ["federated learning"]
    assert query["per-page"] == ["50"]
    assert query["cursor"] == ["*"]
    assert query["select"] == ["id,display_name"]


@override_settings(
    CACHES={
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "openalex-client-cache-tests",
        }
    }
)
def test_openalex_client_uses_cache_for_identical_requests() -> None:
    call_count = 0

    def fake_open(_request, timeout):  # noqa: ANN001
        nonlocal call_count
        _ = timeout
        call_count += 1
        return FakeHTTPResponse({"meta": {"next_cursor": None}, "results": []})

    client = OpenAlexClient(
        base_url="https://api.openalex.org",
        api_key="oa-key",
        mailto="dev@example.com",
        timeout_seconds=2,
        max_retries=1,
        backoff_seconds=0,
        rate_limit_rps=1000,
        page_size=200,
        cache_enabled=True,
        cache_ttl_seconds=60,
    )

    with patch("apps.documents.openalex_client.urlopen", side_effect=fake_open):
        first = client.get_works(query="graph rag", per_page=10, cursor="*")
        second = client.get_works(query="graph rag", per_page=10, cursor="*")

    assert first == second
    assert call_count == 1
