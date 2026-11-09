"""Realtime event names sent over the socket to the frontend."""

NOTIFICATION_CREATED = "notification.created"
CHAT_MESSAGE_CREATED = "chat.message.created"
SESSION_UPDATED = "chat.session.updated"

# Chat mode (text <-> audio) on the session socket.
MODE_CHANGED = "mode.changed"

# Speech-to-text of the user's microphone (audio mode).
VOICE_TRANSCRIPT = "voice.transcript"

# One agent reply, identical in text and audio mode.
AGENT_RESPONSE_STARTED = "agent.response.started"
AGENT_RESPONSE_DELTA = "agent.response.delta"
AGENT_RESPONSE_RECOMMENDATIONS = "agent.response.recommendations"
AGENT_RESPONSE_COMPLETED = "agent.response.completed"
AGENT_RESPONSE_FAILED = "agent.response.failed"
