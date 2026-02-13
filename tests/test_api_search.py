from __future__ import annotations

from unittest.mock import patch

import pytest

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


class StaticBackend:
    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [list(self._vector) for _ in texts]


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
    assert len(payload["results"]) == 1
    assert payload["results"][0]["title"] == internal_paper.title
    assert payload["results"][0]["authors"] == ["Internal Author"]
    assert payload["results"][0]["topics"] == ["RAG"]

    serialized = response.content.decode("utf-8")
    assert confidential_paper.title not in serialized
    assert confidential_paper.abstract not in serialized
