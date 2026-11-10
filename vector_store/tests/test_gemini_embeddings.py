from types import SimpleNamespace
from unittest.mock import patch

from django.test import SimpleTestCase

from vector_store.services.gemini_embeddings import GeminiEmbeddingService


class FakeModels:
    def __init__(self):
        self.calls = []

    def embed_content(self, *, model, contents, config):
        self.calls.append(contents)
        count = len(contents) if isinstance(contents, list) else 1
        return SimpleNamespace(
            embeddings=[SimpleNamespace(values=[float(i)]) for i in range(count)],
        )


def build_service(**kwargs):
    models = FakeModels()
    with patch(
        "vector_store.services.gemini_embeddings.genai.Client",
        lambda **_: SimpleNamespace(models=models),
    ):
        service = GeminiEmbeddingService(
            project="test-project",
            location="us-central1",
            request_delay_seconds=0,
            **kwargs,
        )
    return service, models


class GeminiEmbeddingBatchTests(SimpleTestCase):
    def test_developer_api_batches_texts_into_few_requests(self):
        service, models = build_service(
            use_vertex=False,
            api_key="key",
            model="gemini-embedding-2",
            batch_size=2,
        )
        progress = []

        embeddings = service.embed_documents(
            ["a", "b", "c"],
            on_progress=lambda done, total: progress.append((done, total)),
        )

        self.assertEqual(len(embeddings), 3)
        self.assertEqual(len(models.calls), 2)
        # Each text is its own Content, so it gets its own embedding.
        self.assertEqual(len(models.calls[0]), 2)
        self.assertEqual(progress, [(2, 3), (3, 3)])

    def test_vertex_predict_model_batches_texts(self):
        service, models = build_service(
            use_vertex=True,
            model="gemini-embedding-001",
            batch_size=100,
        )

        embeddings = service.embed_documents(["a", "b", "c"])

        self.assertEqual(len(embeddings), 3)
        self.assertEqual(models.calls, [["a", "b", "c"]])

    def test_vertex_embed_content_model_sends_one_text_per_request(self):
        service, models = build_service(
            use_vertex=True,
            model="gemini-embedding-2",
            batch_size=100,
        )

        embeddings = service.embed_documents(["a", "b", "c"])

        self.assertEqual(len(embeddings), 3)
        self.assertEqual(models.calls, ["a", "b", "c"])

    def test_mismatched_vector_count_raises(self):
        service, models = build_service(
            use_vertex=True,
            model="gemini-embedding-001",
        )
        models.embed_content = lambda **_: SimpleNamespace(embeddings=[])

        with self.assertRaises(RuntimeError):
            service.embed_documents(["a"])
