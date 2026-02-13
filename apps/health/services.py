"""Health check services for app dependencies."""

from __future__ import annotations

from typing import Any, Literal, TypedDict

import redis
from django.conf import settings
from django.db import connection
from django.db.models import Q
from neo4j import GraphDatabase

from apps.documents.models import Author, Embedding, IngestionRun, IngestionStatus, Paper, Topic


class CheckResult(TypedDict):
    status: Literal["ok", "error"]
    detail: str


class HealthReport(TypedDict):
    status: Literal["ok", "degraded"]
    checks: dict[str, CheckResult]
    metrics: dict[str, Any]


class HealthCheckService:
    """Runs lightweight checks for each required dependency."""

    def check(self) -> HealthReport:
        checks: dict[str, CheckResult] = {
            "database": self._check_database(),
            "neo4j": self._check_neo4j(),
            "embeddings": self._check_embeddings_present(),
            "redis": self._check_redis(),
        }
        overall = "ok" if all(item["status"] == "ok" for item in checks.values()) else "degraded"
        return {"status": overall, "checks": checks, "metrics": self._collect_metrics()}

    @staticmethod
    def _check_database() -> CheckResult:
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
            return {"status": "ok", "detail": "database reachable"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "detail": f"database check failed: {exc}"}

    @staticmethod
    def _check_redis() -> CheckResult:
        client: redis.Redis[Any]
        try:
            client = redis.Redis.from_url(
                settings.REDIS_URL,
                socket_connect_timeout=2,
                socket_timeout=2,
            )
            client.ping()
            return {"status": "ok", "detail": "redis reachable"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "detail": f"redis check failed: {exc}"}

    @staticmethod
    def _check_neo4j() -> CheckResult:
        try:
            with GraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
                connection_timeout=3,
            ) as driver:
                driver.verify_connectivity()
            return {"status": "ok", "detail": "neo4j reachable"}
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "detail": f"neo4j check failed: {exc}"}

    @staticmethod
    def _check_embeddings_present() -> CheckResult:
        try:
            count = Embedding.objects.filter(embedding__isnull=False).count()
        except Exception as exc:  # noqa: BLE001
            return {"status": "error", "detail": f"embeddings check failed: {exc}"}

        if count > 0:
            return {"status": "ok", "detail": f"embeddings present ({count})"}

        return {"status": "error", "detail": "no embeddings present"}

    @staticmethod
    def _collect_metrics() -> dict[str, Any]:
        try:
            last_openalex_run = (
                IngestionRun.objects.filter(
                    status=IngestionStatus.SUCCESS,
                )
                .filter(
                    Q(query__icontains="openalex")
                    | Q(query__startswith="live_fetch:")
                    | Q(query__icontains="seed_openalex")
                )
                .order_by("-finished_at", "-id")
                .first()
            )
            return {
                "papers": Paper.objects.count(),
                "authors": Author.objects.count(),
                "topics": Topic.objects.count(),
                "last_openalex_sync_at": (
                    last_openalex_run.finished_at.isoformat()
                    if last_openalex_run and last_openalex_run.finished_at
                    else None
                ),
            }
        except Exception as exc:  # noqa: BLE001
            return {
                "papers": 0,
                "authors": 0,
                "topics": 0,
                "last_openalex_sync_at": None,
                "error": f"metrics collection failed: {exc}",
            }
