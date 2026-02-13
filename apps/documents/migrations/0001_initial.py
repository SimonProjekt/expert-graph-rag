from django.db import migrations, models
import django.db.models.deletion
import pgvector.django


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        pgvector.django.VectorExtension(),
        migrations.CreateModel(
            name="Author",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("external_id", models.CharField(max_length=128, unique=True)),
                ("institution_name", models.CharField(max_length=255)),
            ],
            options={
                "ordering": ["name", "id"],
                "indexes": [models.Index(fields=["name"], name="author_name_idx")],
            },
        ),
        migrations.CreateModel(
            name="Paper",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=500)),
                ("abstract", models.TextField(blank=True)),
                ("published_date", models.DateField(blank=True, db_index=True, null=True)),
                ("doi", models.CharField(blank=True, max_length=255, null=True, unique=True)),
                ("external_id", models.CharField(max_length=128, unique=True)),
                (
                    "security_level",
                    models.CharField(
                        choices=[
                            ("PUBLIC", "Public"),
                            ("INTERNAL", "Internal"),
                            ("CONFIDENTIAL", "Confidential"),
                        ],
                        db_index=True,
                        default="PUBLIC",
                        max_length=20,
                    ),
                ),
            ],
            options={
                "ordering": ["-published_date", "id"],
                "indexes": [
                    models.Index(fields=["security_level", "published_date"], name="paper_sec_pub_idx"),
                    models.Index(fields=["published_date"], name="paper_pub_date_idx"),
                ],
            },
        ),
        migrations.CreateModel(
            name="SearchAudit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("timestamp", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("endpoint", models.CharField(max_length=255)),
                ("query", models.TextField()),
                (
                    "clearance",
                    models.CharField(
                        choices=[
                            ("PUBLIC", "Public"),
                            ("INTERNAL", "Internal"),
                            ("CONFIDENTIAL", "Confidential"),
                        ],
                        db_index=True,
                        max_length=20,
                    ),
                ),
                ("redacted_count", models.PositiveIntegerField(default=0)),
                ("client_id", models.CharField(blank=True, max_length=128, null=True)),
            ],
            options={
                "ordering": ["-timestamp", "id"],
                "indexes": [
                    models.Index(fields=["clearance", "timestamp"], name="audit_clearance_ts_idx"),
                    models.Index(fields=["endpoint", "timestamp"], name="audit_endpoint_ts_idx"),
                ],
                "constraints": [
                    models.CheckConstraint(
                        check=models.Q(redacted_count__gte=0),
                        name="search_audit_redacted_nonneg",
                    )
                ],
            },
        ),
        migrations.CreateModel(
            name="Topic",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=255)),
                ("external_id", models.CharField(max_length=128, unique=True)),
            ],
            options={
                "ordering": ["name", "id"],
                "indexes": [models.Index(fields=["name"], name="topic_name_idx")],
            },
        ),
        migrations.CreateModel(
            name="Authorship",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("author_order", models.PositiveSmallIntegerField()),
                (
                    "author",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="authorships",
                        to="documents.author",
                    ),
                ),
                (
                    "paper",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="authorships",
                        to="documents.paper",
                    ),
                ),
            ],
            options={
                "ordering": ["paper_id", "author_order"],
                "indexes": [models.Index(fields=["paper", "author_order"], name="authorship_paper_order_idx")],
                "constraints": [
                    models.UniqueConstraint(fields=("author", "paper"), name="uniq_authorship_author_paper"),
                    models.UniqueConstraint(fields=("paper", "author_order"), name="uniq_authorship_paper_order"),
                ],
            },
        ),
        migrations.CreateModel(
            name="PaperTopic",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                (
                    "paper",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="paper_topics",
                        to="documents.paper",
                    ),
                ),
                (
                    "topic",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="paper_topics",
                        to="documents.topic",
                    ),
                ),
            ],
            options={
                "indexes": [models.Index(fields=["paper", "topic"], name="paper_topic_idx")],
                "constraints": [
                    models.UniqueConstraint(fields=("paper", "topic"), name="uniq_paper_topic")
                ],
            },
        ),
        migrations.CreateModel(
            name="Embedding",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("chunk_id", models.PositiveIntegerField()),
                ("text_chunk", models.TextField()),
                ("embedding", pgvector.django.VectorField(dimensions=8)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                (
                    "paper",
                    models.ForeignKey(
                        on_delete=django.db.models.deletion.CASCADE,
                        related_name="embeddings",
                        to="documents.paper",
                    ),
                ),
            ],
            options={
                "ordering": ["paper_id", "chunk_id"],
                "indexes": [
                    models.Index(fields=["paper", "chunk_id"], name="embedding_paper_chunk_idx"),
                    pgvector.django.HnswIndex(
                        ef_construction=64,
                        fields=["embedding"],
                        m=16,
                        name="embedding_hnsw_idx",
                        opclasses=["vector_cosine_ops"],
                    ),
                ],
                "constraints": [
                    models.UniqueConstraint(fields=("paper", "chunk_id"), name="uniq_embedding_chunk")
                ],
            },
        ),
    ]
