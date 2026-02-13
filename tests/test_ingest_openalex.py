import json
from unittest.mock import Mock, patch
from urllib.error import URLError

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from apps.documents.models import (
    Author,
    Authorship,
    IngestionRun,
    IngestionStatus,
    Paper,
    PaperTopic,
    SecurityLevel,
    Topic,
)
from apps.documents.openalex import OpenAlexClient


class FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False


def _openalex_payload(*, title: str) -> dict:
    return {
        "meta": {"next_cursor": None},
        "results": [
            {
                "id": "https://openalex.org/W1",
                "display_name": title,
                "abstract_inverted_index": {
                    "Graph": [0],
                    "RAG": [1],
                    "for": [2],
                    "enterprise": [3],
                },
                "publication_date": "2025-01-10",
                "doi": "https://doi.org/10.1000/xyz123",
                "authorships": [
                    {
                        "author": {
                            "id": "https://openalex.org/A1",
                            "display_name": "Alice Smith",
                        },
                        "institutions": [{"display_name": "Example University"}],
                    },
                    {
                        "author": {
                            "id": "https://openalex.org/A2",
                            "display_name": "Bob Chen",
                        },
                        "institutions": [{"display_name": "Example Labs"}],
                    },
                ],
                "concepts": [
                    {
                        "id": "https://openalex.org/C1",
                        "display_name": "Graph database",
                        "score": 0.93,
                    },
                    {
                        "id": "https://openalex.org/C2",
                        "display_name": "Information retrieval",
                        "score": 0.89,
                    },
                ],
            }
        ],
    }


@pytest.mark.django_db
@override_settings(
    OPENALEX_BASE_URL="https://api.openalex.org",
    OPENALEX_API_KEY="test-openalex-key",
    OPENALEX_MAILTO="dev@example.com",
    OPENALEX_PAGE_SIZE=200,
    OPENALEX_HTTP_TIMEOUT_SECONDS=2,
    OPENALEX_MAX_RETRIES=1,
    OPENALEX_BACKOFF_SECONDS=0,
    OPENALEX_RATE_LIMIT_RPS=1000,
    OPENALEX_SECURITY_LEVEL_RATIOS=(0, 0, 100),
)
def test_ingest_openalex_upserts_and_tracks_runs() -> None:
    first_payload = _openalex_payload(title="Graph RAG V1")
    second_payload = _openalex_payload(title="Graph RAG V2")

    with patch(
        "apps.documents.openalex_client.urlopen",
        side_effect=[FakeHTTPResponse(first_payload), FakeHTTPResponse(second_payload)],
    ):
        call_command(
            "ingest_openalex",
            "--query",
            "graph rag",
            "--limit",
            "1",
            "--since",
            "2025-01-01",
        )
        call_command(
            "ingest_openalex",
            "--query",
            "graph rag",
            "--limit",
            "1",
            "--since",
            "2025-01-01",
        )

    assert Paper.objects.count() == 1
    assert Author.objects.count() == 2
    assert Topic.objects.count() == 2
    assert Authorship.objects.count() == 2
    assert PaperTopic.objects.count() == 2

    paper = Paper.objects.get(external_id="https://openalex.org/W1")
    assert paper.title == "Graph RAG V2"
    assert paper.security_level == SecurityLevel.CONFIDENTIAL

    runs = IngestionRun.objects.order_by("id")
    assert runs.count() == 2
    for run in runs:
        assert run.status == IngestionStatus.SUCCESS
        assert run.error_message == ""
        assert run.finished_at is not None
        assert run.counts["works_processed"] == 1


@pytest.mark.django_db
@override_settings(
    OPENALEX_BASE_URL="https://api.openalex.org",
    OPENALEX_API_KEY="test-openalex-key",
    OPENALEX_MAILTO="dev@example.com",
    OPENALEX_PAGE_SIZE=200,
    OPENALEX_HTTP_TIMEOUT_SECONDS=2,
    OPENALEX_MAX_RETRIES=0,
    OPENALEX_BACKOFF_SECONDS=0,
    OPENALEX_RATE_LIMIT_RPS=1000,
    OPENALEX_SECURITY_LEVEL_RATIOS=(70, 20, 10),
)
def test_ingest_openalex_marks_failed_run_on_http_error() -> None:
    with patch("apps.documents.openalex_client.urlopen", side_effect=URLError("network down")):
        with pytest.raises(CommandError):
            call_command("ingest_openalex", "--query", "graph rag", "--limit", "1")

    run = IngestionRun.objects.get()
    assert run.status == IngestionStatus.FAILED
    assert run.finished_at is not None
    assert "network down" in run.error_message


@override_settings(
    OPENALEX_BASE_URL="https://api.openalex.org",
    OPENALEX_API_KEY="test-openalex-key",
    OPENALEX_MAILTO="dev@example.com",
    OPENALEX_PAGE_SIZE=200,
    OPENALEX_HTTP_TIMEOUT_SECONDS=2,
    OPENALEX_MAX_RETRIES=2,
    OPENALEX_BACKOFF_SECONDS=1,
    OPENALEX_RATE_LIMIT_RPS=1000,
)
def test_openalex_client_retries_with_backoff() -> None:
    client = OpenAlexClient(
        base_url="https://api.openalex.org",
        api_key="test-openalex-key",
        mailto="dev@example.com",
        timeout_seconds=2,
        max_retries=2,
        backoff_seconds=1,
        rate_limit_rps=1000,
        page_size=200,
    )
    client._rate_limiter.wait = Mock()  # type: ignore[method-assign]

    payload = {"meta": {"next_cursor": None}, "results": []}
    with patch(
        "apps.documents.openalex_client.urlopen",
        side_effect=[URLError("temporary"), FakeHTTPResponse(payload)],
    ), patch("apps.documents.openalex_client.time.sleep") as sleep_mock:
        with patch("apps.documents.openalex_client.random.uniform", return_value=0.0):
            response = client.request(path="/works", params={"search": "test"})

    assert response == payload
    sleep_mock.assert_called_once_with(1.0)
