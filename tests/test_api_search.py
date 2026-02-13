from __future__ import annotations

from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.documents.embedding_backends import EmbeddingBackendError
from apps.documents.models import (
    Author,
    Authorship,
    Embedding,
    Paper,
    PaperTopic,
    SearchAudit,
    SecurityLevel,
    Topic,
)
from apps.documents.openalex import OpenAlexReadThroughResult


class StaticBackend:
    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [list(self._vector) for _ in texts]


class FailingBackend:
    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        _ = texts
        raise EmbeddingBackendError("openai embedding failed")


@pytest.mark.django_db
@override_settings(
    EMBEDDING_BACKEND="auto",
    OPENAI_API_KEY="invalid-openai-key",
    OPENALEX_LIVE_FETCH=False,
)
def test_search_falls_back_to_local_embeddings_when_primary_backend_fails(client) -> None:
    author = Author.objects.create(
        name="Fallback Author",
        external_id="author:fallback:001",
        institution_name="Fallback Lab",
    )
    paper = Paper.objects.create(
        title="Fallback Search Paper",
        abstract="A resilient search test paper.",
        external_id="paper:fallback:001",
        security_level=SecurityLevel.PUBLIC,
    )
    topic = Topic.objects.create(name="Fallback Topic", external_id="topic:fallback:001")
    Authorship.objects.create(author=author, paper=paper, author_order=1)
    PaperTopic.objects.create(paper=paper, topic=topic)

    vector = [0.9, 0.1, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    Embedding.objects.create(
        paper=paper,
        chunk_id=0,
        text_chunk="fallback search chunk",
        embedding=vector,
    )

    with patch(
        "apps.api.services.get_embedding_backend",
        side_effect=[FailingBackend(), StaticBackend(vector)],
    ) as backend_mock:
        response = client.get(
            "/api/search",
            {
                "query": "fallback search",
                "clearance": SecurityLevel.PUBLIC,
                "page": 1,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"]
    assert payload["results"][0]["title"] == "Fallback Search Paper"
    assert backend_mock.call_count == 2


@pytest.mark.django_db
def test_search_hides_confidential_content_for_public_clearance(client) -> None:
    author_public = Author.objects.create(
        name="Public Author",
        external_id="author:public:001",
        institution_name="Public Institute",
    )
    author_confidential = Author.objects.create(
        name="Confidential Author",
        external_id="author:confidential:001",
        institution_name="Restricted Labs",
    )
    topic = Topic.objects.create(name="Graph Retrieval", external_id="topic:graph-retrieval:001")

    public_paper = Paper.objects.create(
        title="Public Graph Patterns",
        abstract="General techniques for retrieval.",
        external_id="paper:public:001",
        security_level=SecurityLevel.PUBLIC,
    )
    confidential_paper = Paper.objects.create(
        title="Confidential Roadmap",
        abstract="Top secret architecture details.",
        external_id="paper:confidential:001",
        security_level=SecurityLevel.CONFIDENTIAL,
    )

    Authorship.objects.create(author=author_public, paper=public_paper, author_order=1)
    Authorship.objects.create(author=author_confidential, paper=confidential_paper, author_order=1)
    PaperTopic.objects.create(paper=public_paper, topic=topic)
    PaperTopic.objects.create(paper=confidential_paper, topic=topic)

    vector = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    Embedding.objects.create(
        paper=public_paper,
        chunk_id=0,
        text_chunk="Public chunk about graph retrieval techniques.",
        embedding=vector,
    )
    Embedding.objects.create(
        paper=confidential_paper,
        chunk_id=0,
        text_chunk="Confidential roadmap chunk with restricted architecture details.",
        embedding=vector,
    )

    with patch("apps.api.services.get_embedding_backend", return_value=StaticBackend(vector)):
        response = client.get(
            "/api/search",
            {
                "query": "roadmap architecture",
                "clearance": SecurityLevel.PUBLIC,
                "page": 1,
            },
            HTTP_X_CLIENT_ID="client-public",
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["redacted_count"] >= 1
    assert payload["hidden_count"] == payload["redacted_count"]
    assert payload["result_count"] == len(payload["results"])
    assert isinstance(payload["took_ms"], int)
    assert len(payload["results"]) == 1
    assert payload["results"][0]["title"] == public_paper.title
    assert payload["results"][0]["authors"] == ["Public Author"]
    assert payload["results"][0]["topics"] == ["Graph Retrieval"]

    serialized = response.content.decode("utf-8")
    assert confidential_paper.title not in serialized
    assert confidential_paper.abstract not in serialized

    audit = SearchAudit.objects.get()
    assert audit.endpoint == "/api/search"
    assert audit.clearance == SecurityLevel.PUBLIC
    assert audit.redacted_count >= 1
    assert audit.client_id == "client-public"


@pytest.mark.django_db
def test_search_hides_confidential_content_for_internal_clearance(client) -> None:
    author_internal = Author.objects.create(
        name="Internal Author",
        external_id="author:internal:001",
        institution_name="Internal Institute",
    )
    author_confidential = Author.objects.create(
        name="Confidential Author",
        external_id="author:confidential:002",
        institution_name="Restricted Labs",
    )
    topic = Topic.objects.create(name="RAG", external_id="topic:rag:001")

    internal_paper = Paper.objects.create(
        title="Internal Operations Guide",
        abstract="Internal-only operating procedures.",
        external_id="paper:internal:001",
        security_level=SecurityLevel.INTERNAL,
    )
    confidential_paper = Paper.objects.create(
        title="Confidential Incident Report",
        abstract="Confidential incident timeline and names.",
        external_id="paper:confidential:002",
        security_level=SecurityLevel.CONFIDENTIAL,
    )

    Authorship.objects.create(author=author_internal, paper=internal_paper, author_order=1)
    Authorship.objects.create(author=author_confidential, paper=confidential_paper, author_order=1)
    PaperTopic.objects.create(paper=internal_paper, topic=topic)
    PaperTopic.objects.create(paper=confidential_paper, topic=topic)

    vector = [0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    Embedding.objects.create(
        paper=internal_paper,
        chunk_id=0,
        text_chunk="Internal chunk for operations and response.",
        embedding=vector,
    )
    Embedding.objects.create(
        paper=confidential_paper,
        chunk_id=0,
        text_chunk="Confidential chunk containing restricted timeline details.",
        embedding=vector,
    )

    with patch("apps.api.services.get_embedding_backend", return_value=StaticBackend(vector)):
        response = client.get(
            "/api/search",
            {
                "query": "incident response",
                "clearance": SecurityLevel.INTERNAL,
                "page": 1,
            },
        )

    assert response.status_code == 200
    payload = response.json()
    assert payload["redacted_count"] >= 1
    assert payload["hidden_count"] == payload["redacted_count"]
    assert payload["result_count"] == len(payload["results"])
    assert isinstance(payload["took_ms"], int)
    assert len(payload["results"]) == 1
    assert payload["results"][0]["title"] == internal_paper.title
    assert payload["results"][0]["authors"] == ["Internal Author"]
    assert payload["results"][0]["topics"] == ["RAG"]

    serialized = response.content.decode("utf-8")
    assert confidential_paper.title not in serialized
    assert confidential_paper.abstract not in serialized


@pytest.mark.django_db
@override_settings(OPENALEX_LIVE_FETCH=True, OPENALEX_API_KEY="live-fetch-key")
def test_search_read_through_fetch_can_backfill_local_results(client) -> None:
    query_vector = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]

    def fake_live_fetch(self, *, query: str, current_result_count: int, page: int):  # noqa: ANN001
        _ = self
        _ = query
        _ = current_result_count
        _ = page
        author = Author.objects.create(
            name="Live Fetch Author",
            external_id="author:live-fetch:001",
            institution_name="Live Labs",
        )
        topic = Topic.objects.create(name="Live Topic", external_id="topic:live-fetch:001")
        paper = Paper.objects.create(
            title="Live Fetch Paper",
            abstract="Paper inserted by mocked read-through fetch.",
            external_id="paper:live-fetch:001",
            security_level=SecurityLevel.PUBLIC,
        )
        Authorship.objects.create(author=author, paper=paper, author_order=1)
        PaperTopic.objects.create(paper=paper, topic=topic)
        Embedding.objects.create(
            paper=paper,
            chunk_id=0,
            text_chunk="live fetch searchable chunk",
            embedding=query_vector,
        )
        return OpenAlexReadThroughResult(
            enabled=True,
            attempted=True,
            reason="fetched",
            works_processed=1,
            papers_touched=1,
            chunks_embedded=1,
            duration_ms=20,
        )

    with patch("apps.api.services.get_embedding_backend", return_value=StaticBackend(query_vector)):
        with patch(
            "apps.api.services.OpenAlexReadThroughService.fetch_if_needed",
            new=fake_live_fetch,
        ):
            response = client.get(
                "/api/search",
                {
                    "query": "live fetch query",
                    "clearance": SecurityLevel.PUBLIC,
                    "page": 1,
                },
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["results"]
    assert payload["results"][0]["title"] == "Live Fetch Paper"
    assert payload["hidden_count"] == payload["redacted_count"]
    assert payload["result_count"] == len(payload["results"])
    assert payload["live_fetch"]["attempted"] is True
    assert payload["live_fetch"]["reason"] == "fetched"


@pytest.mark.django_db
@override_settings(
    SEARCH_MAX_CHUNK_SCAN=1,
    SEARCH_GRAPH_SEED_PAPERS=1,
    SEARCH_GRAPH_EXPANSION_LIMIT=10,
    SEARCH_GRAPH_HOP_LIMIT=2,
    OPENALEX_LIVE_FETCH=False,
)
def test_search_graph_expansion_returns_connected_papers_with_explainability(client) -> None:
    topic = Topic.objects.create(name="Knowledge Graph", external_id="topic:kg:001")
    author_a = Author.objects.create(
        name="Author A",
        external_id="author:graph:001",
        institution_name="Graph Labs",
    )
    author_b = Author.objects.create(
        name="Author B",
        external_id="author:graph:002",
        institution_name="Graph Labs",
    )

    seed_paper = Paper.objects.create(
        title="Semantic Retrieval for Graph RAG",
        abstract="Strong semantic match.",
        external_id="paper:graph:seed:001",
        security_level=SecurityLevel.PUBLIC,
    )
    expanded_paper = Paper.objects.create(
        title="Connected Topic Expansion for Expert Discovery",
        abstract="Lower semantic match but graph-connected.",
        external_id="paper:graph:expanded:001",
        security_level=SecurityLevel.PUBLIC,
    )

    Authorship.objects.create(author=author_a, paper=seed_paper, author_order=1)
    Authorship.objects.create(author=author_b, paper=expanded_paper, author_order=1)
    PaperTopic.objects.create(paper=seed_paper, topic=topic)
    PaperTopic.objects.create(paper=expanded_paper, topic=topic)

    query_vector = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    Embedding.objects.create(
        paper=seed_paper,
        chunk_id=0,
        text_chunk="Semantic retrieval graph rag",
        embedding=query_vector,
    )
    Embedding.objects.create(
        paper=expanded_paper,
        chunk_id=0,
        text_chunk="Connected expansion techniques",
        embedding=[0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    )

    with patch("apps.api.services.get_embedding_backend", return_value=StaticBackend(query_vector)):
        response = client.get(
            "/api/search",
            {
                "query": "graph rag expansion",
                "clearance": SecurityLevel.PUBLIC,
                "page": 1,
            },
        )

    assert response.status_code == 200
    payload = response.json()

    title_to_result = {row["title"]: row for row in payload["results"]}
    assert seed_paper.title in title_to_result
    assert expanded_paper.title in title_to_result

    expanded = title_to_result[expanded_paper.title]
    assert expanded["source"] in {"graph_hop_1", "graph_hop_2"}
    assert expanded["graph_hop_distance"] in {1, 2}
    assert "Ranked because" in expanded["why_matched"]
    assert "query -> seed_paper:" in expanded["graph_path"]
    assert set(expanded["score_breakdown"].keys()) == {
        "semantic_relevance",
        "graph_authority",
        "graph_centrality",
    }
