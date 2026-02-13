"""Neo4j synchronization and graph metrics services."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from itertools import combinations
from typing import Callable

from django.conf import settings
from django.db import DatabaseError
from django.db.models import Prefetch
from neo4j import GraphDatabase
from neo4j.exceptions import Neo4jError

from apps.documents.models import Author, Authorship, Paper, PaperTopic

logger = logging.getLogger(__name__)


class GraphSyncError(Exception):
    """Raised when syncing papers into Neo4j fails."""


class GraphMetricsError(Exception):
    """Raised when computing graph centrality metrics fails."""


class GraphMetricsGDSUnavailable(GraphMetricsError):
    """Raised when GDS procedures are unavailable and fallback is required."""


ProgressCallback = Callable[[int, int], None]


@dataclass(frozen=True)
class GraphSyncResult:
    papers_total: int
    papers_synced: int
    relationships_synced: int
    collaborators_synced: int


@dataclass(frozen=True)
class GraphMetricsResult:
    method: str
    authors_updated: int
    authors_total: int


class GraphSyncService:
    """Syncs Author/Paper/Topic entities and relations into Neo4j idempotently."""

    def sync_to_neo4j(
        self,
        *,
        limit: int | None = None,
        include_collaborators: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> GraphSyncResult:
        if limit is not None and limit <= 0:
            raise GraphSyncError("limit must be greater than zero.")

        try:
            queryset = (
                Paper.objects.order_by("id")
                .prefetch_related(
                    Prefetch(
                        "authorships",
                        queryset=Authorship.objects.select_related("author").order_by(
                            "author_order"
                        ),
                    ),
                    Prefetch("paper_topics", queryset=PaperTopic.objects.select_related("topic")),
                )
            )
            if limit is not None:
                queryset = queryset[:limit]
            papers = list(queryset)
        except DatabaseError as exc:
            logger.exception("Failed to fetch papers for graph sync")
            raise GraphSyncError("Failed to fetch papers for graph sync.") from exc

        if not papers:
            return GraphSyncResult(
                papers_total=0,
                papers_synced=0,
                relationships_synced=0,
                collaborators_synced=0,
            )

        total = len(papers)
        synced_papers = 0
        synced_relationships = 0
        synced_collaborators = 0
        try:
            with GraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
                connection_timeout=5,
            ) as driver:
                driver.verify_connectivity()

                with driver.session() as session:
                    session.execute_write(self._ensure_constraints)
                    for paper in papers:
                        author_rows = [
                            {
                                "external_id": authorship.author.external_id,
                                "name": authorship.author.name,
                                "institution_name": authorship.author.institution_name,
                                "author_order": authorship.author_order,
                            }
                            for authorship in paper.authorships.all()
                            if authorship.author.external_id
                        ]
                        topic_rows = [
                            {
                                "external_id": paper_topic.topic.external_id,
                                "name": paper_topic.topic.name,
                            }
                            for paper_topic in paper.paper_topics.all()
                            if paper_topic.topic.external_id
                        ]
                        collaborator_rows = self._build_collaborator_rows(author_rows)

                        session.execute_write(
                            self._upsert_paper_graph,
                            paper_external_id=paper.external_id,
                            title=paper.title,
                            security_level=paper.security_level,
                            published_date=(
                                paper.published_date.isoformat()
                                if paper.published_date
                                else None
                            ),
                            authors=author_rows,
                            topics=topic_rows,
                            collaborators=collaborator_rows if include_collaborators else [],
                            include_collaborators=include_collaborators,
                        )

                        synced_papers += 1
                        synced_relationships += len(author_rows) + len(topic_rows)
                        if include_collaborators:
                            synced_collaborators += len(collaborator_rows)

                        if progress_callback is not None:
                            progress_callback(synced_papers, total)
        except Neo4jError as exc:
            logger.exception("Neo4j query failed during graph sync")
            raise GraphSyncError("Neo4j operation failed during graph synchronization.") from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected graph sync failure")
            raise GraphSyncError(f"Unexpected graph sync failure: {exc}") from exc

        return GraphSyncResult(
            papers_total=total,
            papers_synced=synced_papers,
            relationships_synced=synced_relationships,
            collaborators_synced=synced_collaborators,
        )

    def sync_documents(
        self,
        *,
        limit: int | None = None,
        include_collaborators: bool = False,
        progress_callback: ProgressCallback | None = None,
    ) -> GraphSyncResult:
        """Backward-compatible alias."""

        return self.sync_to_neo4j(
            limit=limit,
            include_collaborators=include_collaborators,
            progress_callback=progress_callback,
        )

    @staticmethod
    def _build_collaborator_rows(authors: list[dict[str, str]]) -> list[dict[str, str]]:
        unique_ids = sorted(
            {author["external_id"] for author in authors if author.get("external_id")}
        )
        pairs = combinations(unique_ids, 2)
        return [
            {
                "author_a_external_id": author_a,
                "author_b_external_id": author_b,
            }
            for author_a, author_b in pairs
        ]

    @staticmethod
    def _ensure_constraints(tx) -> None:
        tx.run(
            "CREATE CONSTRAINT author_external_id IF NOT EXISTS "
            "FOR (a:Author) REQUIRE a.external_id IS UNIQUE"
        )
        tx.run(
            "CREATE CONSTRAINT paper_external_id IF NOT EXISTS "
            "FOR (p:Paper) REQUIRE p.external_id IS UNIQUE"
        )
        tx.run(
            "CREATE CONSTRAINT topic_external_id IF NOT EXISTS "
            "FOR (t:Topic) REQUIRE t.external_id IS UNIQUE"
        )

    @staticmethod
    def _upsert_paper_graph(
        tx,
        *,
        paper_external_id: str,
        title: str,
        security_level: str,
        published_date: str | None,
        authors: list[dict[str, str | int]],
        topics: list[dict[str, str]],
        collaborators: list[dict[str, str]],
        include_collaborators: bool,
    ) -> None:
        tx.run(
            """
            MERGE (p:Paper {external_id: $paper_external_id})
            SET p.title = $title,
                p.security_level = $security_level,
                p.published_date = $published_date,
                p.updated_at = datetime()

            WITH p
            OPTIONAL MATCH (p)<-[old_w:WROTE]-(:Author)
            DELETE old_w

            WITH p
            OPTIONAL MATCH (p)-[old_t:HAS_TOPIC]->(:Topic)
            DELETE old_t

            WITH p
            FOREACH (author IN $authors |
                MERGE (a:Author {external_id: author.external_id})
                SET a.name = author.name,
                    a.institution_name = author.institution_name,
                    a.updated_at = datetime()
                MERGE (a)-[w:WROTE]->(p)
                SET w.author_order = author.author_order
            )

            WITH p
            FOREACH (topic IN $topics |
                MERGE (t:Topic {external_id: topic.external_id})
                SET t.name = topic.name,
                    t.updated_at = datetime()
                MERGE (p)-[:HAS_TOPIC]->(t)
            )

            WITH p
            FOREACH (pair IN CASE WHEN $include_collaborators THEN $collaborators ELSE [] END |
                MERGE (a1:Author {external_id: pair.author_a_external_id})
                MERGE (a2:Author {external_id: pair.author_b_external_id})
                MERGE (a1)-[:COLLABORATED_WITH]->(a2)
            )
            """,
            paper_external_id=paper_external_id,
            title=title,
            security_level=security_level,
            published_date=published_date,
            authors=authors,
            topics=topics,
            collaborators=collaborators,
            include_collaborators=include_collaborators,
        )


class GraphMetricsService:
    """Computes author centrality from Neo4j and stores it on Author."""

    _GRAPH_NAME = "expert_graph_rag_author_metrics"

    def compute_author_centrality(self, *, reset_missing: bool = True) -> GraphMetricsResult:
        try:
            with GraphDatabase.driver(
                settings.NEO4J_URI,
                auth=(settings.NEO4J_USER, settings.NEO4J_PASSWORD),
                connection_timeout=5,
            ) as driver:
                driver.verify_connectivity()

                with driver.session() as session:
                    try:
                        scores = self._compute_gds_pagerank(session)
                        method = "gds_pagerank"
                    except GraphMetricsGDSUnavailable:
                        logger.info(
                            "GDS unavailable in Neo4j; falling back to degree centrality."
                        )
                        scores = self._compute_degree_centrality(session)
                        method = "degree_centrality"
        except Neo4jError as exc:
            logger.exception("Neo4j error while computing graph metrics")
            raise GraphMetricsError("Neo4j error while computing graph metrics.") from exc
        except Exception as exc:  # noqa: BLE001
            logger.exception("Unexpected error while computing graph metrics")
            raise GraphMetricsError(f"Unexpected graph metrics failure: {exc}") from exc

        try:
            updated = self._persist_scores(scores=scores, reset_missing=reset_missing)
        except DatabaseError as exc:
            logger.exception("Failed to persist author centrality scores")
            raise GraphMetricsError("Failed to persist author centrality scores.") from exc

        return GraphMetricsResult(
            method=method,
            authors_updated=updated,
            authors_total=Author.objects.count(),
        )

    def _compute_gds_pagerank(self, session) -> dict[str, float]:
        graph_name = self._GRAPH_NAME

        try:
            session.run("CALL gds.version() YIELD version RETURN version LIMIT 1").consume()
        except Neo4jError as exc:
            if self._is_procedure_missing(exc):
                raise GraphMetricsGDSUnavailable("GDS is not available.") from exc
            raise

        try:
            self._drop_projected_graph_if_exists(session, graph_name)

            session.run(
                """
                CALL gds.graph.project.cypher(
                    $graph_name,
                    'MATCH (a:Author) RETURN id(a) AS id, a.external_id AS external_id',
                    'MATCH (a1:Author)-[:WROTE]->(:Paper)<-[:WROTE]-(a2:Author)
                     WHERE id(a1) <> id(a2)
                     RETURN id(a1) AS source, id(a2) AS target'
                )
                YIELD graphName
                RETURN graphName
                """,
                graph_name=graph_name,
            ).consume()

            records = session.run(
                """
                CALL gds.pageRank.stream($graph_name)
                YIELD nodeId, score
                RETURN gds.util.asNode(nodeId).external_id AS external_id, score
                """,
                graph_name=graph_name,
            )
            return self._records_to_score_map(records)
        except Neo4jError as exc:
            if self._is_procedure_missing(exc):
                raise GraphMetricsGDSUnavailable("GDS procedures are unavailable.") from exc
            raise
        finally:
            try:
                self._drop_projected_graph_if_exists(session, graph_name)
            except Neo4jError:
                logger.debug("Ignoring failure while dropping projected GDS graph.", exc_info=True)

    @staticmethod
    def _compute_degree_centrality(session) -> dict[str, float]:
        records = session.run(
            """
            MATCH (a:Author)
            OPTIONAL MATCH (a)-[:WROTE]->(:Paper)<-[:WROTE]-(co:Author)
            WHERE co <> a
            WITH a.external_id AS external_id, count(DISTINCT co) AS degree
            RETURN external_id, toFloat(degree) AS score
            """
        )
        return GraphMetricsService._records_to_score_map(records)

    @staticmethod
    def _records_to_score_map(records) -> dict[str, float]:
        score_map: dict[str, float] = {}
        for record in records:
            external_id = record.get("external_id")
            raw_score = record.get("score")
            if not isinstance(external_id, str) or not external_id:
                continue
            if raw_score is None:
                continue
            score_map[external_id] = float(raw_score)
        return score_map

    @staticmethod
    def _drop_projected_graph_if_exists(session, graph_name: str) -> None:
        exists_record = session.run(
            """
            CALL gds.graph.exists($graph_name)
            YIELD exists
            RETURN exists
            """,
            graph_name=graph_name,
        ).single()

        exists = bool(exists_record and exists_record.get("exists"))
        if not exists:
            return

        session.run(
            """
            CALL gds.graph.drop($graph_name)
            YIELD graphName
            RETURN graphName
            """,
            graph_name=graph_name,
        ).consume()

    @staticmethod
    def _persist_scores(*, scores: dict[str, float], reset_missing: bool) -> int:
        authors = list(Author.objects.all().only("id", "external_id", "centrality_score"))
        if not authors:
            return 0

        updated_rows: list[Author] = []
        seen_external_ids: set[str] = set()

        for author in authors:
            external_id = author.external_id
            if not external_id:
                continue

            if external_id in scores:
                next_score = float(scores[external_id])
                seen_external_ids.add(external_id)
            elif reset_missing:
                next_score = 0.0
            else:
                continue

            if author.centrality_score == next_score:
                continue

            author.centrality_score = next_score
            updated_rows.append(author)

        if updated_rows:
            Author.objects.bulk_update(updated_rows, ["centrality_score"])
        return len(updated_rows)

    @staticmethod
    def _is_procedure_missing(exc: Neo4jError) -> bool:
        code = str(getattr(exc, "code", "") or "")
        message = str(exc)
        return (
            "Procedure.ProcedureNotFound" in code
            or "UnknownProcedure" in code
            or "gds." in message and "no procedure with the name" in message.lower()
        )
