"""Voice options for the Live (voice-mode) agent.

A curated 10 of the 30 prebuilt Live voices (5 male, 5 female), the
speaking pace options, and the consent statement a custom voice's owner
must record.
"""

from django.db import models


class VoiceGender(models.TextChoices):
    MALE = "male", "Male"
    FEMALE = "female", "Female"


class VoiceType(models.TextChoices):
    PREBUILT = "prebuilt", "Prebuilt"
    CUSTOM = "custom", "Custom"


class SpeakingPace(models.TextChoices):
    SLOW = "slow", "Slow"
    NORMAL = "normal", "Normal"
    FAST = "fast", "Fast"


# Live has no speaking-rate parameter, so pace is steered through the
# voice instructions. (A custom voice also inherits its sample's pace.)
SPEAKING_PACE_INSTRUCTIONS = {
    SpeakingPace.SLOW: (
        "Speak slowly and calmly, with clear pauses between sentences."
    ),
    SpeakingPace.NORMAL: "",
    SpeakingPace.FAST: (
        "Speak at a brisk, fast pace without long pauses, while staying clear."
    ),
}


PREBUILT_VOICES = (
    {"name": "Kore", "gender": VoiceGender.FEMALE, "style": "Firm",
     "description": "Confident and steady; good for support and guidance."},
    {"name": "Aoede", "gender": VoiceGender.FEMALE, "style": "Breezy",
     "description": "Light and relaxed; good for casual brands."},
    {"name": "Leda", "gender": VoiceGender.FEMALE, "style": "Youthful",
     "description": "Fresh and energetic; good for lifestyle and retail."},
    {"name": "Zephyr", "gender": VoiceGender.FEMALE, "style": "Bright",
     "description": "Cheerful and clear; good for greetings and sales."},
    {"name": "Sulafat", "gender": VoiceGender.FEMALE, "style": "Warm",
     "description": "Friendly and reassuring; good for care and hospitality."},
    {"name": "Puck", "gender": VoiceGender.MALE, "style": "Upbeat",
     "description": "Lively and positive; the default assistant voice."},
    {"name": "Charon", "gender": VoiceGender.MALE, "style": "Informative",
     "description": "Calm and clear; good for explaining details."},
    {"name": "Fenrir", "gender": VoiceGender.MALE, "style": "Excitable",
     "description": "Energetic and enthusiastic; good for promotions."},
    {"name": "Orus", "gender": VoiceGender.MALE, "style": "Firm",
     "description": "Grounded and authoritative; good for professional services."},
    {"name": "Achird", "gender": VoiceGender.MALE, "style": "Friendly",
     "description": "Approachable and easy to talk to; good for customer care."},
)

PREBUILT_VOICE_NAMES = frozenset(voice["name"] for voice in PREBUILT_VOICES)


def prebuilt_voice(name):
    """The catalog entry for ``name`` (case-insensitive), or None."""
    name = (name or "").strip().lower()
    return next(
        (voice for voice in PREBUILT_VOICES if voice["name"].lower() == name),
        None,
    )


# The statement the voice owner reads aloud for the consent recording
# (Google's English consent wording for replicated voices).
CONSENT_STATEMENT = (
    "I am the owner of this voice and I consent to Google using this voice "
    "to create a synthetic voice model."
)

# A suggested script for the voice sample: natural, varied speech that
# reads in about 45-60 seconds. Live uses the first 20 seconds.
SAMPLE_SCRIPT = (
    "Hi there, and thanks for stopping by. I'm here to help you find "
    "exactly what you need, whether that's a quick answer, a product "
    "recommendation, or a little guidance on where to start. Every "
    "customer is different, so tell me what you're looking for and I'll "
    "do my best to point you in the right direction. If you have a "
    "question about pricing, delivery, or how something works, just ask. "
    "And if I can't answer it myself, I'll make sure someone from our team "
    "follows up with you. I really enjoy these conversations, because "
    "every one of them teaches me something new. So, what can I help you "
    "with today?"
)

PREVIEW_TEXT = (
    "Hi, I'm {name}. Thanks for reaching out. How can I help you today?"
)
