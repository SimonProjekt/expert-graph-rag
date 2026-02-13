from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from celery.result import AsyncResult
from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from apps.documents.models import Paper
from apps.documents.tasks import chunk_papers, embed_chunks


@dataclass(frozen=True)
class TaskBatchSummary:
    batches_total: int
    papers_total: int
    chunks_generated: int = 0
    chunks_created: int = 0
    chunks_updated: int = 0
    chunks_deleted: int = 0
    chunks_embedded: int = 0


class Command(BaseCommand):
    help = "Chunk papers and embed chunks through Celery tasks."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--batch",
            type=int,
            default=128,
            help="Number of papers per task batch (default: 128).",
        )
        parser.add_argument(
            "--workers",
            type=int,
            default=2,
            help="Maximum in-flight Celery task batches (default: 2).",
        )
        parser.add_argument(
            "--backend",
            type=str,
            choices=["auto", "local", "openai"],
            default="auto",
            help="Embedding backend selection (auto/local/openai).",
        )
        parser.add_argument(
            "--chunk-size",
            type=int,
            default=None,
            help="Override chunk token size for this run.",
        )
        parser.add_argument(
            "--overlap",
            type=int,
            default=None,
            help="Override chunk token overlap for this run.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        batch = int(options["batch"])
        workers = int(options["workers"])
        backend = str(options["backend"])
        chunk_size = options.get("chunk_size")
        overlap = options.get("overlap")

        if batch <= 0:
            raise CommandError("--batch must be greater than 0.")
        if workers <= 0:
            raise CommandError("--workers must be greater than 0.")

        paper_ids = list(Paper.objects.order_by("id").values_list("id", flat=True))
        if not paper_ids:
            self.stdout.write(self.style.WARNING("No papers found to process."))
            return

        batches = self._split_batches(paper_ids, batch)
        self.stdout.write(f"Scheduling {len(batches)} chunk task batches...")

        chunk_summary = self._run_batched_tasks(
            batches=batches,
            workers=workers,
            task_callable=lambda ids: chunk_papers.delay(
                paper_ids=ids,
                chunk_size=chunk_size,
                chunk_overlap=overlap,
            ),
            stage_name="chunk",
        )

        self.stdout.write(f"Scheduling {len(batches)} embedding task batches...")

        embed_summary = self._run_batched_tasks(
            batches=batches,
            workers=workers,
            task_callable=lambda ids: embed_chunks.delay(
                paper_ids=ids,
                batch_size=batch,
                backend_name=backend,
            ),
            stage_name="embed",
        )

        self.stdout.write(
            self.style.SUCCESS(
                "Pipeline complete: "
                f"papers={len(paper_ids)}, "
                f"chunks_generated={chunk_summary.chunks_generated}, "
                f"chunks_created={chunk_summary.chunks_created}, "
                f"chunks_updated={chunk_summary.chunks_updated}, "
                f"chunks_deleted={chunk_summary.chunks_deleted}, "
                f"chunks_embedded={embed_summary.chunks_embedded}"
            )
        )

    def _run_batched_tasks(
        self,
        *,
        batches: list[list[int]],
        workers: int,
        task_callable: Callable[[list[int]], AsyncResult],
        stage_name: str,
    ) -> TaskBatchSummary:
        in_flight: list[AsyncResult] = []
        completed = 0

        summary = TaskBatchSummary(
            batches_total=len(batches),
            papers_total=sum(len(batch) for batch in batches),
        )

        for batch in batches:
            in_flight.append(task_callable(batch))
            if len(in_flight) >= workers:
                summary = self._collect_one_result(
                    summary=summary,
                    in_flight=in_flight,
                    stage_name=stage_name,
                )
                completed += 1
                self.stdout.write(f"{stage_name}: completed {completed}/{len(batches)} batches")

        while in_flight:
            summary = self._collect_one_result(
                summary=summary,
                in_flight=in_flight,
                stage_name=stage_name,
            )
            completed += 1
            self.stdout.write(f"{stage_name}: completed {completed}/{len(batches)} batches")

        return summary

    def _collect_one_result(
        self,
        *,
        summary: TaskBatchSummary,
        in_flight: list[AsyncResult],
        stage_name: str,
    ) -> TaskBatchSummary:
        result = in_flight.pop(0)

        timeout = int(getattr(settings, "CELERY_TASK_TIME_LIMIT", 300)) + 60
        payload = result.get(timeout=timeout)

        if not isinstance(payload, dict):
            raise CommandError(
                f"{stage_name} task returned invalid payload type: "
                f"{type(payload).__name__}"
            )

        return TaskBatchSummary(
            batches_total=summary.batches_total,
            papers_total=summary.papers_total,
            chunks_generated=summary.chunks_generated + int(payload.get("chunks_generated", 0)),
            chunks_created=summary.chunks_created + int(payload.get("chunks_created", 0)),
            chunks_updated=summary.chunks_updated + int(payload.get("chunks_updated", 0)),
            chunks_deleted=summary.chunks_deleted + int(payload.get("chunks_deleted", 0)),
            chunks_embedded=summary.chunks_embedded + int(payload.get("chunks_embedded", 0)),
        )

    @staticmethod
    def _split_batches(items: list[int], batch_size: int) -> list[list[int]]:
        return [items[index : index + batch_size] for index in range(0, len(items), batch_size)]
