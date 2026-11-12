import io
import wave

import numpy as np
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase
from unittest.mock import patch

from chatbot.models import Chatbot, ChatbotVoice, VoicePreview
from chatbot.services.voice_audio import (
    VoiceAudioError,
    clip_wav,
    normalize_wav,
)
from chatbot.services.voices import (
    VoiceError,
    create_custom_voice,
    delete_custom_voice,
    live_speech_config,
    select_voice,
    selected_voice,
)

User = get_user_model()

OPTIONS_URL = reverse("chatbot-voice-options")
PREVIEW_URL = reverse("chatbot-voice-preview")
LIST_URL = reverse("chatbot-voice-list")
SELECT_URL = reverse("chatbot-voice-select")
CREATE_URL = reverse("chatbot-custom-voice-create")
DELETE_URL = reverse("chatbot-custom-voice-delete")
DETAIL_URL = reverse("chatbot-detail")


def make_wav(seconds, *, rate=24000, channels=1, width=2, silent=False):
    frames = int(seconds * rate)
    t = np.arange(frames) / rate
    signal = np.zeros(frames) if silent else 0.3 * np.sin(2 * np.pi * 220 * t)
    if width == 1:
        data = ((signal + 1) * 127.5).astype(np.uint8)
    else:
        data = (signal * 32767).astype("<i2")
    data = np.repeat(data[:, None], channels, axis=1).tobytes()
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as wav:
        wav.setnchannels(channels)
        wav.setsampwidth(width)
        wav.setframerate(rate)
        wav.writeframes(data)
    return buffer.getvalue()


def upload(name, data):
    return SimpleUploadedFile(name, data, content_type="audio/wav")


class FakeStorage:
    objects = {}

    def __init__(self, *args, **kwargs):
        pass

    def upload(self, file_obj, *, key, content_type=None, cache_control=None):
        file_obj.seek(0)
        FakeStorage.objects[key] = file_obj.read()

    def download(self, key, file_obj):
        file_obj.write(FakeStorage.objects[key])
        file_obj.seek(0)

    def delete(self, key):
        FakeStorage.objects.pop(key, None)

    def private_url(self, key, download_file_name=None):
        return f"https://private.example.com/{key}?signed"

    def public_url(self, key):
        return f"https://public.example.com/{key}"


class StorageMixin:
    def setUp(self):
        super().setUp()
        cache.clear()
        FakeStorage.objects = {}
        for target in (
            "chatbot.services.voices.R2Storage",
            "chatbot.api.v1.client.voice_views.R2Storage",
        ):
            patcher = patch(target, FakeStorage)
            patcher.start()
            self.addCleanup(patcher.stop)


class VoiceAudioTests(TestCase):
    def test_normalizes_any_pcm_wav_to_24k_mono_16bit(self):
        wav_bytes, duration = normalize_wav(
            io.BytesIO(make_wav(2, rate=44100, channels=2, width=1)),
        )

        with wave.open(io.BytesIO(wav_bytes)) as wav:
            self.assertEqual(wav.getframerate(), 24000)
            self.assertEqual(wav.getnchannels(), 1)
            self.assertEqual(wav.getsampwidth(), 2)
        self.assertAlmostEqual(duration, 2, places=2)

    def test_rejects_non_wav_and_silence(self):
        with self.assertRaises(VoiceAudioError):
            normalize_wav(io.BytesIO(b"not a wav file at all"))
        with self.assertRaises(VoiceAudioError):
            normalize_wav(io.BytesIO(make_wav(1, silent=True)))

    def test_clip(self):
        wav_bytes, _ = normalize_wav(io.BytesIO(make_wav(30)))
        with wave.open(io.BytesIO(clip_wav(wav_bytes, 20))) as wav:
            self.assertAlmostEqual(wav.getnframes() / wav.getframerate(), 20)


