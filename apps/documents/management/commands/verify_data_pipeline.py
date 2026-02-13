from __future__ import annotations

from typing import Any

from django.core.management.base import BaseCommand, CommandError

from apps.documents.verification import DataPipelineVerifier


class Command(BaseCommand):
    help = "Run end-to-end data integration checks across DB, search, and Neo4j."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--query",
            type=str,
            help="Optional sample query for the search verification check.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        sample_query_raw = options.get("query")
        sample_query = sample_query_raw.strip() if isinstance(sample_query_raw, str) else None
        if sample_query == "":
            sample_query = None

        verifier = DataPipelineVerifier()
        report = verifier.run(sample_query=sample_query)

        output = verifier.format_report(report)
        self.stdout.write(output)

        if not report.ok:
            raise CommandError("Data pipeline verification failed.")

        self.stdout.write(self.style.SUCCESS("Data pipeline verification passed."))
