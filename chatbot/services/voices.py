"""Chatbot voices: custom voices in R2, voice selection, the Live speech
config for a chatbot, and listening previews of the prebuilt voices.

Custom voices use Gemini Live's per-session voice replication: no voice
is trained in advance; each voice session sends the stored sample (and
the owner's consent recording) in ``replicated_voice_config``.
"""

import asyncio
import io
import logging
from uuid import uuid4

from asgiref.sync import async_to_sync
from django.conf import settings
from django.core.cache import cache
from django.db import IntegrityError, transaction
from google import genai
from google.genai import types

from app.constants import (
    CUSTOM_VOICE_LIVE_SAMPLE_SECONDS,
    CUSTOM_VOICE_MAX_CONSENT_SECONDS,
    CUSTOM_VOICE_MAX_SAMPLE_SECONDS,
    CUSTOM_VOICE_MAX_UPLOAD_MB,
    CUSTOM_VOICE_MIN_SAMPLE_SECONDS,
    GEMINI_LIVE_CHAT_VOICE,
    MAX_CUSTOM_VOICES_PER_CHATBOT,
    R2_FILES_PREFIX,
)
from app.services.r2 import R2Storage
from chatbot.models import ChatbotVoice, VoicePreview
from chatbot.services.voice_audio import (
    WAV_MIME_TYPE,
    clip_wav,
    normalize_wav,
    pcm_to_wav,
)
from chatbot.utils.voices import (
    CONSENT_STATEMENT,
    PREVIEW_TEXT,
    VoiceType,
    prebuilt_voice,
)

logger = logging.getLogger(__name__)

AUDIO_CACHE_SECONDS = 60 * 60
PREVIEW_TIMEOUT_SECONDS = 30
BYTES_PER_MEGABYTE = 1024 * 1024


class VoiceError(ValueError):
    """A voice request the client should correct."""


# ----------------------------------------------------------------- storage
def _voice_key(chatbot, kind):
    prefix = R2_FILES_PREFIX.strip("/")
    return f"{prefix}/voices/{chatbot.pk}/{uuid4().hex}-{kind}.wav"


def _upload_wav(storage, wav_bytes, key):
    storage.upload(io.BytesIO(wav_bytes), key=key, content_type=WAV_MIME_TYPE)


def _schedule_delete(*keys):
    def _delete():
        storage = R2Storage()
        for key in keys:
            try:
                storage.delete(key)
            except Exception:
                logger.exception("Could not delete voice audio %s", key)

    transaction.on_commit(_delete)


def voice_audio(key):
    """A stored voice recording's bytes, cached for repeated sessions."""
    cache_key = f"voice-audio:{key}"
    data = cache.get(cache_key)
    if data is None:
        buffer = io.BytesIO()
        R2Storage().download(key, buffer)
        data = buffer.getvalue()
        cache.set(cache_key, data, AUDIO_CACHE_SECONDS)
    return data


def sample_url(voice, storage=None):
    """A short-lived link to listen to a custom voice's sample."""
    return (storage or R2Storage()).private_url(voice.sample_audio_key)


# ----------------------------------------------------------- custom voices
def _normalized(upload, *, label, min_seconds, max_seconds):
    if upload.size > CUSTOM_VOICE_MAX_UPLOAD_MB * BYTES_PER_MEGABYTE:
        raise VoiceError(
            f"The {label} must be under "
            f"{CUSTOM_VOICE_MAX_UPLOAD_MB:g} MB."
        )
    try:
        wav_bytes, duration = normalize_wav(upload)
    except ValueError as exc:
        raise VoiceError(f"The {label}: {exc}") from exc
    if duration < min_seconds or duration > max_seconds:
        raise VoiceError(
            f"The {label} must be {min_seconds:g}-{max_seconds:g} seconds "
            f"long (got {duration:.1f} s)."
        )
    return wav_bytes, duration


