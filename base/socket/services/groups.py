"""Stable Channels group names shared by all realtime publishers."""


def chatbot_dashboard_group(chatbot_id):
    """Notifications and realtime events for one chatbot's audience."""
    return f"notifications.chatbot.{chatbot_id}"


def chat_session_group(session_id):
    """Realtime chat message delivery for one chat session."""
    return f"chat_session_{session_id}"
