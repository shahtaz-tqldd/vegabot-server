from pathlib import Path
from uuid import uuid4

from django.conf import settings

from app.services.r2 import R2Storage


class PrivateKnowledgeStorage(R2Storage):
    """Store private knowledge sources in R2 and issue short-lived links."""

    @staticmethod
    def build_key(*, chatbot_id, filename):
        safe_name = Path(filename).name.replace(" ", "_")
        prefix = settings.R2_FILES_PREFIX.strip("/")
        return f"{prefix}/{chatbot_id}/{uuid4().hex}/{safe_name}"
