from rest_framework import serializers

from notification.models import Notification


class NotificationSerializer(serializers.ModelSerializer):
    chatbot_id = serializers.UUIDField(read_only=True)

    class Meta:
        model = Notification
        fields = [
            "id",
            "chatbot_id",
            "notification_type",
            "title",
            "message",
            "metadata",
            "created_at",
        ]
        read_only_fields = fields
