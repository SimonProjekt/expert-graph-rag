from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from uuid import uuid4

from django.conf import settings
from django.db import DatabaseError
from django.utils import timezone
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from apps.api.services import SearchBackendError, SearchExecutionError, SearchService
from apps.documents.models import (
    Author,
    Authorship,
    Embedding,
    IngestionRun,
    Paper,
    PaperTopic,
    SecurityLevel,
    Topic,
)


@dataclass(frozen=True)
class VerificationCheck:
    name: str
    passed: bool
    details: dict[str, object] = field(default_factory=dict)
    error: str | None = None


@dataclass(frozen=True)
class VerificationReport:
    started_at: datetime
    finished_at: datetime
    checks: list[VerificationCheck]

    @property
    def ok(self) -> bool:
        return all(check.passed for check in self.checks)


@dataclass(frozen=True)
class PipelineSnapshot:
    counts: dict[str, int]
    embedding_stats: dict[str, float | int]
    neo4j_stats: dict[str, int]
    neo4j_error: str | None
    last_ingestion_run_at: datetime | None
    last_embed_run_at: datetime | None
    last_graph_sync_at: datetime | None
    status: str


class DataPipelineVerifier:
    """Runs data integration checks across Postgres, embeddings, APIs, and Neo4j."""

    def run(self, *, sample_query: str | None = None) -> VerificationReport:
        started_at = timezone.now()
        checks = [
            self._check_postgres_counts(),
            self._check_embeddings(),
            self._check_search_returns_results(sample_query=sample_query),
            self._check_access_control_fixture(),
            self._check_neo4j_graph(),
        ]
        finished_at = timezone.now()

        return VerificationReport(
            started_at=started_at,
            finished_at=finished_at,
            checks=checks,
        )

    def collect_snapshot(self) -> PipelineSnapshot:
        counts = self._collect_postgres_counts()
        embedding_stats = self._collect_embedding_stats()

        neo4j_error: str | None = None
        neo4j_stats: dict[str, int] = {
            "papers": 0,
            "authors": 0,
            "topics": 0,
            "wrote_rels": 0,
            "has_topic_rels": 0,
        }
        last_graph_sync_at: datetime | None = None

        try:
            neo4j_stats, last_graph_sync_at = self._collect_neo4j_graph_stats()
        except RuntimeError as exc:
            neo4j_error = str(exc)

        last_ingestion_run = IngestionRun.objects.order_by("-started_at", "-id").first()
        last_embed_run = Embedding.objects.order_by("-created_at", "-id").first()

        status = "healthy"
        if counts["papers"] == 0 or embedding_stats["non_null_vectors"] == 0:
            status = "degraded"
        if neo4j_error is not None:
            status = "degraded"
        if neo4j_error is None and (
            neo4j_stats["papers"] == 0
            or neo4j_stats["wrote_rels"] == 0
            or neo4j_stats["has_topic_rels"] == 0
        ):
            status = "degraded"

        return PipelineSnapshot(
            counts=counts,
            embedding_stats=embedding_stats,
            neo4j_stats=neo4j_stats,
            neo4j_error=neo4j_error,
            last_ingestion_run_at=(
                last_ingestion_run.finished_at or last_ingestion_run.started_at
                if last_ingestion_run is not None
                else None
            ),
            last_embed_run_at=(last_embed_run.created_at if last_embed_run is not None else None),
            last_graph_sync_at=last_graph_sync_at,
            status=status,
        )

    @staticmethod
    def format_report(report: VerificationReport) -> str:
        lines = [
            "Data Integration Verification Report",
            (
                f"Started: {report.started_at.isoformat()} | "
                f"Finished: {report.finished_at.isoformat()}"
            ),
            "",
        ]

        for check in report.checks:
            marker = "PASS" if check.passed else "FAIL"
            lines.append(f"[{marker}] {check.name}")
            if check.details:
                for key in sorted(check.details):
                    value = check.details[key]
                    if isinstance(value, (dict, list, tuple)):
                        rendered = json.dumps(value, sort_keys=True)
                    else:
                        rendered = str(value)
                    lines.append(f"  - {key}: {rendered}")
            if check.error:
                lines.append(f"  - error: {check.error}")
            lines.append("")

        lines.append(f"Overall: {'PASS' if report.ok else 'FAIL'}")
        return "\n".join(lines)

    def _check_postgres_counts(self) -> VerificationCheck:
        try:
            counts = self._collect_postgres_counts()
        except DatabaseError as exc:
            return VerificationCheck(
                name="Postgres core table counts",
                passed=False,
                error=f"database error: {exc}",
            )

        passed = all(value > 0 for value in counts.values())
        return VerificationCheck(
            name="Postgres core table counts",
            passed=passed,
            details=counts,
            error=None if passed else "one or more tables have zero rows",
        )

    def _check_embeddings(self) -> VerificationCheck:
        try:
            stats = self._collect_embedding_stats()
        except DatabaseError as exc:
            return VerificationCheck(
                name="Embedding coverage",
                passed=False,
                error=f"database error: {exc}",
            )

        passed = (
            stats["total_chunks"] > 0
            and stats["non_null_vectors"] > 0
            and stats["avg_chunks_per_paper"] >= 1.0
        )
        return VerificationCheck(
            name="Embedding coverage",
            passed=passed,
            details=stats,
            error=None if passed else "embedding coverage is incomplete",
        )

    def _check_search_returns_results(self, *, sample_query: str | None) -> VerificationCheck:
        query = (sample_query or self._resolve_sample_query()).strip()
        service = SearchService()

        try:
            payload = service.search(
                query=query,
                clearance=SecurityLevel.PUBLIC,
                page=1,
                endpoint="/verify_data_pipeline/search",
                client_id="verify_data_pipeline",
                user_role=SecurityLevel.PUBLIC,
            )
        except (SearchExecutionError, SearchBackendError) as exc:
            return VerificationCheck(
                name="Search service returns results",
                passed=False,
                details={"query": query},
                error=str(exc),
            )

        result_count = len(payload.get("results", []))
        passed = result_count > 0

        return VerificationCheck(
            name="Search service returns results",
            passed=passed,
            details={
                "query": query,
                "result_count": result_count,
                "redacted_count": payload.get("redacted_count", 0),
            },
            error=None if passed else "search returned no results",
        )

    def _check_access_control_fixture(self) -> VerificationCheck:
        suffix = uuid4().hex[:10]
        topic_external_id = f"verify:topic:{suffix}"
        public_author_external_id = f"verify:author:public:{suffix}"
        confidential_author_external_id = f"verify:author:confidential:{suffix}"
        public_paper_external_id = f"verify:paper:public:{suffix}"
        confidential_paper_external_id = f"verify:paper:confidential:{suffix}"

        confidential_title = f"Confidential Pipeline Strategy {suffix}"
        confidential_abstract = f"Confidential abstract marker {suffix}"

        try:
            topic = Topic.objects.create(
                name=f"Verify Topic {suffix}",
                external_id=topic_external_id,
            )
            public_author = Author.objects.create(
                name=f"Verify Public Author {suffix}",
                external_id=public_author_external_id,
                institution_name="Verification Lab",
            )
            confidential_author = Author.objects.create(
                name=f"Verify Confidential Author {suffix}",
                external_id=confidential_author_external_id,
                institution_name="Verification Lab",
            )

            public_paper = Paper.objects.create(
                title=f"Public Integration Notes {suffix}",
                abstract=f"Public abstract marker {suffix}",
                external_id=public_paper_external_id,
                security_level=SecurityLevel.PUBLIC,
            )
            confidential_paper = Paper.objects.create(
                title=confidential_title,
                abstract=confidential_abstract,
                external_id=confidential_paper_external_id,
                security_level=SecurityLevel.CONFIDENTIAL,
            )

            Authorship.objects.create(author=public_author, paper=public_paper, author_order=1)
            Authorship.objects.create(
                author=confidential_author,
                paper=confidential_paper,
                author_order=1,
            )
            PaperTopic.objects.create(paper=public_paper, topic=topic)
            PaperTopic.objects.create(paper=confidential_paper, topic=topic)

            vector = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
            Embedding.objects.create(
                paper=public_paper,
                chunk_id=0,
                text_chunk=f"public chunk marker {suffix}",
                embedding=vector,
            )
            Embedding.objects.create(
                paper=confidential_paper,
                chunk_id=0,
                text_chunk=f"confidential chunk marker {suffix}",
                embedding=vector,
            )

            class DeterministicSearchService(SearchService):
                def _embed_query(self, query: str) -> list[float]:
                    return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

            payload = DeterministicSearchService().search(
                query=f"strategy {suffix}",
                clearance=SecurityLevel.PUBLIC,
                page=1,
                endpoint="/verify_data_pipeline/access_control",
                client_id="verify_data_pipeline",
                user_role=SecurityLevel.PUBLIC,
            )

            serialized = json.dumps(payload)
            leaked = confidential_title in serialized or confidential_abstract in serialized
            passed = (not leaked) and payload.get("redacted_count", 0) >= 1

            return VerificationCheck(
                name="Access control leakage prevention",
                passed=passed,
                details={
                    "fixture_suffix": suffix,
                    "redacted_count": payload.get("redacted_count", 0),
                    "results_count": len(payload.get("results", [])),
                },
                error=None if passed else "confidential content leaked at PUBLIC clearance",
            )
        except DatabaseError as exc:
            return VerificationCheck(
                name="Access control leakage prevention",
                passed=False,
                error=f"database error: {exc}",
            )
        finally:
            Paper.objects.filter(
                external_id__in=[public_paper_external_id, confidential_paper_external_id]
            ).delete()
            Author.objects.filter(
                external_id__in=[public_author_external_id, confidential_author_external_id]
            ).delete()
            Topic.objects.filter(external_id=topic_external_id).delete()

    def _check_neo4j_graph(self) -> VerificationCheck:
        try:
            stats, _last_sync = self._collect_neo4j_graph_stats()
        except RuntimeError as exc:
            return VerificationCheck(
                name="Neo4j graph shape",
                passed=False,
                error=str(exc),
            )

        passed = (
            stats["papers"] > 0
            and stats["authors"] > 0
            and stats["topics"] > 0
            and stats["wrote_rels"] > 0
            and stats["has_topic_rels"] > 0
        )

        return VerificationCheck(
            name="Neo4j graph shape",
            passed=passed,
            details=stats,
            error=None if passed else "graph nodes/relationships are missing",
        )

    def _collect_postgres_counts(self) -> dict[str, int]:
        return {
            "papers": Paper.objects.count(),
            "authors": Author.objects.count(),
            "topics": Topic.objects.count(),
            "authorships": Authorship.objects.count(),
            "paper_topics": PaperTopic.objects.count(),
        }

    def _collect_embedding_stats(self) -> dict[str, float | int]:
        paper_count = Paper.objects.count()
        total_chunks = Embedding.objects.count()
        non_null_vectors = Embedding.objects.filter(embedding__isnull=False).count()
        avg_chunks_per_paper = (float(total_chunks) / float(paper_count)) if paper_count else 0.0
        return {
            "total_chunks": total_chunks,
            "non_null_vectors": non_null_vectors,
            "avg_chunks_per_paper": round(avg_chunks_per_paper, 3),
        }

    def _collect_neo4j_graph_stats(self) -> tuple[dict[str, int], datetime | None]:
        try:
            with GraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
                connection_timeout=5,
            ) as driver:
                driver.verify_connectivity()
                with driver.session() as session:
                    stats = {
                        "papers": self._run_count_query(
                            session,
                            "MATCH (p:Paper) RETURN count(p) AS value",
                        ),
                        "authors": self._run_count_query(
                            session,
                            "MATCH (a:Author) RETURN count(a) AS value",
                        ),
                        "topics": self._run_count_query(
                            session,
                            "MATCH (t:Topic) RETURN count(t) AS value",
                        ),
                        "wrote_rels": self._run_count_query(
                            session,
                            "MATCH ()-[r:WROTE]->() RETURN count(r) AS value",
                        ),
                        "has_topic_rels": self._run_count_query(
                            session,
                            "MATCH ()-[r:HAS_TOPIC]->() RETURN count(r) AS value",
                        ),
                    }
                    last_sync = self._run_datetime_query(
                        session,
                        "MATCH (p:Paper) RETURN max(p.updated_at) AS value",
                    )
        except (Neo4jError, OSError, ValueError) as exc:
            raise RuntimeError(f"neo4j verification failed: {exc}") from exc

        return stats, last_sync

    @staticmethod
    def _run_count_query(session, query: str) -> int:
        record = session.run(query).single()
        if record is None:
            return 0
        return int(record.get("value") or 0)

    @staticmethod
    def _run_datetime_query(session, query: str) -> datetime | None:
        record = session.run(query).single()
        if record is None:
            return None

        raw_value = record.get("value")
        if raw_value is None:
            return None

        # neo4j.time.DateTime exposes to_native().
        to_native = getattr(raw_value, "to_native", None)
        if callable(to_native):
            native = to_native()
            if isinstance(native, datetime):
                return native
            return None

        if isinstance(raw_value, datetime):
            return raw_value
        return None

    @staticmethod
    def _resolve_sample_query() -> str:
        paper = Paper.objects.order_by("-published_date", "id").only("title").first()
        if paper is None or not paper.title.strip():
            return "graph retrieval"

        words = [token for token in paper.title.split() if token]
        if not words:
            return "graph retrieval"
        return " ".join(words[:4])