class VoiceServiceTests(StorageMixin, TestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(email="voice@example.com", password="x")
        self.chatbot = Chatbot.objects.create(
            chatbot_name="Voice Bot", business_type="retail", created_by=self.user,
        )

    def create(self, sample_seconds=15, consent_seconds=5, name="Founder"):
        return create_custom_voice(
            chatbot=self.chatbot,
            name=name,
            gender="female",
            sample_audio=upload("sample.wav", make_wav(sample_seconds, rate=48000)),
            consent_audio=upload("consent.wav", make_wav(consent_seconds)),
            user=self.user,
        )

    def test_create_stores_both_recordings(self):
        voice = self.create()

        self.assertIn(voice.sample_audio_key, FakeStorage.objects)
        self.assertIn(voice.consent_audio_key, FakeStorage.objects)
        self.assertAlmostEqual(voice.sample_duration_seconds, 15, places=1)
        self.assertIn("I consent", voice.consent_statement)

    def test_sample_length_limits(self):
        for seconds in (5, 61):
            with self.assertRaises(VoiceError):
                self.create(sample_seconds=seconds)
        self.assertEqual(FakeStorage.objects, {})

    @patch("chatbot.services.voices.MAX_CUSTOM_VOICES_PER_CHATBOT", 1)
    def test_custom_voice_limit(self):
        self.create()
        with self.assertRaises(VoiceError):
            self.create(name="Second")

    def test_duplicate_name_cleans_up_uploads(self):
        self.create()
        with self.assertRaises(VoiceError):
            self.create()
        self.assertEqual(len(FakeStorage.objects), 2)

    def test_custom_voice_speech_config_clips_the_sample(self):
        voice = self.create(sample_seconds=45)
        select_voice(self.chatbot, voice_type="custom", custom_voice=voice)

        config = live_speech_config(Chatbot.objects.get(pk=self.chatbot.pk))

        replicated = config.voice_config.replicated_voice_config
        self.assertEqual(replicated.mime_type, "audio/wav")
        with wave.open(io.BytesIO(replicated.voice_sample_audio)) as wav:
            self.assertAlmostEqual(wav.getnframes() / wav.getframerate(), 20)
        self.assertEqual(
            replicated.consent_audio, FakeStorage.objects[voice.consent_audio_key],
        )

    def test_unavailable_custom_voice_falls_back_to_prebuilt(self):
        voice = self.create()
        select_voice(self.chatbot, voice_type="custom", custom_voice=voice)
        FakeStorage.objects.clear()
        self.chatbot.voice_name = "Kore"

        config = live_speech_config(self.chatbot)

        self.assertEqual(
            config.voice_config.prebuilt_voice_config.voice_name, "Kore",
        )

    def test_delete_falls_back_to_prebuilt(self):
        voice = self.create()
        select_voice(self.chatbot, voice_type="custom", custom_voice=voice)

        with self.captureOnCommitCallbacks(execute=True):
            delete_custom_voice(voice)

        self.chatbot.refresh_from_db()
        self.assertIsNone(self.chatbot.custom_voice)
        self.assertEqual(FakeStorage.objects, {})
        self.assertEqual(selected_voice(self.chatbot)["type"], "prebuilt")

    def test_selected_voice_defaults(self):
        self.assertEqual(
            selected_voice(self.chatbot),
            selected_voice(self.chatbot) | {"name": "Kore", "is_default": True},
        )
        select_voice(self.chatbot, voice_type="prebuilt", voice_name="kore")
        chosen = selected_voice(self.chatbot)
        self.assertEqual((chosen["name"], chosen["gender"]), ("Kore", "female"))
        self.assertFalse(chosen["is_default"])


class VoiceAPITests(StorageMixin, APITestCase):
    def setUp(self):
        super().setUp()
        self.user = User.objects.create_user(email="owner@example.com", password="x")
        self.other = User.objects.create_user(email="other@example.com", password="x")
        self.chatbot = Chatbot.objects.create(
            chatbot_name="Shop", business_type="retail", created_by=self.user,
        )
        self.client.force_authenticate(self.user)

    def create_voice(self, **overrides):
        data = {
            "name": "Founder",
            "gender": "male",
            "sample_audio": upload("sample.wav", make_wav(12)),
            "consent_audio": upload("consent.wav", make_wav(4)),
            "consent_confirmed": "true",
            **overrides,
        }
        return self.client.post(
            f"{CREATE_URL}?chatbot={self.chatbot.slug}", data, format="multipart",
        )

    def test_options_lists_ten_voices_five_per_gender(self):
        response = self.client.get(OPTIONS_URL)

        voices = response.data["data"]["prebuilt_voices"]
        self.assertEqual(len(voices), 10)
        self.assertEqual(sum(v["gender"] == "male" for v in voices), 5)
        self.assertEqual(
            [p["value"] for p in response.data["data"]["speaking_paces"]],
            ["slow", "normal", "fast"],
        )
        self.assertIn("consent_statement", response.data["data"]["custom_voice"])

    def test_preview_generated_once_then_cached(self):
        with patch(
            "chatbot.services.voices._speak_with_timeout", return_value=b"\x00\x01" * 100,
        ) as speak, patch(
            "chatbot.services.voices._live_model_name", return_value="gemini-live",
        ):
            first = self.client.get(PREVIEW_URL, {"voice": "kore"})
            second = self.client.get(PREVIEW_URL, {"voice": "Kore"})

        self.assertEqual(first.status_code, status.HTTP_200_OK)
        self.assertEqual(first.data["data"]["preview_url"], second.data["data"]["preview_url"])
        self.assertEqual(speak.call_count, 1)
        self.assertTrue(VoicePreview.objects.filter(voice_name="Kore").exists())
        options = self.client.get(OPTIONS_URL).data["data"]["prebuilt_voices"]
        kore = next(v for v in options if v["name"] == "Kore")
        self.assertEqual(kore["preview_url"], first.data["data"]["preview_url"])

    def test_preview_rejects_unknown_voice(self):
        response = self.client.get(PREVIEW_URL, {"voice": "Nobody"})
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_create_select_list_and_delete_custom_voice(self):
        created = self.create_voice()
        self.assertEqual(created.status_code, status.HTTP_201_CREATED)
        voice_id = created.data["data"]["id"]
        self.assertTrue(created.data["data"]["sample_url"])

        selected = self.client.patch(
            f"{SELECT_URL}?chatbot={self.chatbot.slug}",
            {"voice_type": "custom", "custom_voice": voice_id, "speaking_pace": "slow"},
            format="json",
        )
        self.assertEqual(selected.status_code, status.HTTP_200_OK)
        self.assertEqual(selected.data["data"]["selected"]["id"], voice_id)

        listed = self.client.get(LIST_URL, {"chatbot": self.chatbot.slug}).data["data"]
        self.assertEqual(listed["speaking_pace"], "slow")
        self.assertTrue(listed["custom_voices"][0]["is_selected"])

        detail = self.client.get(DETAIL_URL, {"chatbot": self.chatbot.slug}).data["data"]
        self.assertEqual(detail["voice"]["name"], "Founder")
        self.assertEqual(detail["speaking_pace"], "slow")

        deleted = self.client.delete(
            f"{DELETE_URL}?chatbot={self.chatbot.slug}&voice={voice_id}",
        )
        self.assertEqual(deleted.status_code, status.HTTP_200_OK)
        self.assertFalse(ChatbotVoice.objects.exists())

    def test_create_requires_consent_confirmation(self):
        response = self.create_voice(consent_confirmed="false")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertIn("consent_confirmed", response.data["errors"])

    def test_create_rejects_short_sample(self):
        response = self.create_voice(sample_audio=upload("s.wav", make_wav(3)))
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_select_prebuilt_validates_name(self):
        bad = self.client.patch(
            f"{SELECT_URL}?chatbot={self.chatbot.slug}",
            {"voice_type": "prebuilt", "voice_name": "Nobody"},
            format="json",
        )
        good = self.client.patch(
            f"{SELECT_URL}?chatbot={self.chatbot.slug}",
            {"voice_type": "prebuilt", "voice_name": "charon"},
            format="json",
        )
        self.assertEqual(bad.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(good.data["data"]["selected"]["name"], "Charon")

    def test_other_users_cannot_manage_voices(self):
        self.client.force_authenticate(self.other)

        created = self.create_voice()
        selected = self.client.patch(
            f"{SELECT_URL}?chatbot={self.chatbot.slug}",
            {"voice_type": "prebuilt", "voice_name": "Kore"},
            format="json",
        )

        self.assertEqual(created.status_code, status.HTTP_404_NOT_FOUND)
        self.assertEqual(selected.status_code, status.HTTP_404_NOT_FOUND)

    def test_pace_updates_through_chatbot_update(self):
        response = self.client.patch(
            f"{reverse('chatbot-update')}?chatbot={self.chatbot.slug}",
            {"speaking_pace": "fast"},
            format="json",
        )
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.chatbot.refresh_from_db()
        self.assertEqual(self.chatbot.speaking_pace, "fast")
