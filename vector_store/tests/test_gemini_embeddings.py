import json
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


class FakeApiClient:
    def __init__(self):
        self.calls = []

    def request(self, method, path, body, http_options):
        self.calls.append((method, path, body))
        return SimpleNamespace(
            body=json.dumps(
                {"embeddings": [{"values": [float(i)]} for i in range(len(body["requests"]))]},
            ),
        )


def build_service(**kwargs):
    models = FakeModels()
    models.api_client = FakeApiClient()
    with patch(
        "vector_store.services.gemini_embeddings.genai.Client",
        lambda **_: SimpleNamespace(models=models, _api_client=models.api_client),
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

    def test_vertex_embed_content_model_uses_batch_embed_contents(self):
        service, models = build_service(
            use_vertex=True,
            model="gemini-embedding-2",
            batch_size=2,
        )

        embeddings = service.embed_documents(["a", "b", "c"])

        self.assertEqual(len(embeddings), 3)
        calls = models.api_client.calls
        self.assertEqual(len(calls), 1)
        # A lone trailing text goes through the SDK's embedContent.
        self.assertEqual(models.calls, ["c"])
        method, path, body = calls[0]
        self.assertEqual(method, "post")
        self.assertEqual(
            path,
            "publishers/google/models/gemini-embedding-2:batchEmbedContents",
        )
        self.assertEqual(len(body["requests"]), 2)
        self.assertEqual(body["requests"][0]["taskType"], "RETRIEVAL_DOCUMENT")
        self.assertEqual(body["requests"][0]["outputDimensionality"], 1536)
        self.assertEqual(
            body["requests"][0]["content"]["parts"], [{"text": "a"}],
        )

    def test_vertex_single_query_uses_embed_content(self):
        service, models = build_service(
            use_vertex=True,
            model="gemini-embedding-2",
        )

        service.embed_query("hello")

        self.assertEqual(models.calls, ["hello"])
        self.assertEqual(models.api_client.calls, [])

    def test_mismatched_vector_count_raises(self):
        service, models = build_service(
            use_vertex=True,
            model="gemini-embedding-001",
        )
        models.embed_content = lambda **_: SimpleNamespace(embeddings=[])

        with self.assertRaises(RuntimeError):
            service.embed_documents(["a"])
