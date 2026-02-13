from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="IngestionRun",
            fields=[
                (
                    "id",
                    models.BigAutoField(
                        auto_created=True,
                        primary_key=True,
                        serialize=False,
                        verbose_name="ID",
                    ),
                ),
                ("query", models.TextField()),
                ("started_at", models.DateTimeField(auto_now_add=True, db_index=True)),
                ("finished_at", models.DateTimeField(blank=True, null=True)),
                ("counts", models.JSONField(blank=True, default=dict)),
                (
                    "status",
                    models.CharField(
                        choices=[
                            ("RUNNING", "Running"),
                            ("SUCCESS", "Success"),
                            ("FAILED", "Failed"),
                        ],
                        db_index=True,
                        default="RUNNING",
                        max_length=20,
                    ),
                ),
                ("error_message", models.TextField(blank=True, default="")),
            ],
            options={
                "ordering": ["-started_at", "id"],
                "indexes": [
                    models.Index(
                        fields=["status", "started_at"],
                        name="ingestion_run_status_ts_idx",
                    )
                ],
            },
        ),
    ]
