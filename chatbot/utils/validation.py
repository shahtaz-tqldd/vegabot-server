from django.core.exceptions import ValidationError


def validate_other_settings(value):
    if not isinstance(value, dict):
        raise ValidationError("Other settings must be a JSON object.")
