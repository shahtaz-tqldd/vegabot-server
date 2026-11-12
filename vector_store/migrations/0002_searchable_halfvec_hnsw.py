import pgvector.django
from django.db import connection, migrations, models
from django.db.models import Q


def backfill_is_searchable(apps, schema_editor):
    """Hide vectors whose source is disabled or whose chatbot is deleted."""
    VectorDocument = apps.get_model("vector_store", "VectorDocument")
    searchable = (
        Q(space="document", knowledge_base__is_enabled=True)
        | Q(space="product", product__is_enabled=True)
        | Q(space="service", service__is_enabled=True)
    ) & Q(chatbot__is_deleted=False)
    searchable_ids = VectorDocument.objects.filter(searchable).values("id")
    VectorDocument.objects.exclude(id__in=searchable_ids).update(
        is_searchable=False,
    )


def _partial_hnsw(space):
    return pgvector.django.HnswIndex(
        name=f"vecdoc_{space}_hnsw",
        fields=["embedding"],
        m=16,
        ef_construction=64,
        opclasses=["halfvec_cosine_ops"],
        condition=Q(space=space, is_searchable=True),
    )


operations = [
    migrations.AddField(
        model_name="vectordocument",
        name="is_searchable",
        field=models.BooleanField(default=True),
    ),
    migrations.RunPython(backfill_is_searchable, migrations.RunPython.noop),
    migrations.AddConstraint(
        model_name="vectordocument",
        constraint=models.CheckConstraint(
            condition=(
                Q(space="document", knowledge_base__isnull=False, product__isnull=True, service__isnull=True)
                | Q(space="product", product__isnull=False, knowledge_base__isnull=True, service__isnull=True)
                | Q(space="service", service__isnull=False, knowledge_base__isnull=True, product__isnull=True)
            ),
            name="vector_doc_single_source",
        ),
    ),
]

# pgvector and its indexes are PostgreSQL-only (see 0001). The full-vector
# index is replaced by per-space partial halfvec indexes over searchable
# rows; the metadata GIN index is no longer queried.
POSTGRES_OPERATIONS = [
    migrations.RemoveIndex(
        model_name="vectordocument",
        name="vector_metadata_gin",
    ),
    migrations.RemoveIndex(
        model_name="vectordocument",
        name="vector_docs_embedding_hnsw",
    ),
    migrations.AlterField(
        model_name="vectordocument",
        name="embedding",
        field=pgvector.django.HalfVectorField(dimensions=1536),
    ),
    migrations.AddIndex(
        model_name="vectordocument",
        index=_partial_hnsw("document"),
    ),
    migrations.AddIndex(
        model_name="vectordocument",
        index=_partial_hnsw("product"),
    ),
    migrations.AddIndex(
        model_name="vectordocument",
        index=_partial_hnsw("service"),
    ),
]

if connection.vendor == "postgresql":
    operations += POSTGRES_OPERATIONS


class Migration(migrations.Migration):

    dependencies = [
        ("chatbot", "0003_voices"),
        ("knowledge", "0003_product_image_url"),
        ("vector_store", "0001_initial"),
    ]

    operations = operations
