import tempfile

from knowledge.services.storage import PrivateKnowledgeStorage


class KnowledgeExtractionError(ValueError):
    pass


def extract_file_content(knowledge_base, *, storage=None):
    """Download a private source and normalize it to Markdown/plain text."""

    storage = storage or PrivateKnowledgeStorage()
    suffix = f".{knowledge_base.file_type}"
    with tempfile.NamedTemporaryFile(suffix=suffix) as temporary_file:
        storage.download(knowledge_base.file_key, temporary_file)
        from markitdown import MarkItDown

        content = MarkItDown().convert(temporary_file.name).text_content
    content = (content or "").strip()
    if not content:
        raise KnowledgeExtractionError("No readable text was found in the file.")
    return content
