from __future__ import annotations

from typing import Any

from django.core.management import call_command
from django.core.management.base import BaseCommand


class Command(BaseCommand):
    help = "Backward-compatible alias for sync_to_neo4j."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--limit", type=int, help="Only sync up to N papers.")
        parser.add_argument(
            "--include-collaborators",
            action="store_true",
            help="Also create collaborator edges.",
        )
        parser.add_argument(
            "--progress-every",
            type=int,
            default=25,
            help="Print progress every N papers.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        kwargs: dict[str, Any] = {
            "limit": options.get("limit"),
            "progress_every": options.get("progress_every") or 25,
        }
        if options.get("include_collaborators"):
            kwargs["include_collaborators"] = True

        call_command("sync_to_neo4j", **kwargs)
