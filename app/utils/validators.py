from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from django.core.exceptions import ValidationError


def validate_timezone_name(value):
    try:
        ZoneInfo(value)
    except (ZoneInfoNotFoundError, ValueError, TypeError) as exc:
        raise ValidationError("Enter a valid IANA timezone, such as Asia/Dhaka.") from exc


def validate_json_object(value):
    if not isinstance(value, dict):
        raise ValidationError("This value must be a JSON object.")
