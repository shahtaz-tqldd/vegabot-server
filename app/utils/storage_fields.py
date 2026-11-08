from rest_framework import serializers


class R2ImageField(serializers.ImageField):
    """Accept an uploaded image while representing the stored R2 URL."""

    def to_representation(self, value):
        if isinstance(value, str):
            return value
        return super().to_representation(value)
