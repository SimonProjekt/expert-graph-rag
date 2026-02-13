from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.management import call_command
from django.utils import timezone

from apps.documents.models import Author, Embedding, Paper, Topic
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
