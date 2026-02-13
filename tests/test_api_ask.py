from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest
from django.test import override_settings

from apps.api.llm import OpenAIAnswerService
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


@pytest.mark.django_db
@override_settings(OPENAI_API_KEY="", ASK_TOP_K=4, ASK_FALLBACK_SENTENCE_COUNT=2)
def test_ask_fallback_mode_returns_extractive_answer_with_citations(
    client,
    query_vector: list[float],
) -> None:
    topic = Topic.objects.create(name="Graph", external_id="topic:ask:graph:001")

    allowed_author = Author.objects.create(
        name="Allowed Expert",
        external_id="author:ask:allowed:001",
        institution_name="Public Lab",
    )
    restricted_author = Author.objects.create(
        name="Restricted Expert",
        external_id="author:ask:restricted:001",
        institution_name="Restricted Lab",
    )

    allowed_paper = Paper.objects.create(
        title="Public Graph Retrieval",
        abstract="Graph retrieval can improve enterprise search.",
        external_id="paper:ask:public:001",
        published_date=date(2025, 1, 1),
        security_level=SecurityLevel.PUBLIC,
    )
    restricted_paper = Paper.objects.create(
        title="Confidential Graph Program",
        abstract="Confidential implementation details.",
        external_id="paper:ask:confidential:001",
        published_date=date(2025, 1, 2),
        security_level=SecurityLevel.CONFIDENTIAL,
    )

    Authorship.objects.create(author=allowed_author, paper=allowed_paper, author_order=1)
    Authorship.objects.create(author=restricted_author, paper=restricted_paper, author_order=1)
    PaperTopic.objects.create(paper=allowed_paper, topic=topic)
    PaperTopic.objects.create(paper=restricted_paper, topic=topic)

    Embedding.objects.create(
        paper=allowed_paper,
        chunk_id=0,
        text_chunk="Graph retrieval improves precision. It also improves explainability.",
        embedding=query_vector,
    )
    Embedding.objects.create(
        paper=restricted_paper,
        chunk_id=0,
        text_chunk="Confidential roadmap and budget details.",
        embedding=query_vector,
    )

    with patch("apps.api.ask.get_embedding_backend", return_value=StaticBackend(query_vector)):
        with patch(
            "apps.api.experts.get_embedding_backend",
            return_value=StaticBackend(query_vector),
        ):
            with patch("apps.api.ask.AskService._generate_openai_answer") as openai_mock:
                response = client.get(
                    "/api/ask",
                    {
                        "query": "how does graph retrieval help",
                        "clearance": SecurityLevel.PUBLIC,
                    },
                    HTTP_X_CLIENT_ID="ask-client-fallback",
                )

    assert response.status_code == 200
    payload = response.json()

    assert payload["answer"]
    assert "1. Concise answer" in payload["answer"]
    assert "2. Evidence bullets" in payload["answer"]
    assert "3. Citations" in payload["answer"]
    assert "4. Suggested follow-up questions" in payload["answer"]
    assert "[1]" in payload["answer"]
    assert len(payload["citations"]) >= 1
    assert any(citation["redacted"] for citation in payload["citations"])
    assert len(payload["recommended_experts"]) <= 5

    serialized = response.content.decode("utf-8")
    assert restricted_paper.title not in serialized
    assert restricted_paper.abstract not in serialized

    recommended_names = [expert["name"] for expert in payload["recommended_experts"]]
    assert "Restricted Expert" not in recommended_names

    openai_mock.assert_not_called()


@pytest.mark.django_db
@override_settings(OPENAI_API_KEY="test-openai-key", ASK_TOP_K=4)
def test_ask_openai_mode_calls_server_side_llm_and_returns_citations(
    client,
    query_vector: list[float],
) -> None:
    topic = Topic.objects.create(name="AI", external_id="topic:ask:ai:001")
    author = Author.objects.create(
        name="AI Expert",
        external_id="author:ask:ai:001",
        institution_name="AI Lab",
    )
    paper = Paper.objects.create(
        title="AI Retrieval Guide",
        abstract="AI retrieval with citations.",
        external_id="paper:ask:ai:001",
        published_date=date(2025, 1, 3),
        security_level=SecurityLevel.PUBLIC,
    )

    Authorship.objects.create(author=author, paper=paper, author_order=1)
    PaperTopic.objects.create(paper=paper, topic=topic)
    Embedding.objects.create(
        paper=paper,
        chunk_id=0,
        text_chunk="AI retrieval can answer questions with grounded evidence.",
        embedding=query_vector,
    )

    llm_output = (
        "1. Concise answer\n"
        "Use grounded retrieval [1].\n\n"
        "2. Evidence bullets\n"
        "- The top chunk explains grounded retrieval [1].\n\n"
        "3. Citations\n"
        "- [1] paper:ask:ai:001\n\n"
        "4. Suggested follow-up questions\n"
        "- Which expert should I contact?\n"
    )
    llm_mock = patch.object(OpenAIAnswerService, "generate_answer", return_value=llm_output)

    with patch("apps.api.ask.get_embedding_backend", return_value=StaticBackend(query_vector)):
        with patch(
            "apps.api.experts.get_embedding_backend",
            return_value=StaticBackend(query_vector),
        ):
            with patch(
                "apps.api.ask.OpenAIAnswerService.from_settings",
                return_value=OpenAIAnswerService(api_key="test-openai-key", model="gpt-4o-mini"),
            ):
                with llm_mock as generate_answer_mock:
                    with patch(
                        "apps.api.ask.AskService._build_extractive_answer",
                        return_value="Fallback answer [1]",
                    ) as fallback_mock:
                        response = client.get(
                            "/api/ask",
                            {
                                "query": "how to do ai retrieval",
                                "clearance": SecurityLevel.PUBLIC,
                            },
                        )

    assert response.status_code == 200
    payload = response.json()

    assert payload["answer"] == llm_output.strip()
    assert payload["citations"]
    assert payload["citations"][0]["paper_title"] == "AI Retrieval Guide"
    assert len(payload["recommended_experts"]) == 1
    assert payload["recommended_experts"][0]["name"] == "AI Expert"

    generate_answer_mock.assert_called_once()
    fallback_mock.assert_not_called()