def create_custom_voice(
    *, chatbot, name, gender, sample_audio, consent_audio, user, storage=None,
):
    """Store a custom voice from its sample and consent recordings."""
    if chatbot.custom_voices.count() >= MAX_CUSTOM_VOICES_PER_CHATBOT:
        raise VoiceError(
            f"A chatbot can have at most "
            f"{MAX_CUSTOM_VOICES_PER_CHATBOT} custom voices."
        )
    sample_bytes, sample_seconds = _normalized(
        sample_audio,
        label="voice sample",
        min_seconds=CUSTOM_VOICE_MIN_SAMPLE_SECONDS,
        max_seconds=CUSTOM_VOICE_MAX_SAMPLE_SECONDS,
    )
    consent_bytes, consent_seconds = _normalized(
        consent_audio,
        label="consent recording",
        min_seconds=2,
        max_seconds=CUSTOM_VOICE_MAX_CONSENT_SECONDS,
    )

    storage = storage or R2Storage()
    sample_key = _voice_key(chatbot, "sample")
    consent_key = _voice_key(chatbot, "consent")
    _upload_wav(storage, sample_bytes, sample_key)
    try:
        _upload_wav(storage, consent_bytes, consent_key)
        with transaction.atomic():
            return ChatbotVoice.objects.create(
                chatbot=chatbot,
                name=name,
                gender=gender,
                sample_audio_key=sample_key,
                sample_duration_seconds=round(sample_seconds, 2),
                consent_audio_key=consent_key,
                consent_duration_seconds=round(consent_seconds, 2),
                consent_statement=CONSENT_STATEMENT,
                created_by=user,
            )
    except IntegrityError as exc:
        storage.delete(sample_key)
        storage.delete(consent_key)
        raise VoiceError("This chatbot already has a voice with this name.") from exc
    except Exception:
        storage.delete(sample_key)
        storage.delete(consent_key)
        raise


@transaction.atomic
def delete_custom_voice(voice):
    """Delete a custom voice and its recordings; a chatbot using it falls
    back to its prebuilt voice."""
    keys = (voice.sample_audio_key, voice.consent_audio_key)
    voice.delete()  # Chatbot.custom_voice is SET_NULL
    _schedule_delete(*keys)
    for key in keys:
        cache.delete(f"voice-audio:{key}")


def select_voice(chatbot, *, voice_type, voice_name="", custom_voice=None, user=None):
    """Make a prebuilt or custom voice the chatbot's current voice."""
    if voice_type == VoiceType.CUSTOM:
        chatbot.custom_voice = custom_voice
    else:
        chatbot.custom_voice = None
        chatbot.voice_name = prebuilt_voice(voice_name)["name"]
    chatbot.updated_by = user
    chatbot.save(
        update_fields=["voice_name", "custom_voice", "updated_by", "updated_at"],
    )
    return chatbot


# ---------------------------------------------------------- representation
def prebuilt_voice_payload(entry, preview_urls=None):
    return {
        "type": VoiceType.PREBUILT,
        "name": entry["name"],
        "gender": entry["gender"],
        "style": entry["style"],
        "description": entry["description"],
        "preview_url": (preview_urls or {}).get(entry["name"]),
    }


def custom_voice_payload(voice, *, include_sample=False, storage=None):
    data = {
        "type": VoiceType.CUSTOM,
        "id": str(voice.pk),
        "name": voice.name,
        "gender": voice.gender,
        "sample_duration_seconds": voice.sample_duration_seconds,
        "created_at": voice.created_at,
    }
    if include_sample:
        try:
            data["sample_url"] = sample_url(voice, storage)
        except Exception:
            logger.exception("No sample URL for voice %s", voice.pk)
            data["sample_url"] = None
    return data


def default_voice_name():
    return prebuilt_voice(GEMINI_LIVE_CHAT_VOICE) or prebuilt_voice("Kore")


def selected_voice(chatbot):
    """The voice the chatbot speaks with right now."""
    if chatbot.custom_voice_id:
        return custom_voice_payload(chatbot.custom_voice)
    entry = prebuilt_voice(chatbot.voice_name)
    if entry is not None:
        return prebuilt_voice_payload(entry) | {"is_default": False}
    configured = (chatbot.voice_name or GEMINI_LIVE_CHAT_VOICE).strip()
    entry = prebuilt_voice(configured)
    if entry is not None:
        return prebuilt_voice_payload(entry) | {"is_default": True}
    # A configured voice outside the curated list.
    return {
        "type": VoiceType.PREBUILT,
        "name": configured,
        "gender": None,
        "style": "",
        "description": "",
        "preview_url": None,
        "is_default": True,
    }


