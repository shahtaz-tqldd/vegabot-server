from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError


class Command(BaseCommand):
    help = "Enable user"

    # python manage.py enable_user --email shahtaz67@gmail.com

    def add_arguments(self, parser):
        parser.add_argument(
            "--email",
            required=True,
            help="Email address of the user to enable.",
        )

    def handle(self, *args, **options):
        user_model = get_user_model()
        email = options["email"].strip()

        if not email:
            raise CommandError("Email cannot be empty.")

        try:
            user = user_model.objects.get(email__iexact=email)
        except user_model.DoesNotExist as exc:
            raise CommandError(f"User with email '{email}' does not exist.") from exc

        user.email = user_model.objects.normalize_email(user.email).strip().casefold()
        user.save(update_fields=["email", "updated_at"])
        user.reactivate()
        self.stdout.write(self.style.SUCCESS(f"User enabled for {user.email}."))
