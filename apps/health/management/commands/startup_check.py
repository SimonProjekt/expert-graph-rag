from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand

from apps.documents.verification import DataPipelineVerifier


class Command(BaseCommand):
    help = (
        "Run lightweight startup diagnostics and warn if embeddings or graph data are missing."
    )

    def handle(self, *args: Any, **options: Any) -> None:
        try:
            snapshot = DataPipelineVerifier().collect_snapshot()
        except Exception as exc:  # noqa: BLE001
            self.stdout.write(
                self.style.WARNING(
                    "WARNING: Startup diagnostics could not run yet "
                    f"(likely before migrations): {exc}"
                )
            )
            return

        self.stdout.write(f"Startup data status: {snapshot.status}")

        if snapshot.embedding_stats["non_null_vectors"] == 0:
            self.stdout.write(
                self.style.WARNING(
                    "WARNING: No embeddings found. Run 'python manage.py seed_demo_data' "
                    "or 'python manage.py embed_papers'."
                )
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Embeddings present: "
                    f"{snapshot.embedding_stats['non_null_vectors']} vectors"
                )
            )

        graph_missing = (
            snapshot.neo4j_error is not None
            or snapshot.neo4j_stats["papers"] == 0
            or snapshot.neo4j_stats["wrote_rels"] == 0
            or snapshot.neo4j_stats["has_topic_rels"] == 0
        )

        if graph_missing:
            if snapshot.neo4j_error:
                self.stdout.write(
                    self.style.WARNING(
                        "WARNING: Neo4j graph check failed: "
                        f"{snapshot.neo4j_error}"
                    )
                )
            else:
                self.stdout.write(
                    self.style.WARNING(
                        "WARNING: Graph data is missing or incomplete. Run "
                        "'python manage.py seed_demo_data' or 'python manage.py sync_to_neo4j'."
                    )
                )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Neo4j graph present: "
                    f"papers={snapshot.neo4j_stats['papers']}, "
                    f"wrote={snapshot.neo4j_stats['wrote_rels']}, "
                    f"has_topic={snapshot.neo4j_stats['has_topic_rels']}"
                )
            )

        if snapshot.last_ingestion_run_at:
            self.stdout.write(
                "Last ingestion run: "
                f"{snapshot.last_ingestion_run_at.isoformat()}"
            )
        if snapshot.last_embed_run_at:
            self.stdout.write(
                "Last embedding write: "
                f"{snapshot.last_embed_run_at.isoformat()}"
            )
        if snapshot.last_graph_sync_at:
            self.stdout.write(
                "Last graph sync (from Neo4j): "
                f"{snapshot.last_graph_sync_at.isoformat()}"
            )
