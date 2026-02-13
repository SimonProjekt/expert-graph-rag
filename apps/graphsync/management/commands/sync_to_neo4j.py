from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.graphsync.services import GraphSyncError, GraphSyncService


class Command(BaseCommand):
    help = "Sync Author/Paper/Topic from DB to Neo4j with idempotent upserts."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--limit", type=int, help="Only sync up to N papers.")
        parser.add_argument(
            "--include-collaborators",
            action="store_true",
            help="Also create (:Author)-[:COLLABORATED_WITH]->(:Author) links.",
        )
        parser.add_argument(
            "--progress-every",
            type=int,
            default=25,
            help="Print progress every N papers (default: 25).",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        limit = options.get("limit")
        include_collaborators = bool(options.get("include_collaborators"))
        progress_every = int(options.get("progress_every") or 25)

        if progress_every <= 0:
            raise CommandError("--progress-every must be greater than 0.")

        service = GraphSyncService()

        def on_progress(current: int, total: int) -> None:
            if current == total or current % progress_every == 0:
                self.stdout.write(f"Progress: {current}/{total} papers synced")

        try:
            result = service.sync_to_neo4j(
                limit=limit,
                include_collaborators=include_collaborators,
                progress_callback=on_progress,
            )
        except GraphSyncError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Sync complete: "
                f"papers={result.papers_synced}/{result.papers_total}, "
                f"relations={result.relationships_synced}, "
                f"collaborators={result.collaborators_synced}"
            )
        )
