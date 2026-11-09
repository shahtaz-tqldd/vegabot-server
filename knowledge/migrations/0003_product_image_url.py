from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("knowledge", "0002_knowledgebase_text_source"),
    ]

    operations = [
        migrations.AddField(
            model_name="product",
            name="image_url",
            field=models.URLField(blank=True, default=""),
        ),
    ]
