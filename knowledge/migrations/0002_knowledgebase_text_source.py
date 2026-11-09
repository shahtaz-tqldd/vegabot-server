from django.db import migrations, models


class Migration(migrations.Migration):
    dependencies = [
        ("knowledge", "0001_initial"),
    ]

    operations = [
        migrations.AlterField(
            model_name="knowledgebase",
            name="source_type",
            field=models.CharField(
                choices=[
                    ("file", "File"),
                    ("website", "Website"),
                    ("text", "Text"),
                ],
                db_index=True,
                max_length=20,
            ),
        ),
    ]
