import json

from django.core.management.base import BaseCommand, CommandError

from chatbot.models import Chatbot
from notification.services import create_chatbot_notification


class Command(BaseCommand):
    help = "Create and emit a demo training notification for a chatbot."

    def add_arguments(self, parser):
        parser.add_argument(
            "--chatbot-id",
            required=True,
            help="Chatbot UUID that receives the notification.",
        )
        parser.add_argument("--title", default="Knowledge training complete")
        parser.add_argument(
            "--message",
            default="Demo training run finished successfully.",
        )
        parser.add_argument(
            "--metadata",
            default="{}",
            help='JSON object metadata. Example: --metadata \'{"source":"demo"}\'',
        )

    def handle(self, *args, **options):
        try:
            metadata = json.loads(options["metadata"])
        except json.JSONDecodeError as exc:
            raise CommandError(f"--metadata must be valid JSON: {exc}") from exc
        if not isinstance(metadata, dict):
            raise CommandError("--metadata must be a JSON object.")

        chatbot = Chatbot.objects.filter(pk=options["chatbot_id"]).first()
        if chatbot is None:
            raise CommandError(f"Chatbot not found: {options['chatbot_id']}")

        notification = create_chatbot_notification(
            chatbot=chatbot,
            title=options["title"],
            message=options["message"],
            metadata=metadata,
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"Created chatbot notification {notification.id}."
            )
        )
