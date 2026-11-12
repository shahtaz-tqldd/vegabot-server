"""Realtime event names sent over the socket to the frontend."""

NOTIFICATION_CREATED = "notification.created"
CHAT_MESSAGE_CREATED = "chat.message.created"
SESSION_UPDATED = "chat.session.updated"

# Session activity recorded by the agent's tools, pushed to the session
# socket for frontend notifications. Each carries the session's updated
# activity counts.
CART_ITEM_ADDED = "chat.cart.item_added"
CART_ITEM_REMOVED = "chat.cart.item_removed"
ORDER_PLACED = "chat.order.placed"
ESCALATION_CREATED = "chat.escalation.created"

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

# Diagnostics of one agent turn (tool calls, results, recommendation
# decisions); only sent when settings.AGENT_CLIENT_DEBUG is on.
AGENT_DEBUG = "agent.debug"
