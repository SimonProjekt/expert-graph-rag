from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0003_embedding_nullable"),
    ]

    operations = [
        migrations.AddField(
            model_name="author",
            name="centrality_score",
            field=models.FloatField(blank=True, db_index=True, null=True),
        ),
    ]
