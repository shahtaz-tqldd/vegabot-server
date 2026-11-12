from django.db import models
from django.utils.text import slugify

from app.core.models import BaseModel
from app.utils.validators import validate_timezone_name
from chatbot.utils.choices import ChatbotStatusTypes, ChatbotToneTypes
from chatbot.utils.validation import validate_other_settings
from chatbot.utils.voices import SpeakingPace, VoiceGender


DEFAULT_CHATBOT_WELCOME_MESSAGE_TEMPLATE = (
    "Hey, I am {chatbot_name}, I am here to answer anything you want to know"
)
DEFAULT_CHATBOT_FALLBACK_MESSAGE = (
    "Sorry, I couldn't find anything to my knowledge to answer this question, "
    "should I connect with you one of our human assistant?"
)
DEFAULT_CHATBOT_ESCALATION_RULE = (
    "Hand off to human agent, when you don't find any answer, asking about "
    "payment or collaboration."
)
DEFAULT_CHATBOT_NEVER_ANSWER = (
    "Never answer about payment, outside scope and all."
)


def build_default_chatbot_welcome_message(chatbot_name):
    return DEFAULT_CHATBOT_WELCOME_MESSAGE_TEMPLATE.format(
        chatbot_name=chatbot_name,
    )


class ChatbotQuerySet(models.QuerySet):
    def active(self):
        return self.filter(is_deleted=False)

    def accessible_to(self, user):
        """Chatbots the user may see and chat with: their own plus the
        admin-created demo chatbots."""
        return self.active().filter(
            models.Q(created_by=user) | models.Q(created_by__is_staff=True),
        )

    def with_knowledge_counts(self):
        """Annotate per-chatbot knowledge counts for list endpoints."""
        return self.annotate(
            product_count=models.Count("products", distinct=True),
            service_count=models.Count("services", distinct=True),
            knowledge_source_count=models.Count(
                "knowledge_bases", distinct=True,
            ),
        )


