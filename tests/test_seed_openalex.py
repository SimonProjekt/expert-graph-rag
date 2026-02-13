from __future__ import annotations

import json
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pytest
from django.core.management import call_command
from django.test import override_settings

from apps.documents.models import Author, IngestionRun, IngestionStatus, Paper


class FakeHTTPResponse:
    def __init__(self, payload: dict):
        self._payload = payload

    def read(self) -> bytes:
        return json.dumps(self._payload).encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback) -> bool:
        return False


def _work_payload() -> dict:
    return {
        "meta": {"next_cursor": None},
        "results": [
            {
                "id": "https://openalex.org/W_seed_001",
                "display_name": "Seeded OpenAlex Work",
                "abstract_inverted_index": {"Seeded": [0], "paper": [1]},
                "publication_date": "2025-01-20",
                "doi": "https://doi.org/10.5555/seed-openalex-001",
                "authorships": [
                    {
                        "author": {
                            "id": "https://openalex.org/A_seed_001",
                            "display_name": "Seed Author",
                        },
                        "institutions": [{"display_name": "Seed Labs"}],
                    }
                ],
                "concepts": [
                    {"id": "https://openalex.org/C_seed_001", "display_name": "Knowledge graph"}
                ],
            }
        ],
    }


def _author_payload() -> dict:
    return {
        "meta": {"next_cursor": None},
        "results": [
            {
                "id": "https://openalex.org/A_seed_002",
                "display_name": "Seed Author 2",
                "last_known_institutions": [{"display_name": "Seed Institute"}],
            }
        ],
    }


@pytest.mark.django_db
@override_settings(
    OPENALEX_BASE_URL="https://api.openalex.org",
    OPENALEX_API_KEY="seed-key",
    OPENALEX_MAILTO="seed@example.com",
    OPENALEX_PAGE_SIZE=200,
    OPENALEX_HTTP_TIMEOUT_SECONDS=2,
    OPENALEX_MAX_RETRIES=1,
    OPENALEX_BACKOFF_SECONDS=0,
    OPENALEX_RATE_LIMIT_RPS=1000,
    OPENALEX_SECURITY_LEVEL_RATIOS=(100, 0, 0),
)
def test_seed_openalex_command_ingests_and_tracks_run() -> None:
    work_payload = _work_payload()
    author_payload = _author_payload()

    def fake_open(request, timeout):  # noqa: ANN001
        _ = timeout
        query = parse_qs(urlparse(request.full_url).query)
        if "/authors" in request.full_url:
            assert query["api_key"] == ["seed-key"]
            assert query["mailto"] == ["seed@example.com"]
            return FakeHTTPResponse(author_payload)
        return FakeHTTPResponse(work_payload)

    with patch("apps.documents.openalex_client.urlopen", side_effect=fake_open):
        with patch(
            "apps.documents.management.commands.seed_openalex.GraphSyncService.sync_to_neo4j"
        ) as sync_mock:
            sync_mock.return_value = type(
                "SyncResult",
                (),
                {
                    "papers_total": 1,
                    "papers_synced": 1,
                    "relationships_synced": 2,
                    "collaborators_synced": 0,
                },
            )()
            call_command(
                "seed_openalex",
                "--works",
                "1",
                "--authors",
                "2",
                "--query",
                "knowledge graph",
                "--years",
                "2024-2026",
            )

    assert Paper.objects.filter(external_id="https://openalex.org/W_seed_001").exists()
    assert Author.objects.count() >= 2
    run = IngestionRun.objects.order_by("-id").first()
    assert run is not None
    assert run.status == IngestionStatus.SUCCESS
    assert "seed_openalex" in run.query

