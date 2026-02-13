from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.documents.models import (
    Author,
    Authorship,
    Embedding,
    Paper,
    PaperTopic,
    SecurityLevel,
    Topic,
)


class StaticBackend:
    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [list(self._vector) for _ in texts]


@pytest.fixture
def query_vector() -> list[float]:
    return [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]


@pytest.fixture
def patched_expert_backend(query_vector: list[float]):
    with patch(
        "apps.api.experts.get_embedding_backend",
        return_value=StaticBackend(query_vector),
    ):
        yield


@pytest.mark.django_db
def test_experts_ranking_uses_quality_not_only_paper_count(
    client,
    patched_expert_backend,
) -> None:
    topic_graph = Topic.objects.create(name="Graph", external_id="topic:graph:001")
    topic_search = Topic.objects.create(name="Search", external_id="topic:search:001")

    high_volume_author = Author.objects.create(
        name="High Volume Expert",
        external_id="author:high-volume:001",
        institution_name="Archive Labs",
    )
    high_quality_author = Author.objects.create(
        name="High Quality Expert",
        external_id="author:high-quality:001",
        institution_name="Research Labs",
    )

    old_dates = [date(2019, 1, 1), date(2018, 6, 1), date(2017, 3, 1)]
    for index, published_date in enumerate(old_dates, start=1):
        paper = Paper.objects.create(
            title=f"Legacy Retrieval {index}",
            abstract="Legacy retrieval approaches.",
            external_id=f"paper:legacy:{index:03d}",
            published_date=published_date,
            security_level=SecurityLevel.PUBLIC,
        )
        Authorship.objects.create(author=high_volume_author, paper=paper, author_order=1)
        PaperTopic.objects.create(paper=paper, topic=topic_graph)
        Embedding.objects.create(
            paper=paper,
            chunk_id=0,
            text_chunk="Legacy retrieval chunk.",
            embedding=[0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
        )

    quality_paper = Paper.objects.create(
        title="Modern Expert Retrieval",
        abstract="Semantic ranking and explainability.",
        external_id="paper:quality:001",
        published_date=date(2025, 1, 10),
        security_level=SecurityLevel.PUBLIC,
    )
    Authorship.objects.create(author=high_quality_author, paper=quality_paper, author_order=1)
    PaperTopic.objects.create(paper=quality_paper, topic=topic_graph)
    PaperTopic.objects.create(paper=quality_paper, topic=topic_search)
    Embedding.objects.create(
        paper=quality_paper,
        chunk_id=0,
        text_chunk="Modern semantic retrieval chunk.",
        embedding=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    )

    response = client.get(
        "/api/experts",
        {
            "query": "semantic expert retrieval",
            "clearance": SecurityLevel.PUBLIC,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    experts = payload["experts"]
    assert len(experts) >= 2

    ranked_names = [expert["name"] for expert in experts]
    assert ranked_names[0] == "High Quality Expert"

    by_name = {expert["name"]: expert for expert in experts}
    quality_breakdown = by_name["High Quality Expert"]["score_breakdown"]
    volume_breakdown = by_name["High Volume Expert"]["score_breakdown"]

    assert quality_breakdown["semantic_relevance"] > volume_breakdown["semantic_relevance"]
    assert len(by_name["High Volume Expert"]["top_papers"]) > len(
        by_name["High Quality Expert"]["top_papers"]
    )
    assert "why_ranked" in by_name["High Quality Expert"]


@pytest.mark.django_db
def test_experts_respect_clearance_for_top_papers(client, patched_expert_backend) -> None:
    public_author = Author.objects.create(
        name="Public Expert",
        external_id="author:public:experts:001",
        institution_name="Public Lab",
    )
    confidential_author = Author.objects.create(
        name="Confidential Expert",
        external_id="author:confidential:experts:001",
        institution_name="Restricted Lab",
    )

    topic = Topic.objects.create(name="AI", external_id="topic:ai:001")

    public_paper = Paper.objects.create(
        title="Public AI Methods",
        abstract="Public abstract.",
        external_id="paper:public:experts:001",
        published_date=date(2024, 1, 1),
        security_level=SecurityLevel.PUBLIC,
    )
    confidential_paper = Paper.objects.create(
        title="Confidential AI Program",
        abstract="Highly confidential details.",
        external_id="paper:confidential:experts:001",
        published_date=date(2025, 1, 1),
        security_level=SecurityLevel.CONFIDENTIAL,
    )

    Authorship.objects.create(author=public_author, paper=public_paper, author_order=1)
    Authorship.objects.create(author=confidential_author, paper=confidential_paper, author_order=1)
    PaperTopic.objects.create(paper=public_paper, topic=topic)
    PaperTopic.objects.create(paper=confidential_paper, topic=topic)

    Embedding.objects.create(
        paper=public_paper,
        chunk_id=0,
        text_chunk="Public AI retrieval chunk.",
        embedding=[0.0, 1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    )
    Embedding.objects.create(
        paper=confidential_paper,
        chunk_id=0,
        text_chunk="Confidential AI program chunk.",
        embedding=[1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0],
    )

    public_response = client.get(
        "/api/experts",
        {
            "query": "ai program",
            "clearance": SecurityLevel.PUBLIC,
        },
    )

    assert public_response.status_code == 200
    public_payload = public_response.json()
    public_names = [expert["name"] for expert in public_payload["experts"]]
    assert "Confidential Expert" not in public_names

    public_serialized = public_response.content.decode("utf-8")
    assert confidential_paper.title not in public_serialized
    assert confidential_paper.abstract not in public_serialized

    confidential_response = client.get(
        "/api/experts",
        {
            "query": "ai program",
            "clearance": SecurityLevel.CONFIDENTIAL,
        },
    )

    assert confidential_response.status_code == 200
    confidential_payload = confidential_response.json()
    confidential_names = [expert["name"] for expert in confidential_payload["experts"]]
    assert "Confidential Expert" in confidential_names


@pytest.mark.django_db
@override_settings(EXPERTS_ENABLE_GRAPH_CENTRALITY=True)
def test_experts_use_persisted_graph_centrality_signal(
    client,
    patched_expert_backend,
) -> None:
    topic = Topic.objects.create(name="ML", external_id="topic:ml:001")

    low_centrality_author = Author.objects.create(
        name="Low Centrality Expert",
        external_id="author:centrality:low:001",
        institution_name="Team A",
        centrality_score=0.1,
    )
    high_centrality_author = Author.objects.create(
        name="High Centrality Expert",
        external_id="author:centrality:high:001",
        institution_name="Team B",
        centrality_score=0.9,
    )

    low_paper = Paper.objects.create(
        title="Shared Semantic Basis A",
        abstract="Equivalent semantic basis.",
        external_id="paper:centrality:low:001",
        published_date=date(2025, 1, 1),
        security_level=SecurityLevel.PUBLIC,
    )
    high_paper = Paper.objects.create(
        title="Shared Semantic Basis B",
        abstract="Equivalent semantic basis.",
        external_id="paper:centrality:high:001",
        published_date=date(2025, 1, 1),
        security_level=SecurityLevel.PUBLIC,
    )

    Authorship.objects.create(author=low_centrality_author, paper=low_paper, author_order=1)
    Authorship.objects.create(author=high_centrality_author, paper=high_paper, author_order=1)
    PaperTopic.objects.create(paper=low_paper, topic=topic)
    PaperTopic.objects.create(paper=high_paper, topic=topic)

    # Same vectors keep semantic scores equal, so centrality should break the tie.
    shared_vector = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    Embedding.objects.create(
        paper=low_paper,
        chunk_id=0,
        text_chunk="Shared semantic chunk.",
        embedding=shared_vector,
    )
    Embedding.objects.create(
        paper=high_paper,
        chunk_id=0,
        text_chunk="Shared semantic chunk.",
        embedding=shared_vector,
    )

    response = client.get(
        "/api/experts",
        {
            "query": "semantic basis",
            "clearance": SecurityLevel.PUBLIC,
        },
    )

    assert response.status_code == 200
    payload = response.json()
    experts = payload["experts"]
    assert len(experts) >= 2

    ranked_names = [expert["name"] for expert in experts]
    assert ranked_names[0] == "High Centrality Expert"

    by_name = {expert["name"]: expert for expert in experts}
    high_graph_score = by_name["High Centrality Expert"]["score_breakdown"]["graph_centrality"]
    low_graph_score = by_name["Low Centrality Expert"]["score_breakdown"]["graph_centrality"]
    assert high_graph_score > low_graph_score