class Chatbot(BaseModel):
    chatbot_name = models.CharField(max_length=120)
    business_type = models.CharField(max_length=120)
    description = models.TextField(blank=True)
    slug = models.SlugField(
        max_length=140,
        unique=True,
        blank=True,
        editable=False,
    )
    logo = models.URLField(blank=True)
    tone = models.CharField(
        max_length=30,
        choices=ChatbotToneTypes.choices,
        default=ChatbotToneTypes.PROFESSIONAL,
    )

    # conversation
    welcome_message = models.TextField(
        blank=True,
        default=DEFAULT_CHATBOT_WELCOME_MESSAGE_TEMPLATE,
    )
    fallback_message = models.TextField(
        blank=True,
        default=DEFAULT_CHATBOT_FALLBACK_MESSAGE,
    )
    instructions = models.TextField(blank=True)
    escalation_rule = models.TextField(
        blank=True,
        default=DEFAULT_CHATBOT_ESCALATION_RULE,
    )
    never_answer = models.TextField(
        blank=True,
        default=DEFAULT_CHATBOT_NEVER_ANSWER,
    )

    language = models.CharField(max_length=20, default="en")
    timezone = models.CharField(
        max_length=64,
        default="UTC",
        validators=[validate_timezone_name],
        help_text="IANA timezone, for example Asia/Dhaka.",
    )

    # features: which knowledge sources the agent may use. A source is
    # only used when it is enabled here AND has trained content.
    knowledge_enabled = models.BooleanField(
        default=True,
        help_text="Answer from documents, web pages, and raw text.",
    )
    products_enabled = models.BooleanField(
        default=True,
        help_text="Search and recommend products from the product catalog.",
    )
    services_enabled = models.BooleanField(
        default=True,
        help_text="Search and recommend services from the service list.",
    )
    product_ordering_enabled = models.BooleanField(
        default=False,
        help_text=(
            "Let the agent add products to a cart and take orders. "
            "Requires products_enabled."
        ),
    )

    # voice (audio mode). A selected custom voice wins over voice_name;
    # with neither, the platform default voice is used.
    voice_name = models.CharField(
        max_length=40,
        blank=True,
        default="",
        help_text="Prebuilt Live voice, for example Kore or Puck.",
    )
    custom_voice = models.ForeignKey(
        "chatbot.ChatbotVoice",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
        help_text="The selected custom (replicated) voice, if any.",
    )
    speaking_pace = models.CharField(
        max_length=10,
        choices=SpeakingPace.choices,
        default=SpeakingPace.NORMAL,
    )

    # other settings
    other_settings = models.JSONField(
        default=dict,
        blank=True,
        validators=[validate_other_settings],
        help_text="Miscellaneous settings to operate chatbot",
    )

    # lifecycle
    status = models.CharField(
        max_length=30,
        choices=ChatbotStatusTypes.choices,
        default=ChatbotStatusTypes.ACTIVE,
        db_index=True,
    )

    is_deleted = models.BooleanField(default=False, db_index=True)

    objects = ChatbotQuerySet.as_manager()

    class Meta:
        ordering = ["chatbot_name"]
        indexes = [
            models.Index(
                fields=["status", "is_deleted"],
                name="chatbot_status_active_idx",
            ),
        ]

    def __str__(self):
        return self.chatbot_name

    @property
    def ordering_enabled(self):
        """Cart and order taking is a products add-on."""
        return self.products_enabled and self.product_ordering_enabled

    def generate_unique_slug(self):
        base_slug = (
            slugify(self.chatbot_name)[:120].strip("-") or "chatbot"
        )

        queryset = type(self).objects.all()

        if self.pk:
            queryset = queryset.exclude(pk=self.pk)

        candidate = base_slug
        suffix = 2

        while queryset.filter(slug=candidate).exists():
            suffix_text = f"-{suffix}"
            candidate = f"{base_slug[:140 - len(suffix_text)]}{suffix_text}"
            suffix += 1

        return candidate

    def save(self, *args, **kwargs):
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            if not update_fields:
                return
            kwargs["update_fields"] = update_fields

        previous = None
        if not self._state.adding and (
            update_fields is None
            or "chatbot_name" in update_fields
        ):
            previous = type(self).objects.filter(pk=self.pk).values(
                "chatbot_name", "welcome_message",
            ).first()

        if not self.slug:
            self.slug = self.generate_unique_slug()
            if update_fields is not None:
                update_fields.add("slug")

        # Keep the welcome message in sync with the chatbot name while it
        # is still the generated default; once customized it is left alone.
        refresh_welcome = self.welcome_message == (
            DEFAULT_CHATBOT_WELCOME_MESSAGE_TEMPLATE
        )
        if previous is not None and self.chatbot_name != previous["chatbot_name"]:
            refresh_welcome = refresh_welcome or (
                self.welcome_message == previous["welcome_message"]
                and previous["welcome_message"] == (
                    build_default_chatbot_welcome_message(
                        previous["chatbot_name"],
                    )
                )
            )
        if refresh_welcome:
            self.welcome_message = build_default_chatbot_welcome_message(
                self.chatbot_name,
            )
            if update_fields is not None:
                update_fields.add("welcome_message")

        super().save(*args, **kwargs)

    @property
    def is_active(self):
        return (
            not self.is_deleted
            and self.status
            not in {
                ChatbotStatusTypes.DISABLED,
                ChatbotStatusTypes.DISABLED_BY_ADMIN,
            }
        )


class ChatbotVoice(BaseModel):
    """A custom voice replicated from its owner's recordings.

    Both recordings are normalized to 24 kHz mono 16-bit WAV and stored
    privately in R2. Voice sessions send the sample (clipped to the Live
    limit) and the consent recording to Gemini Live, which builds the
    voice per session.
    """

    chatbot = models.ForeignKey(
        Chatbot,
        on_delete=models.CASCADE,
        related_name="custom_voices",
    )
    name = models.CharField(max_length=60)
    gender = models.CharField(max_length=10, choices=VoiceGender.choices)
    sample_audio_key = models.CharField(max_length=1024)
    sample_duration_seconds = models.FloatField()
    consent_audio_key = models.CharField(max_length=1024)
    consent_duration_seconds = models.FloatField()
    consent_statement = models.TextField(
        help_text="The consent statement the voice owner read aloud.",
    )

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(
                fields=["chatbot", "name"],
                name="unique_custom_voice_name_per_chatbot",
            ),
        ]

    def __str__(self):
        return f"{self.name} ({self.chatbot})"


class VoicePreview(models.Model):
    """A cached listening sample of one prebuilt voice (public R2 file)."""

    voice_name = models.CharField(max_length=40, unique=True)
    audio_url = models.URLField(max_length=2048)
    audio_key = models.CharField(max_length=1024)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.voice_name
