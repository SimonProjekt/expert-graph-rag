from __future__ import annotations

from django.db import models
from pgvector.django import HnswIndex, VectorField


class SecurityLevel(models.TextChoices):
    PUBLIC = "PUBLIC", "Public"
    INTERNAL = "INTERNAL", "Internal"
    CONFIDENTIAL = "CONFIDENTIAL", "Confidential"


class Author(models.Model):
    name = models.CharField(max_length=255)
    external_id = models.CharField(max_length=128, unique=True)
    institution_name = models.CharField(max_length=255)
    centrality_score = models.FloatField(null=True, blank=True, db_index=True)

    class Meta:
        ordering = ["name", "id"]
        indexes = [models.Index(fields=["name"], name="author_name_idx")]

    def __str__(self) -> str:
        return f"{self.name} ({self.external_id})"


class Paper(models.Model):
    title = models.CharField(max_length=500)
    abstract = models.TextField(blank=True)
    published_date = models.DateField(null=True, blank=True, db_index=True)
    doi = models.CharField(max_length=255, unique=True, null=True, blank=True)
    external_id = models.CharField(max_length=128, unique=True)
    security_level = models.CharField(
        max_length=20,
        choices=SecurityLevel.choices,
        default=SecurityLevel.PUBLIC,
        db_index=True,
    )

    class Meta:
        ordering = ["-published_date", "id"]
        indexes = [
            models.Index(fields=["security_level", "published_date"], name="paper_sec_pub_idx"),
            models.Index(fields=["published_date"], name="paper_pub_date_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.title} ({self.external_id})"


class Topic(models.Model):
    name = models.CharField(max_length=255)
    external_id = models.CharField(max_length=128, unique=True)

    class Meta:
        ordering = ["name", "id"]
        indexes = [models.Index(fields=["name"], name="topic_name_idx")]

    def __str__(self) -> str:
        return f"{self.name} ({self.external_id})"


class Authorship(models.Model):
    author = models.ForeignKey(Author, on_delete=models.CASCADE, related_name="authorships")
    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="authorships")
    author_order = models.PositiveSmallIntegerField()

    class Meta:
        ordering = ["paper_id", "author_order"]
        constraints = [
            models.UniqueConstraint(
                fields=["author", "paper"],
                name="uniq_authorship_author_paper",
            ),
            models.UniqueConstraint(
                fields=["paper", "author_order"],
                name="uniq_authorship_paper_order",
            ),
        ]
        indexes = [
            models.Index(
                fields=["paper", "author_order"],
                name="authorship_paper_order_idx",
            )
        ]

    def __str__(self) -> str:
        return f"{self.paper_id}:{self.author_id}#{self.author_order}"


class PaperTopic(models.Model):
    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="paper_topics")
    topic = models.ForeignKey(Topic, on_delete=models.CASCADE, related_name="paper_topics")

    class Meta:
        constraints = [models.UniqueConstraint(fields=["paper", "topic"], name="uniq_paper_topic")]
        indexes = [models.Index(fields=["paper", "topic"], name="paper_topic_idx")]

    def __str__(self) -> str:
        return f"{self.paper_id}:{self.topic_id}"


class Embedding(models.Model):
    paper = models.ForeignKey(Paper, on_delete=models.CASCADE, related_name="embeddings")
    chunk_id = models.PositiveIntegerField()
    text_chunk = models.TextField()
    embedding = VectorField(dimensions=8, null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["paper_id", "chunk_id"]
        constraints = [
            models.UniqueConstraint(
                fields=["paper", "chunk_id"],
                name="uniq_embedding_chunk",
            )
        ]
        indexes = [
            models.Index(fields=["paper", "chunk_id"], name="embedding_paper_chunk_idx"),
            HnswIndex(
                name="embedding_hnsw_idx",
                fields=["embedding"],
                m=16,
                ef_construction=64,
                opclasses=["vector_cosine_ops"],
            ),
        ]

    def __str__(self) -> str:
        return f"embedding:{self.paper_id}:{self.chunk_id}"


class SearchAudit(models.Model):
    timestamp = models.DateTimeField(auto_now_add=True, db_index=True)
    endpoint = models.CharField(max_length=255)
    query = models.TextField()
    clearance = models.CharField(max_length=20, choices=SecurityLevel.choices, db_index=True)
    user_role = models.CharField(
        max_length=20,
        choices=SecurityLevel.choices,
        default=SecurityLevel.PUBLIC,
        db_index=True,
    )
    redacted_count = models.PositiveIntegerField(default=0)
    client_id = models.CharField(max_length=128, null=True, blank=True)

    class Meta:
        ordering = ["-timestamp", "id"]
        constraints = [
            models.CheckConstraint(
                check=models.Q(redacted_count__gte=0),
                name="search_audit_redacted_nonneg",
            )
        ]
        indexes = [
            models.Index(fields=["clearance", "timestamp"], name="audit_clearance_ts_idx"),
            models.Index(fields=["user_role", "timestamp"], name="audit_user_role_ts_idx"),
            models.Index(fields=["endpoint", "timestamp"], name="audit_endpoint_ts_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.timestamp.isoformat()} {self.endpoint}"


class IngestionStatus(models.TextChoices):
    RUNNING = "RUNNING", "Running"
    SUCCESS = "SUCCESS", "Success"
    FAILED = "FAILED", "Failed"


class IngestionRun(models.Model):
    query = models.TextField()
    started_at = models.DateTimeField(auto_now_add=True, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    counts = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=IngestionStatus.choices,
        default=IngestionStatus.RUNNING,
        db_index=True,
    )
    error_message = models.TextField(blank=True, default="")

    class Meta:
        ordering = ["-started_at", "id"]
        indexes = [
            models.Index(fields=["status", "started_at"], name="ingestion_run_status_ts_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.started_at.isoformat()} {self.status}"
