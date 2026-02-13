from __future__ import annotations

from typing import Any

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError

DEMO_QUERIES = (
    "5G RAN optimization with AI scheduling",
    "network slicing orchestration reliability",
    "O-RAN xApp policy optimization",
    "federated learning for telecom networks",
    "core network anomaly detection",
    "energy efficient base station control",
    "near-real-time RIC optimization for massive MIMO",
    "telecom digital twins for radio resource management",
    "self-healing mobile network fault prediction",
    "private 5G industrial campus network automation",
)


class Command(BaseCommand):
    help = (
        "Seed interview-ready telecom demo data: local fixture + optional multi-query OpenAlex "
        "ingestion + embedding + graph sync."
    )

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--works-per-query",
            type=int,
            default=80,
            help="Target works per OpenAlex query when API key is configured (default: 80).",
        )
        parser.add_argument(
            "--authors-per-query",
            type=int,
            default=40,
            help="Target authors per OpenAlex query when API key is configured (default: 40).",
        )
        parser.add_argument(
            "--years",
            type=str,
            default="2021-2026",
            help="OpenAlex publication year range (default: 2021-2026).",
        )
        parser.add_argument(
            "--backend",
            type=str,
            choices=["auto", "local", "openai"],
            default="local",
            help="Embedding backend for seed steps (default: local).",
        )
        parser.add_argument(
            "--batch-size",
            type=int,
            default=128,
            help="Embedding batch size for OpenAlex seed (default: 128).",
        )
        parser.add_argument(
            "--skip-openalex",
            action="store_true",
            help="Only seed local fixture data and skip OpenAlex even if API key exists.",
        )
        parser.add_argument(
            "--skip-verify",
            action="store_true",
            help="Skip verify_data_pipeline at the end.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        works_per_query = int(options["works_per_query"])
        authors_per_query = int(options["authors_per_query"])
        years = str(options["years"]).strip()
        backend = str(options["backend"]).strip().lower()
        batch_size = int(options["batch_size"])
        skip_openalex = bool(options["skip_openalex"])
        skip_verify = bool(options["skip_verify"])

        if works_per_query <= 0:
            raise CommandError("--works-per-query must be greater than 0.")
        if authors_per_query <= 0:
            raise CommandError("--authors-per-query must be greater than 0.")
        if batch_size <= 0:
            raise CommandError("--batch-size must be greater than 0.")
        if not years:
            raise CommandError("--years cannot be empty.")

        self.stdout.write(self.style.NOTICE("Step 1/4: Seeding local telecom fixture data..."))
        call_command("seed_demo_data", backend=backend, skip_graph_sync=True)

        openalex_enabled = bool(settings.OPENALEX_API_KEY) and not skip_openalex
        if openalex_enabled:
            self.stdout.write(self.style.NOTICE("Step 2/4: Ingesting additional OpenAlex data..."))
            for index, query in enumerate(DEMO_QUERIES, start=1):
                self.stdout.write(
                    self.style.HTTP_INFO(
                        f"  [{index}/{len(DEMO_QUERIES)}] query={query!r} "
                        f"works={works_per_query} authors={authors_per_query}"
                    )
                )
                call_command(
                    "seed_openalex",
                    works=works_per_query,
                    authors=authors_per_query,
                    query=query,
                    years=years,
                    topics=["telecom"],
                    backend=backend,
                    batch_size=batch_size,
                    skip_graph_sync=True,
                )
        else:
            reason = "--skip-openalex was provided" if skip_openalex else "OPENALEX_API_KEY missing"
            self.stdout.write(
                self.style.WARNING(
                    f"Step 2/4: Skipping OpenAlex ingestion ({reason}). "
                    "Using fixture-only data."
                )
            )

        self.stdout.write(self.style.NOTICE("Step 3/4: Syncing graph..."))
        call_command("sync_to_neo4j", include_collaborators=True)

        if skip_verify:
            self.stdout.write(
                self.style.WARNING("Step 4/4: Skipping verification (--skip-verify).")
            )
        else:
            self.stdout.write(self.style.NOTICE("Step 4/4: Running pipeline verification..."))
            call_command("verify_data_pipeline", query=DEMO_QUERIES[0])

        self.stdout.write(
            self.style.SUCCESS(
                "Interview data seed complete. Open /demo/ and try telecom queries "
                "from the left panel."
            )
        )
