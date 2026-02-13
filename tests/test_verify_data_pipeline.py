from __future__ import annotations

from datetime import date
from io import StringIO
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.management import call_command
from django.utils import timezone

from apps.documents.models import (
    Author,
    Authorship,
    Embedding,
    Paper,
    PaperTopic,
    SecurityLevel,
    Topic,
)
from apps.documents.verification import DataPipelineVerifier


class StaticBackend:
    def __init__(self, vector: list[float]) -> None:
        self._vector = vector

    def embed_texts(self, texts: list[str]) -> list[list[float]]:
        return [list(self._vector) for _ in texts]


class FakeNeo4jResult:
    def __init__(self, value):
        self._value = value

    def single(self):
        return {"value": self._value}


class FakeNeo4jSession:
    def run(self, query: str):
        q = " ".join(query.split())
        if "MATCH (p:Paper) RETURN count(p) AS value" in q:
            return FakeNeo4jResult(3)
        if "MATCH (a:Author) RETURN count(a) AS value" in q:
            return FakeNeo4jResult(4)
        if "MATCH (t:Topic) RETURN count(t) AS value" in q:
            return FakeNeo4jResult(2)
        if "MATCH ()-[r:WROTE]->() RETURN count(r) AS value" in q:
            return FakeNeo4jResult(5)
        if "MATCH ()-[r:HAS_TOPIC]->() RETURN count(r) AS value" in q:
            return FakeNeo4jResult(4)
        if "MATCH (p:Paper) RETURN max(p.updated_at) AS value" in q:
            return FakeNeo4jResult(timezone.now())
        return FakeNeo4jResult(0)

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


class FakeNeo4jDriver:
    def verify_connectivity(self) -> None:
        return None

    def session(self):
        return FakeNeo4jSession()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb) -> bool:
        return False


@pytest.mark.django_db
def test_verifier_access_control_check_prevents_leakage() -> None:
    check = DataPipelineVerifier()._check_access_control_fixture()

    assert check.passed
    assert check.details["redacted_count"] >= 1
    assert check.error is None


@pytest.mark.django_db
def test_verify_data_pipeline_command_passes_on_seeded_data() -> None:
    topic = Topic.objects.create(name="Verification Topic", external_id="topic:verify:001")
    author = Author.objects.create(
        name="Verification Author",
        external_id="author:verify:001",
        institution_name="Verification Institute",
    )
    paper = Paper.objects.create(
        title="Verification Graph Retrieval",
        abstract="Verification sample abstract for retrieval.",
        external_id="paper:verify:001",
        published_date=date(2025, 1, 1),
        security_level=SecurityLevel.PUBLIC,
    )

    Authorship.objects.create(author=author, paper=paper, author_order=1)
    PaperTopic.objects.create(paper=paper, topic=topic)

    vector = [1.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0, 0.0]
    Embedding.objects.create(
        paper=paper,
        chunk_id=0,
        text_chunk="verification chunk text",
        embedding=vector,
    )

    output = StringIO()

    with patch("apps.documents.verification.GraphDatabase.driver", return_value=FakeNeo4jDriver()):
        with patch("apps.api.services.get_embedding_backend", return_value=StaticBackend(vector)):
            call_command("verify_data_pipeline", stdout=output)

    rendered = output.getvalue()
    assert "Overall: PASS" in rendered
    assert "Data pipeline verification passed." in rendered


@pytest.mark.django_db
def test_debug_data_page_is_staff_only(client) -> None:
    response = client.get("/debug/data/")
    assert response.status_code == 302


@pytest.mark.django_db
def test_debug_data_page_loads_for_staff(client) -> None:
    user_model = get_user_model()
    staff = user_model.objects.create_superuser(
        username="debug-admin",
        email="debug-admin@example.com",
        password="pass1234",
    )
    client.force_login(staff)

    with patch("apps.documents.verification.GraphDatabase.driver", return_value=FakeNeo4jDriver()):
        response = client.get("/debug/data/")

    assert response.status_code == 200
    content = response.content.decode("utf-8")
    assert "Data Integration Debug" in content
    assert "Run verify_data_pipeline" in content
