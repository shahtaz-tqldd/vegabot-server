"""Re-rank retrieved knowledge with the Vertex AI Ranking API.

Calls the Discovery Engine ``rankingConfigs:rank`` REST endpoint with
Application Default Credentials. Needs the Discovery Engine API enabled
on the project and ``roles/discoveryengine.viewer`` for the caller.
"""

import threading
from dataclasses import dataclass

from app.constants import GOOGLE_RANKING_TIMEOUT_SECONDS
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured

RANKING_ENDPOINT = (
    "https://discoveryengine.googleapis.com/v1/projects/{project}/locations/"
    "{location}/rankingConfigs/default_ranking_config:rank"
)
MAX_RECORDS = 200
CLOUD_PLATFORM_SCOPE = "https://www.googleapis.com/auth/cloud-platform"

_session = None
_session_lock = threading.Lock()


def _authorized_session():
    """One process-wide authorized session, created on first use."""
    global _session
    if _session is None:
        with _session_lock:
            if _session is None:
                import google.auth
                from google.auth.transport.requests import AuthorizedSession

                credentials, _ = google.auth.default(scopes=[CLOUD_PLATFORM_SCOPE])
                _session = AuthorizedSession(credentials)
    return _session


@dataclass(frozen=True, slots=True)
class RankRecord:
    id: str
    content: str
    title: str | None = None


class GoogleRankingService:
    def __init__(
        self,
        *,
        project=None,
        location=None,
        model=None,
        timeout=None,
        session=None,
    ):
        self.project = project or settings.GOOGLE_CLOUD_PROJECT_ID
        if not self.project:
            raise ImproperlyConfigured(
                "GOOGLE_CLOUD_PROJECT_ID is required for re-ranking."
            )
        self.location = location or settings.GOOGLE_CLOUD_LOCATION
        self.model = model or settings.GOOGLE_RANKING_MODEL
        self.timeout = timeout or GOOGLE_RANKING_TIMEOUT_SECONDS
        self._session = session

    @property
    def session(self):
        return self._session or _authorized_session()

    def rank(self, query, records):
        """Score ``records`` against ``query``. Returns ``{id: score}``.

        Records the API leaves out of the response are absent from the
        mapping.
        """
        records = list(records)[:MAX_RECORDS]
        if not records:
            return {}
        payload = {
            "model": self.model,
            "query": query,
            "topN": len(records),
            "ignoreRecordDetailsInResponse": True,
            "records": [self._record_payload(record) for record in records],
        }
        response = self.session.post(
            RANKING_ENDPOINT.format(project=self.project, location=self.location),
            json=payload,
            timeout=self.timeout,
        )
        response.raise_for_status()
        return {
            str(item["id"]): float(item.get("score", 0.0))
            for item in response.json().get("records", [])
            if "id" in item
        }

    @staticmethod
    def _record_payload(record):
        payload = {"id": record.id, "content": record.content}
        if record.title:
            payload["title"] = record.title
        return payload
