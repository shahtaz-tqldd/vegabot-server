import time

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

from google import genai
from google.genai import types


class GeminiEmbeddingService:
    def __init__(self, *, project=None, location=None, model=None, dimensions=None, request_delay_seconds=None):
        self.project = project or settings.GOOGLE_CLOUD_PROJECT_ID
        self.location = location or settings.GOOGLE_CLOUD_LOCATION
        self.model = model or settings.GEMINI_EMBEDDING_MODEL
        self.dimensions = dimensions or settings.GEMINI_EMBEDDING_DIMENSIONS
        self.request_delay_seconds = (
            settings.GEMINI_EMBEDDING_REQUEST_DELAY_SECONDS
            if request_delay_seconds is None
            else request_delay_seconds
        )
        self._last_request_at = None

        if not self.project:
            raise ImproperlyConfigured("GOOGLE_CLOUD_PROJECT_ID is required for Vertex AI.")

        # Credentials are picked up automatically from GOOGLE_APPLICATION_CREDENTIALS (ADC)
        self.client = genai.Client(
            vertexai=True,
            project=self.project,
            location=self.location,
        )

    def embed_document(self, text):
        return self._embed_text(text, task_type="RETRIEVAL_DOCUMENT")

    def embed_query(self, text):
        return self._embed_text(text, task_type="RETRIEVAL_QUERY")

    def _embed_text(self, text, *, task_type):
        config = types.EmbedContentConfig(task_type=task_type)
        if self.dimensions:
            config.output_dimensionality = int(self.dimensions)

        self._wait_for_rate_limit()
        response = self.client.models.embed_content(
            model=self.model,
            contents=text,
            config=config,
        )
        return response.embeddings[0].values

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
