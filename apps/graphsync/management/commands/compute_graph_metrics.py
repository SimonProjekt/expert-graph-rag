from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.graphsync.services import GraphMetricsError, GraphMetricsService


class Command(BaseCommand):
    help = "Compute author graph centrality from Neo4j and persist it to the database."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--no-reset-missing",
            action="store_true",
            help=(
                "Do not reset centrality_score to 0.0 for local authors missing in the graph."
            ),
        )

    def handle(self, *args: Any, **options: Any) -> None:
        reset_missing = not bool(options.get("no_reset_missing"))

        service = GraphMetricsService()
        try:
            result = service.compute_author_centrality(reset_missing=reset_missing)
        except GraphMetricsError as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write(
            self.style.SUCCESS(
                "Graph metrics complete: "
                f"method={result.method}, "
                f"authors_updated={result.authors_updated}, "
                f"authors_total={result.authors_total}"
            )
        )
