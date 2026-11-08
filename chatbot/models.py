from django.db import models
from django.utils.text import slugify

from app.core.models import BaseModel
from app.utils.validators import validate_timezone_name
from chatbot.utils.choices import ChatbotStatusTypes, ChatbotToneTypes
from chatbot.utils.validation import validate_other_settings


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
        default=ChatbotStatusTypes.DRAFT,
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
