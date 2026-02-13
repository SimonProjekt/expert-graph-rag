from __future__ import annotations

import json
from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.documents.models import Author, Authorship, Embedding, Paper, Topic
from apps.documents.services import EmbeddingError
from apps.documents.verification import PipelineSnapshot


@pytest.mark.django_db
def test_seed_demo_data_falls_back_to_deterministic_embeddings() -> None:
    output = StringIO()

    with patch(
        "apps.documents.management.commands.seed_demo_data.EmbeddingService.embed_pending_chunks",
        side_effect=EmbeddingError("local model unavailable"),
    ):
        with patch(
            "apps.documents.management.commands.seed_demo_data.GraphSyncService.sync_to_neo4j"
        ) as sync_mock:
            sync_mock.return_value = type(
                "SyncResult",
                (),
                {
                    "papers_total": 3,
                    "papers_synced": 3,
                    "relationships_synced": 10,
                    "collaborators_synced": 2,
                },
            )()
            call_command("seed_demo_data", stdout=output)

    assert Paper.objects.count() >= 3
    assert Author.objects.count() >= 3
    assert Topic.objects.count() >= 3
    assert Embedding.objects.filter(embedding__isnull=False).exists()
    assert "deterministic-fallback" in output.getvalue()


@pytest.mark.django_db
def test_seed_demo_data_replaces_authorship_order_without_unique_conflict(tmp_path) -> None:
    first_fixture = [
        {
            "title": "Seed Paper",
            "abstract": "Initial abstract.",
            "published_date": "2025-01-01",
            "doi": "10.5555/seed-paper-001",
            "external_id": "openalex:W_seed_001",
            "security_level": "PUBLIC",
            "authors": [
                {
                    "name": "Author One",
                    "external_id": "openalex:A_seed_001",
                    "institution_name": "Lab A",
                },
                {
                    "name": "Author Two",
                    "external_id": "openalex:A_seed_002",
                    "institution_name": "Lab B",
                },
            ],
            "topics": [
                {
                    "name": "ran optimization",
                    "external_id": "openalex:T_seed_001",
                }
            ],
        }
    ]
    second_fixture = [
        {
            "title": "Seed Paper Updated",
            "abstract": "Updated abstract.",
            "published_date": "2025-01-02",
            "doi": "10.5555/seed-paper-001",
            "external_id": "openalex:W_seed_001",
            "security_level": "PUBLIC",
            "authors": [
                {
                    "name": "Author Three",
                    "external_id": "openalex:A_seed_003",
                    "institution_name": "Lab C",
                },
                {
                    "name": "Author Four",
                    "external_id": "openalex:A_seed_004",
                    "institution_name": "Lab D",
                },
            ],
            "topics": [
                {
                    "name": "network slicing",
                    "external_id": "openalex:T_seed_002",
                }
            ],
        }
    ]

    first_fixture_path = tmp_path / "seed_fixture_1.json"
    second_fixture_path = tmp_path / "seed_fixture_2.json"
    first_fixture_path.write_text(json.dumps(first_fixture), encoding="utf-8")
    second_fixture_path.write_text(json.dumps(second_fixture), encoding="utf-8")

    with patch(
        "apps.documents.management.commands.seed_demo_data.PaperChunkingService.chunk_papers",
        return_value={"chunks_generated": 0, "chunks_created": 0, "chunks_updated": 0},
    ):
        with patch(
            "apps.documents.management.commands.seed_demo_data.EmbeddingService.embed_pending_chunks",
            return_value=0,
        ):
            call_command(
                "seed_demo_data",
                fixture=str(first_fixture_path),
                backend="local",
                skip_graph_sync=True,
            )
            call_command(
                "seed_demo_data",
                fixture=str(second_fixture_path),
                backend="local",
                skip_graph_sync=True,
            )

    paper = Paper.objects.get(external_id="openalex:W_seed_001")
    authorships = list(
        Authorship.objects.filter(paper=paper)
        .select_related("author")
        .order_by("author_order")
        .values_list("author__external_id", "author_order")
    )
    assert authorships == [
        ("openalex:A_seed_003", 1),
        ("openalex:A_seed_004", 2),
    ]


@pytest.mark.django_db
def test_startup_check_warns_when_embeddings_or_graph_missing() -> None:
    snapshot = PipelineSnapshot(
        counts={
            "papers": 2,
            "authors": 2,
            "topics": 2,
            "authorships": 2,
            "paper_topics": 2,
        },
        embedding_stats={
            "total_chunks": 0,
            "non_null_vectors": 0,
            "avg_chunks_per_paper": 0.0,
        },
        neo4j_stats={
            "papers": 0,
            "authors": 0,
            "topics": 0,
            "wrote_rels": 0,
            "has_topic_rels": 0,
        },
        neo4j_error=None,
        last_ingestion_run_at=timezone.now(),
        last_embed_run_at=None,
        last_graph_sync_at=None,
        status="degraded",
    )

    output = StringIO()
    with patch(
        "apps.health.management.commands.startup_check.DataPipelineVerifier.collect_snapshot",
        return_value=snapshot,
    ):
        call_command("startup_check", stdout=output)

    rendered = output.getvalue()
    assert "WARNING: No embeddings found" in rendered
    assert "WARNING: Graph data is missing or incomplete" in rendered
