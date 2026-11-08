"""Best-effort multimodal processing of chat message attachments.

Images are stored as-is and marked processed immediately: the multimodal
agent consumes them visually, so there is nothing to extract here.
Documents are converted to text with ``markitdown`` (pdf, docx, xlsx,
csv, markdown, plain text, ...) and the result is persisted on the
attachment so the chatbot can answer from the file contents.
"""

import tempfile
from pathlib import Path

from chat.utils.choices import AttachmentProcessStatus, ChatMessageAttachmentType

MAX_EXTRACTED_TEXT_CHARS = 200_000


def _extract_document_text(source, *, file_name="") -> str:
    """Convert an uploaded document to plain text using markitdown."""
    from markitdown import MarkItDown

    # markitdown selects its converter from the file extension.
    suffix = Path(file_name or "").suffix or ".txt"
    with tempfile.NamedTemporaryFile(suffix=suffix) as temporary_file:
        temporary_file.write(source.read())
        temporary_file.flush()
        return (MarkItDown().convert(temporary_file.name).text_content or "").strip()


def process_attachment(attachment, *, source=None):
    """Process one attachment in place and persist its status.

    ``source`` is the in-memory uploaded file; when omitted the
    attachment is only re-classified (already uploaded files).
    Failures never raise: the attachment is marked failed with the
    error message so the chat can continue.
    """
    try:
        if attachment.attachment_type == ChatMessageAttachmentType.DOCUMENT:
            if source is None:
                # Nothing new to read; keep whatever was extracted before.
                if attachment.status != AttachmentProcessStatus.FAILED:
                    attachment.status = AttachmentProcessStatus.PROCESSED
                attachment.save(
                    update_fields=["status", "updated_at"],
                )
                return attachment

            extracted = _extract_document_text(
                source, file_name=attachment.file_name,
            )
            if not extracted:
                raise ValueError("No readable text was found in the file.")
            attachment.extracted_text = extracted[:MAX_EXTRACTED_TEXT_CHARS]
            attachment.status = AttachmentProcessStatus.PROCESSED
            attachment.error_message = ""
        else:
            # Images are consumed visually by the multimodal agent;
            # audio/video transcription arrives with the agent module.
            attachment.status = AttachmentProcessStatus.PROCESSED
    except Exception as exc:  # noqa: BLE001 - chat must survive bad files
        attachment.status = AttachmentProcessStatus.FAILED
        attachment.error_message = str(exc)[:500]

    attachment.save(update_fields=["status", "extracted_text", "error_message", "updated_at"])
    return attachment
