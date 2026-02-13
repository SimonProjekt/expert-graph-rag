from __future__ import annotations

import json
from typing import Any

from django.core.management.base import BaseCommand
from django.db.models import Q

from apps.documents.models import IngestionRun, IngestionStatus
from apps.documents.verification import DataPipelineVerifier


class Command(BaseCommand):
    help = "Print data-integration stats (works/authors/topics/embeddings/graph + sync timestamps)."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--json",
            action="store_true",
            help="Render output as JSON.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        snapshot = DataPipelineVerifier().collect_snapshot()
        last_run = IngestionRun.objects.order_by("-started_at", "-id").first()
        last_success = (
            IngestionRun.objects.filter(status=IngestionStatus.SUCCESS)
            .order_by("-finished_at", "-id")
            .first()
        )
        openalex_runs = IngestionRun.objects.filter(
            Q(query__icontains="seed_openalex")
            | Q(query__startswith="live_fetch:")
            | Q(query__icontains="openalex")
        ).count()

        payload: dict[str, Any] = {
            "status": snapshot.status,
            "counts": snapshot.counts,
            "embedding_stats": snapshot.embedding_stats,
            "neo4j_stats": snapshot.neo4j_stats,
            "neo4j_error": snapshot.neo4j_error,
            "last_ingestion_run_at": (
                snapshot.last_ingestion_run_at.isoformat()
                if snapshot.last_ingestion_run_at
                else None
            ),
            "last_openalex_sync_at": (
                last_success.finished_at.isoformat()
                if last_success and last_success.finished_at
                else None
            ),
            "openalex_run_count": openalex_runs,
            "last_ingestion_status": (last_run.status if last_run else None),
        }

        if options.get("json"):
            self.stdout.write(json.dumps(payload, indent=2, sort_keys=True))
            return

        self.stdout.write("OpenAlex / Data Integration Stats")
        self.stdout.write(f"Status: {payload['status']}")
        self.stdout.write(
            "Postgres counts: "
            f"papers={snapshot.counts['papers']}, "
            f"authors={snapshot.counts['authors']}, "
            f"topics={snapshot.counts['topics']}, "
            f"authorships={snapshot.counts['authorships']}, "
            f"paper_topics={snapshot.counts['paper_topics']}"
        )
        self.stdout.write(
            "Embeddings: "
            f"total_chunks={snapshot.embedding_stats['total_chunks']}, "
            f"non_null_vectors={snapshot.embedding_stats['non_null_vectors']}, "
            f"avg_chunks_per_paper={snapshot.embedding_stats['avg_chunks_per_paper']}"
        )
        if snapshot.neo4j_error:
            self.stdout.write(f"Neo4j: ERROR ({snapshot.neo4j_error})")
        else:
            self.stdout.write(
                "Neo4j: "
                f"papers={snapshot.neo4j_stats['papers']}, "
                f"authors={snapshot.neo4j_stats['authors']}, "
                f"topics={snapshot.neo4j_stats['topics']}, "
                f"WROTE={snapshot.neo4j_stats['wrote_rels']}, "
                f"HAS_TOPIC={snapshot.neo4j_stats['has_topic_rels']}"
            )
        self.stdout.write(f"Last OpenAlex sync: {payload['last_openalex_sync_at'] or 'n/a'}")
        self.stdout.write(f"OpenAlex runs tracked: {openalex_runs}")
