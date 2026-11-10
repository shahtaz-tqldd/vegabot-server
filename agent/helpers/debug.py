"""Agent turn diagnostics, logged server-side and sent to the client.

Each diagnostic is one ``agent.debug`` socket event:

    {"type": "agent.debug",
     "data": {"turn_id": "...", "mode": "audio", "stage": "tool_result",
              "tool": "show_recommendations", ...}}

Stages: ``tool_call``, ``tool_result``, ``recommendations_pending``,
``recommendations_sent``, ``recommendations_dropped``. Client delivery is
gated by ``settings.AGENT_CLIENT_DEBUG`` (defaults to ``DEBUG``); the
server log line is always written.
"""

from django.conf import settings

from agent.helpers.stream import emit_event
from agent.tools import RECOMMENDATION_TOOL
from app.utils.logger import logger
from base.socket.events import AGENT_DEBUG

MAX_ARG_CHARS = 300


def client_debug_enabled():
    return bool(getattr(settings, "AGENT_CLIENT_DEBUG", False))


async def emit_debug(emit, stage, *, mode, turn_id=None, **data):
    payload = {"turn_id": turn_id, "mode": mode, "stage": stage, **data}
    logger.info("agent.debug %s", payload)
    if client_debug_enabled():
        await emit_event(emit, {"type": AGENT_DEBUG, "data": payload})


def card_summary(items):
    return [
        {"id": item.get("id"), "name": item.get("name"), "type": item.get("type")}
        for item in items or []
    ]


def _result_summary(name, payload):
    payload = payload or {}
    summary = {"status": payload.get("status")}
    if "sources" in payload:
        summary["results"] = [
            {"ref": source.get("ref"), "name": source.get("name"),
             "space": source.get("space")}
            for source in payload.get("sources") or []
        ]
    if name == RECOMMENDATION_TOOL:
        summary["items"] = card_summary(payload.get("items"))
    if payload.get("message"):
        summary["message"] = payload["message"]
    return summary


def tool_diagnostics(event):
    """(stage, data) pairs for the tool calls/results in one ADK event."""
    diagnostics = []
    for call in event.get_function_calls():
        diagnostics.append((
            "tool_call",
            {"tool": call.name, "args": str(call.args or {})[:MAX_ARG_CHARS]},
        ))
    for response in event.get_function_responses():
        diagnostics.append((
            "tool_result",
            {"tool": response.name,
             **_result_summary(response.name, response.response)},
        ))
    return diagnostics
