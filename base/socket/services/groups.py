"""Stable Channels group names shared by all realtime publishers."""


def global_notification_group():
    """Platform-wide notifications for every connected user."""
    return "notifications.global"


def user_notification_group(user_id):
    """Notifications addressed to one user account."""
    return f"notifications.user.{user_id}"


def chatbot_dashboard_group(chatbot_id):
    """Notifications and realtime events for one chatbot's audience."""
    return f"notifications.chatbot.{chatbot_id}"


def chat_session_group(session_id):
    """Realtime chat message delivery for one chat session."""
    return f"chat_session_{session_id}"
