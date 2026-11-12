import json
import time

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from google import genai
from google.genai import _transformers as genai_transformers
from google.genai import types

from app.constants import (
    GEMINI_EMBEDDING_BATCH_SIZE,
    GEMINI_EMBEDDING_DIMENSIONS,
    GEMINI_EMBEDDING_REQUEST_DELAY_SECONDS,
    GEMINI_EMBEDDING_USE_VERTEX,
)


class GeminiEmbeddingService:
    """Gemini embeddings with single-request batching on every backend.

    Batching per backend (``batch_size`` texts per request):
    - Vertex AI ``:embedContent`` models (``gemini-embedding-2`` and newer):
      Vertex's ``batchEmbedContents`` REST method. The SDK does not expose
      it for Vertex, so it is called through the SDK's authenticated client;
      vectors are identical to single ``embedContent`` calls.
    - Vertex AI ``:predict`` models (``gemini-embedding-001``,
      ``text-embedding-*``): one ``predict`` request with many instances.
    - Gemini Developer API (``GEMINI_EMBEDDING_USE_VERTEX`` disabled):
      ``batchEmbedContents`` with ``GEMINI_API_KEY``.
    """

    def __init__(
        self,
        *,
        project=None,
        location=None,
        model=None,
        dimensions=None,
        request_delay_seconds=None,
        api_key=None,
        use_vertex=None,
        batch_size=None,
    ):
        self.project = project or settings.GOOGLE_CLOUD_PROJECT_ID
        self.location = location or settings.GOOGLE_CLOUD_LOCATION
        self.model = model or settings.GEMINI_EMBEDDING_MODEL
        self.dimensions = dimensions or GEMINI_EMBEDDING_DIMENSIONS
        self.request_delay_seconds = (
            GEMINI_EMBEDDING_REQUEST_DELAY_SECONDS
            if request_delay_seconds is None
            else request_delay_seconds
        )
        self.api_key = api_key or settings.GEMINI_API_KEY
        self.use_vertex = (
            GEMINI_EMBEDDING_USE_VERTEX if use_vertex is None else use_vertex
        )
        self.batch_size = max(
            int(batch_size or GEMINI_EMBEDDING_BATCH_SIZE or 1), 1,
        )
        self._last_request_at = None

        if self.use_vertex:
            if not self.project:
                raise ImproperlyConfigured(
                    "GOOGLE_CLOUD_PROJECT_ID is required for Vertex AI.",
                )
            # Credentials are picked up automatically from
            self.client = genai.Client(
                vertexai=True,
                project=self.project,
                location=self.location,
            )
        else:
            if not self.api_key:
                raise ImproperlyConfigured(
                    "GEMINI_API_KEY is required when GEMINI_EMBEDDING_USE_VERTEX "
                    "is disabled.",
                )
            # Explicit vertexai=False: GOOGLE_GENAI_USE_VERTEXAI (used by the
            # chat agents) would otherwise route this client to Vertex.
            self.client = genai.Client(vertexai=False, api_key=self.api_key)

    # ------------------------------------------------------------- public

    @property
    def uses_vertex_batch_embed_contents(self):
        return self.use_vertex and genai_transformers.t_is_vertex_embed_content_model(
            self.model,
        )

    def embed_document(self, text):
        return self._embed_batch([text], task_type="RETRIEVAL_DOCUMENT")[0]

    def embed_query(self, text):
        return self._embed_batch([text], task_type="RETRIEVAL_QUERY")[0]

    def embed_documents(self, texts, *, on_progress=None):
        """Embed many texts with as few requests as the backend allows.

        Returns embeddings in the same order as ``texts``. ``on_progress``
        is called as ``on_progress(done, total)`` after each request.
        """
        texts = list(texts)
        total = len(texts)
        embeddings = []
        for start in range(0, total, self.batch_size):
            embeddings.extend(
                self._embed_batch(
                    texts[start:start + self.batch_size],
                    task_type="RETRIEVAL_DOCUMENT",
                ),
            )
            if on_progress:
                on_progress(len(embeddings), total)
        return embeddings

    # ------------------------------------------------------------ helpers

    def _embed_batch(self, texts, *, task_type):
        self._wait_for_rate_limit()
        if self.uses_vertex_batch_embed_contents and len(texts) > 1:
            vectors = self._vertex_batch_embed_contents(texts, task_type=task_type)
        else:
            vectors = self._sdk_embed_content(texts, task_type=task_type)
        if len(vectors) != len(texts):
            raise RuntimeError(
                f"Embedding API returned {len(vectors)} vectors for "
                f"{len(texts)} inputs.",
            )
        return vectors

    def _sdk_embed_content(self, texts, *, task_type):
        config = types.EmbedContentConfig(task_type=task_type)
        if self.dimensions:
            config.output_dimensionality = int(self.dimensions)

        if not self.use_vertex:
            # One Content per text; a bare list of strings would be merged
            # into a single Content and yield a single embedding.
            contents = [
                types.Content(role="user", parts=[types.Part(text=text)])
                for text in texts
            ]
        elif len(texts) == 1:
            contents = texts[0]
        else:
            # Vertex :predict takes one instance per string.
            contents = list(texts)

        response = self.client.models.embed_content(
            model=self.model,
            contents=contents,
            config=config,
        )
        return [embedding.values for embedding in response.embeddings or []]

    def _vertex_batch_embed_contents(self, texts, *, task_type):
        """POST {model}:batchEmbedContents on Vertex AI.

        Uses the SDK's API client for auth, project/location routing and
        error handling (non-2xx responses raise ``google.genai.errors``).
        """
        request_config = {"taskType": task_type}
        if self.dimensions:
            request_config["outputDimensionality"] = int(self.dimensions)
        body = {
            "requests": [
                {
                    "content": {"role": "user", "parts": [{"text": text}]},
                    **request_config,
                }
                for text in texts
            ],
        }
        model_path = (
            self.model if "/" in self.model
            else f"publishers/google/models/{self.model}"
        )
        response = self.client._api_client.request(
            "post",
            f"{model_path}:batchEmbedContents",
            body,
            None,
        )
        payload = json.loads(response.body or "{}")
        return [item.get("values", []) for item in payload.get("embeddings", [])]

    def _wait_for_rate_limit(self):
        delay = float(self.request_delay_seconds or 0)
        if delay <= 0:
            return

        now = time.monotonic()
        if self._last_request_at is not None:
            elapsed = now - self._last_request_at
            if elapsed < delay:
                time.sleep(delay - elapsed)
                now = time.monotonic()

        self._last_request_at = now
