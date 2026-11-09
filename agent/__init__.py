"""The customer-facing agent.

One agent (``agent.root_agent``), two configurations selected by chat
mode: ``text`` runs the chat model (``agent.client.AgentClient``) and
``audio`` runs the live model (``agent.voice.VoiceSession``). Both share
one ADK session per chat session, so users switch freely between typing
and talking.
"""
