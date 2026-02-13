import pytest
from django.db.migrations.recorder import MigrationRecorder

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


@pytest.mark.django_db
def test_documents_migration_applied_and_models_create() -> None:
    assert MigrationRecorder.Migration.objects.filter(app="documents", name="0001_initial").exists()

    author = Author.objects.create(
        name="Alice Smith",
        external_id="author:alice-smith:001",
        institution_name="Example University",
    )
    topic = Topic.objects.create(name="Graph RAG", external_id="topic:graph-rag:001")
    paper = Paper.objects.create(
        title="Enterprise Graph RAG",
        abstract="A practical architecture for regulated environments.",
        external_id="paper:enterprise-graph-rag:001",
        doi="10.5555/enterprise-graph-rag-001",
        security_level=SecurityLevel.INTERNAL,
    )

    authorship = Authorship.objects.create(author=author, paper=paper, author_order=1)
    paper_topic = PaperTopic.objects.create(paper=paper, topic=topic)
    embedding = Embedding.objects.create(
        paper=paper,
        chunk_id=0,
        text_chunk="A practical architecture for regulated environments.",
        embedding=[0.1, 0.0, -0.1, 0.2, -0.2, 0.3, -0.3, 0.4],
    )
    audit = SearchAudit.objects.create(
        endpoint="/api/search",
        query="graph rag compliance",
        clearance=SecurityLevel.INTERNAL,
        redacted_count=2,
        client_id="test-client-1",
    )

    assert authorship.author_order == 1
    assert paper_topic.topic_id == topic.id
    assert embedding.paper_id == paper.id
    assert audit.redacted_count == 2
