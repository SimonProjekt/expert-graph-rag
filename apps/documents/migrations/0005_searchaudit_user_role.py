from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0004_author_centrality_score"),
    ]

    operations = [
        migrations.AddField(
            model_name="searchaudit",
            name="user_role",
            field=models.CharField(
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
        migrations.AddIndex(
            model_name="searchaudit",
            index=models.Index(fields=["user_role", "timestamp"], name="audit_user_role_ts_idx"),
        ),
    ]
