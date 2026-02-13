from django.db import migrations
import pgvector.django


class Migration(migrations.Migration):
    dependencies = [
        ("documents", "0002_ingestionrun"),
    ]

    operations = [
        migrations.AlterField(
            model_name="embedding",
            name="embedding",
            field=pgvector.django.VectorField(blank=True, dimensions=8, null=True),
        ),
    ]
