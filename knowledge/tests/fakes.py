"""Shared fakes for knowledge training and vector tests."""

from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch


class FakeEmbeddingService:
    """Deterministic embeddings; no network calls."""

    calls = []

    def __init__(self, *args, **kwargs):
        pass

    def embed_document(self, text):
        FakeEmbeddingService.calls.append(text)
        return [0.1, 0.2, 0.3]

    def embed_query(self, text):
        return [0.1, 0.2, 0.3]


class FakeVectorService:
    """Records vector writes; no database access."""

    def __init__(self, *args, **kwargs):
        self.kb_replacements = []
        self.catalog_replacements = []
        self.removed_documents = []
        self.removed_catalog_items = []

    def has_complete_vector_set(self, knowledge_base_id):
        return False

    def replace_knowledge_base(self, knowledge_base_id, documents):
        self.kb_replacements.append((knowledge_base_id, list(documents)))
        return documents

    def replace_catalog_items(self, *, space, item_ids, documents):
        self.catalog_replacements.append((space, list(item_ids), list(documents)))
        return documents

    def remove_document(self, knowledge_base_id):
        self.removed_documents.append(knowledge_base_id)

    def remove_catalog_item(self, *, space, item_id):
        self.removed_catalog_items.append((space, item_id))


class FakeScraper:
    def __init__(self, *args, **kwargs):
        pass

    async def scrape_text_content(self, url, *args, **kwargs):
        return SimpleNamespace(
            success=True,
            data=SimpleNamespace(
                title="Example Shop",
                text="We sell wireless accessories with a 30 day return policy.",
            ),
        )


class FakePrivateStorage:
    uploads = []

    @staticmethod
    def build_key(*, chatbot_id, filename):
        return f"files/{chatbot_id}/test/{filename}"

    def upload(self, file_obj, *, key, **kwargs):
        FakePrivateStorage.uploads.append(key)
        file_obj.seek(0)
        self.stored = {key: file_obj.read()}
        file_obj.seek(0)

    def download(self, key, target_file):
        target_file.write(self.stored.get(key, b""))
        target_file.seek(0)


@contextmanager
def fake_training_stack():
    """Patch embedding, vector persistence, and the scraper used by the
    training pipeline; yields the shared FakeVectorService."""
    vector_service = FakeVectorService()
    with patch(
        "knowledge.services.training.GeminiEmbeddingService",
        FakeEmbeddingService,
    ), patch(
        "knowledge.services.training.KnowledgeVectorService",
        lambda *args, **kwargs: vector_service,
    ), patch(
        "knowledge.services.training.WebScraper",
        FakeScraper,
    ):
        yield vector_service
