from django.apps import AppConfig


class ChatConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "chat"
    # Keep the original Django app label after renaming the Python package.
    # The label is part of the migration history and implicit database table
    # names, so changing it would make existing chat_session migrations and
    # tables look like a different app to Django.
    label = "chat_session"

    def ready(self):
        from chat import signals  # noqa: F401
