from contextlib import asynccontextmanager
from contextvars import ContextVar

from django.conf import settings
from google.adk.models import Gemini
from google.genai import Client, types


_VERTEX_CLIENT_SCOPE = ContextVar("vertex_client_scope", default=None)


@asynccontextmanager
async def vertex_client_scope():
    """Own one lazy HTTP pool for a turn, and close it on its owning loop."""
    scope = {"client": None}
    token = _VERTEX_CLIENT_SCOPE.set(scope)
    try:
        yield
    finally:
        try:
            client = scope["client"]
            if client is not None:
                try:
                    await client.aio.aclose()
                finally:
                    client.close()
        finally:
            _VERTEX_CLIENT_SCOPE.reset(token)


def vertex_client():
    """Share a client across agents in this turn, never across event loops."""
    scope = _VERTEX_CLIENT_SCOPE.get()
    if scope is None:
        raise RuntimeError("Vertex requests require an active vertex_client_scope.")
    if scope["client"] is None:
        scope["client"] = Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT_ID,
            location=settings.GOOGLE_CLOUD_LOCATION,
        )
    return scope["client"]


class ChatGemini(Gemini):
    """Reuse the turn's Vertex client across coordinator and specialists."""

    @property
    def api_client(self):
        return vertex_client()


def chat_model():
    return ChatGemini(model=settings.GEMINI_CHAT_MODEL)


def live_model(*, model_name=None):
    from base.models import DisonConfig

    configured = model_name
    if not configured:
        config = DisonConfig.objects.first()
        live_config = getattr(config, "live_llm_model", None)
        if isinstance(live_config, dict):
            configured = live_config.get("model_name")
    return ChatGemini(model=configured or settings.GEMINI_CHAT_MODEL)


def generation_config(max_output_tokens=None):
    return types.GenerateContentConfig(
        temperature=0.2,
        max_output_tokens=(
            max_output_tokens
            if max_output_tokens is not None
            else settings.GEMINI_CHAT_MAX_OUTPUT_TOKENS
        ),
    )
