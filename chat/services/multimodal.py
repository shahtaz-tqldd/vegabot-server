"""Multimodal content assembly for chat messages.

``build_message_parts`` turns a chat message into the ordered multimodal
parts the agent consumes and the knowledge module can embed:

- ``text``       the message body
- ``image``      image attachments, passed by URL for visual answering
- ``file_text``  extracted document text so the chatbot can read files
"""

from chat.utils.choices import AttachmentProcessStatus, ChatMessageAttachmentType


def build_message_parts(message, *, max_file_text_chars=20_000):
    """Return the multimodal parts for one chat message."""
    parts = []
    if message.content and message.content.strip():
        parts.append({"type": "text", "text": message.content.strip()})

    attachments = message.attachments.order_by("created_at", "id")
    for attachment in attachments:
        if attachment.attachment_type == ChatMessageAttachmentType.IMAGE:
            parts.append(
                {
                    "type": "image",
                    "url": attachment.file_url,
                    "mime_type": attachment.mime_type,
                    "name": attachment.file_name,
                }
            )
        elif attachment.extracted_text:
            parts.append(
                {
                    "type": "file_text",
                    "name": attachment.file_name,
                    "text": attachment.extracted_text[:max_file_text_chars],
                }
            )

    return parts
