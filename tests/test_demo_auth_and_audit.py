from __future__ import annotations

from datetime import date
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings

from apps.common.demo_auth import SESSION_NAME_KEY, SESSION_ROLE_KEY
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
def test_demo_login_sets_role_in_session(client) -> None:
    response = client.post(
        "/demo/login/",
        {"role": SecurityLevel.INTERNAL, "name": "recruiter-demo", "next": "/"},
    )

    assert response.status_code == 302
    assert response["Location"] == "/"

    session = client.session
    assert session[SESSION_ROLE_KEY] == SecurityLevel.INTERNAL
    assert session[SESSION_NAME_KEY] == "recruiter-demo"


@pytest.mark.django_db
@override_settings(OPENAI_API_KEY="")
def test_api_uses_session_clearance_by_default_and_query_param_override(client) -> None:
    topic = Topic.objects.create(name="Graph", external_id="topic:session:graph:001")
    author = Author.objects.create(
        name="Alice",
        external_id="author:session:alice:001",
        institution_name="Lab",
    )

    public_paper = Paper.objects.create(
        title="Public Guide",
        abstract="Public information.",
        external_id="paper:session:public:001",
        published_date=date(2025, 1, 1),
        security_level=SecurityLevel.PUBLIC,
    )
    confidential_paper = Paper.objects.create(
        title="Confidential Playbook",
        abstract="Highly sensitive implementation.",
        external_id="paper:session:confidential:001",
        published_date=date(2025, 1, 2),
        security_level=SecurityLevel.CONFIDENTIAL,
    )

    Authorship.objects.create(author=author, paper=public_paper, author_order=1)
    Authorship.objects.create(author=author, paper=confidential_paper, author_order=1)
    PaperTopic.objects.create(paper=public_paper, topic=topic)
    PaperTopic.objects.create(paper=confidential_paper, topic=topic)

    vector = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    Embedding.objects.create(
        paper=public_paper,
        chunk_id=0,
        text_chunk="Public retrieval patterns.",
        embedding=vector,
    )
    Embedding.objects.create(
        paper=confidential_paper,
        chunk_id=0,
        text_chunk="Confidential retrieval strategy.",
        embedding=vector,
    )

    session = client.session
    session[SESSION_ROLE_KEY] = SecurityLevel.PUBLIC
    session[SESSION_NAME_KEY] = "session-recruiter"
    session.save()

    with patch("apps.api.services.get_embedding_backend", return_value=StaticBackend(vector)):
        restricted_response = client.get(
            "/api/search",
            {
                "query": "retrieval strategy",
                "page": 1,
            },
        )
        override_response = client.get(
            "/api/search",
            {
                "query": "retrieval strategy",
                "page": 1,
                "clearance": SecurityLevel.CONFIDENTIAL,
            },
        )

    assert restricted_response.status_code == 200
    restricted_payload = restricted_response.json()
    assert restricted_payload["clearance"] == SecurityLevel.PUBLIC
    assert restricted_payload["redacted_count"] >= 1

    serialized = restricted_response.content.decode("utf-8")
    assert "Confidential Playbook" not in serialized

    first_audit = SearchAudit.objects.filter(endpoint="/api/search").order_by("id").first()
    assert first_audit is not None
    assert first_audit.clearance == SecurityLevel.PUBLIC
    assert first_audit.user_role == SecurityLevel.PUBLIC
    assert first_audit.client_id == "session-recruiter"

    assert override_response.status_code == 200
    override_payload = override_response.json()
    assert override_payload["clearance"] == SecurityLevel.CONFIDENTIAL

    latest_audit = SearchAudit.objects.filter(endpoint="/api/search").order_by("-id").first()
    assert latest_audit is not None
    assert latest_audit.clearance == SecurityLevel.CONFIDENTIAL
    assert latest_audit.user_role == SecurityLevel.PUBLIC


@pytest.mark.django_db
@override_settings(OPENAI_API_KEY="", ASK_TOP_K=4)
def test_api_ask_logs_user_role_from_session(client) -> None:
    topic = Topic.objects.create(name="RAG", external_id="topic:session:rag:001")
    internal_author = Author.objects.create(
        name="Internal Author",
        external_id="author:session:internal:001",
        institution_name="Internal Lab",
    )
    confidential_author = Author.objects.create(
        name="Confidential Author",
        external_id="author:session:confidential:001",
        institution_name="Secure Lab",
    )

    internal_paper = Paper.objects.create(
        title="Internal Handbook",
        abstract="Internal workflow.",
        external_id="paper:session:internal:001",
        published_date=date(2025, 1, 1),
        security_level=SecurityLevel.INTERNAL,
    )
    confidential_paper = Paper.objects.create(
        title="Confidential Blueprint",
        abstract="Top secret roadmap.",
        external_id="paper:session:confidential:002",
        published_date=date(2025, 1, 2),
        security_level=SecurityLevel.CONFIDENTIAL,
    )

    Authorship.objects.create(author=internal_author, paper=internal_paper, author_order=1)
    Authorship.objects.create(author=confidential_author, paper=confidential_paper, author_order=1)
    PaperTopic.objects.create(paper=internal_paper, topic=topic)
    PaperTopic.objects.create(paper=confidential_paper, topic=topic)

    vector = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    Embedding.objects.create(
        paper=internal_paper,
        chunk_id=0,
        text_chunk="Internal RAG guidance.",
        embedding=vector,
    )
    Embedding.objects.create(
        paper=confidential_paper,
        chunk_id=0,
        text_chunk="Confidential roadmap details.",
        embedding=vector,
    )

    session = client.session
    session[SESSION_ROLE_KEY] = SecurityLevel.INTERNAL
    session[SESSION_NAME_KEY] = "analyst"
    session.save()

    with patch("apps.api.ask.get_embedding_backend", return_value=StaticBackend(vector)):
        with patch("apps.api.experts.get_embedding_backend", return_value=StaticBackend(vector)):
            response = client.get(
                "/api/ask",
                {
                    "query": "rag roadmap",
                },
            )

    assert response.status_code == 200
    payload = response.json()
    assert payload["redacted_count"] >= 1

    ask_audit = SearchAudit.objects.filter(endpoint="/api/ask").order_by("-id").first()
    assert ask_audit is not None
    assert ask_audit.clearance == SecurityLevel.INTERNAL
    assert ask_audit.user_role == SecurityLevel.INTERNAL
    assert ask_audit.client_id == "analyst"


@pytest.mark.django_db
def test_admin_search_audit_changelist_is_available(client) -> None:
    User = get_user_model()
    admin_user = User.objects.create_superuser(
        username="admin",
        email="admin@example.com",
        password="pass1234",
    )

    SearchAudit.objects.create(
        endpoint="/api/search",
        query="test query",
        clearance=SecurityLevel.PUBLIC,
        user_role=SecurityLevel.INTERNAL,
        redacted_count=2,
        client_id="client-1",
    )

    client.force_login(admin_user)
    response = client.get("/admin/documents/searchaudit/")

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Search audits" in content or "Select search audit to change" in content
    assert "Internal" in content
