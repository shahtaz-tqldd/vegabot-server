from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand

from app.settings.env import env


class Command(BaseCommand):
    help = "Create the initial superuser from environment variables if one does not already exist."

    def handle(self, *args, **options):
        user_model = get_user_model()

        if user_model.objects.filter(is_superuser=True).exists():
            self.stdout.write(self.style.WARNING("Superuser already exists. Skipping creation."))
            return

        email = env("SUPERUSER_EMAIL", "").strip()
        password = env("SUPERUSER_PASSWORD", "").strip()
        full_name = env("SUPERUSER_NAME", "").strip()

        if not email or not password:
            self.stdout.write(
                self.style.WARNING(
                    "SUPERUSER_EMAIL or SUPERUSER_PASSWORD is missing. Skipping superuser creation."
                )
            )
            return

        create_kwargs = {
            "email": email,
            "password": password,
        }
        field_names = {field.name for field in user_model._meta.get_fields()}

        if "name" in field_names:
            create_kwargs["name"] = full_name

        user_model.objects.create_superuser(**create_kwargs)
        self.stdout.write(self.style.SUCCESS(f"Superuser created for {email}."))
