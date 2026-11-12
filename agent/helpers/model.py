from contextlib import asynccontextmanager
from contextvars import ContextVar

from django.conf import settings
from google.adk.models import Gemini
from google.genai import Client, types

from app.constants import GEMINI_CHAT_MAX_OUTPUT_TOKENS, GEMINI_LIVE_CHAT_VOICE
from chat.utils.choices import ChatMode


_VERTEX_CLIENT_SCOPE = ContextVar("vertex_client_scope", default=None)


@asynccontextmanager
async def vertex_client_scope(location=None):
    """Own one lazy HTTP pool for a turn, and close it on its owning loop.

    ``location`` overrides the default Vertex location for every client
    created inside the scope (live chat uses the live-chat location).
    """
    scope = {"client": None, "location": location}
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
            for extra in scope.get("extra_clients") or ():
                try:
                    await extra.aio.aclose()
                finally:
                    extra.close()
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
            location=scope["location"] or settings.GOOGLE_CLOUD_LOCATION,
        )
    return scope["client"]


class ChatGemini(Gemini):
    """Reuse the turn's Vertex client across coordinator and specialists."""

    @property
    def api_client(self):
        return vertex_client()

    @property
    def _live_api_client(self):
        """Live runs need their own client at the scope's location.

        ADK's default live client carries no project/location, so live
        requests resolve to the wrong region. Build it here the ADK way
        (tracking headers + live api version) but with the scope's
        location; it is closed with the scope.
        """
        scope = _VERTEX_CLIENT_SCOPE.get()
        if scope is None:
            raise RuntimeError("Vertex requests require an active vertex_client_scope.")
        client = Client(
            vertexai=True,
            project=settings.GOOGLE_CLOUD_PROJECT_ID,
            location=scope["location"] or settings.GOOGLE_CLOUD_LOCATION,
            http_options=types.HttpOptions(
                headers=self._tracking_headers(),
                api_version=self._live_api_version,
                base_url=self._base_url_and_api_version[0],
            ),
        )
        scope.setdefault("extra_clients", []).append(client)
        return client


def _as_dict(model_config):
    return model_config if isinstance(model_config, dict) else {}


def model_name_for(mode, model_config=None):
    """The model a mode runs: VegabotConfig override, else the setting."""
    default = (
        settings.GEMINI_LIVE_CHAT_MODEL
        if mode == ChatMode.AUDIO
        else settings.GEMINI_CHAT_MODEL
    )
    return _as_dict(model_config).get("model_name") or default


def chat_model(*, model_config=None):
    """The (cheap) text model; ``model_config`` is VegabotConfig
    ``text_llm_model``."""
    return ChatGemini(model=model_name_for(ChatMode.TEXT, model_config))


def live_model(*, model_config=None, speech_config=None):
    """The (costly) live model with its voice; ``model_config`` is
    VegabotConfig ``live_llm_model``. ``speech_config`` is the chatbot's
    selected voice; without it, the configured default voice is used."""
    model_config = _as_dict(model_config)
    return ChatGemini(
        model=model_name_for(ChatMode.AUDIO, model_config),
        speech_config=speech_config
        or build_speech_config(model_config.get("voice_name")),
    )


def build_model(mode, *, model_config=None, speech_config=None):
    if mode == ChatMode.AUDIO:
        return live_model(model_config=model_config, speech_config=speech_config)
    return chat_model(model_config=model_config)


def load_model_config(mode):
    """Per-mode model overrides from the Vegabot singleton (sync ORM)."""
    from base.models import VegabotConfig

    config = VegabotConfig.objects.first()
    if config is None:
        return {}
    field = "live_llm_model" if mode == ChatMode.AUDIO else "text_llm_model"
    return _as_dict(getattr(config, field, None))


def vertex_location_for(mode):
    """Live models are served from their own Vertex location."""
    if mode == ChatMode.AUDIO:
        return settings.GOOGLE_CLOUD_LIVE_CHAT_LOCATION or None
    return None


def build_speech_config(voice_name=None):
    """Voice selection for the live agent; None keeps the API default."""
    voice = (voice_name or GEMINI_LIVE_CHAT_VOICE or "").strip()
    if not voice:
        return None
    return types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(
                voice_name=voice,
            ),
        ),
    )


def generation_config(max_output_tokens=None):
    return types.GenerateContentConfig(
        temperature=0.2,
        max_output_tokens=(
            max_output_tokens
            if max_output_tokens is not None
            else GEMINI_CHAT_MAX_OUTPUT_TOKENS
        ),
    )