def preview_urls():
    return dict(VoicePreview.objects.values_list("voice_name", "audio_url"))


# --------------------------------------------------------------- live config
def prebuilt_speech_config(voice_name):
    voice_name = (voice_name or "").strip()
    if not voice_name:
        return None
    return types.SpeechConfig(
        voice_config=types.VoiceConfig(
            prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=voice_name),
        ),
    )


def live_speech_config(chatbot, *, default_voice=None):
    """The speech config for the chatbot's selected voice (sync: may read
    R2). A custom voice that cannot be loaded falls back to prebuilt."""
    voice = chatbot.custom_voice if chatbot.custom_voice_id else None
    if voice is not None:
        try:
            sample = clip_wav(
                voice_audio(voice.sample_audio_key),
                CUSTOM_VOICE_LIVE_SAMPLE_SECONDS,
            )
            return types.SpeechConfig(
                voice_config=types.VoiceConfig(
                    replicated_voice_config=types.ReplicatedVoiceConfig(
                        mime_type=WAV_MIME_TYPE,
                        voice_sample_audio=sample,
                        consent_audio=voice_audio(voice.consent_audio_key),
                    ),
                ),
            )
        except Exception:
            logger.exception(
                "Custom voice %s unavailable; using the prebuilt voice", voice.pk,
            )
    return prebuilt_speech_config(
        chatbot.voice_name or default_voice or GEMINI_LIVE_CHAT_VOICE,
    )


# ------------------------------------------------------------------ previews
def _live_model_name():
    from agent.helpers.model import load_model_config, model_name_for
    from chat.utils.choices import ChatMode

    return model_name_for(ChatMode.AUDIO, load_model_config(ChatMode.AUDIO))


async def _speak(model, voice_name, text):
    """One short Live turn speaking ``text``; returns 24 kHz PCM."""
    client = genai.Client(
        vertexai=True,
        project=settings.GOOGLE_CLOUD_PROJECT_ID,
        location=(
            settings.GOOGLE_CLOUD_LIVE_CHAT_LOCATION
            or settings.GOOGLE_CLOUD_LOCATION
        ),
    )
    config = types.LiveConnectConfig(
        response_modalities=["AUDIO"],
        speech_config=prebuilt_speech_config(voice_name),
        system_instruction=(
            "Read the user's text aloud exactly as written, then stop. "
            "Do not add anything."
        ),
    )
    chunks = []
    try:
        async with client.aio.live.connect(model=model, config=config) as session:
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text=text)]),
                turn_complete=True,
            )
            async for message in session.receive():
                content = message.server_content
                if content is None:
                    continue
                for part in (content.model_turn.parts if content.model_turn else None) or []:
                    if part.inline_data and part.inline_data.data:
                        chunks.append(part.inline_data.data)
                if content.turn_complete:
                    break
    finally:
        await client.aio.aclose()
    return b"".join(chunks)


async def _speak_with_timeout(model, voice_name, text):
    return await asyncio.wait_for(
        _speak(model, voice_name, text), PREVIEW_TIMEOUT_SECONDS,
    )


def voice_preview_url(voice_name):
    """The listening sample of a prebuilt voice, generated once and kept
    in R2."""
    entry = prebuilt_voice(voice_name)
    if entry is None:
        raise VoiceError("Unknown voice.")
    existing = VoicePreview.objects.filter(voice_name=entry["name"]).first()
    if existing is not None:
        return existing.audio_url

    pcm = async_to_sync(_speak_with_timeout)(
        _live_model_name(),
        entry["name"],
        PREVIEW_TEXT.format(name=entry["name"]),
    )
    if not pcm:
        raise RuntimeError("The voice preview came back empty.")

    storage = R2Storage()
    prefix = R2_FILES_PREFIX.strip("/")
    key = f"{prefix}/voice-previews/{entry['name'].lower()}-{uuid4().hex[:8]}.wav"
    _upload_wav(storage, pcm_to_wav(pcm), key)
    preview, created = VoicePreview.objects.get_or_create(
        voice_name=entry["name"],
        defaults={"audio_url": storage.public_url(key), "audio_key": key},
    )
    if not created:  # generated concurrently; keep the first
        storage.delete(key)
    return preview.audio_url
