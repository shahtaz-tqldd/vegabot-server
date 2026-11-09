"""The client-facing event stream of one agent reply.

Text and voice replies emit the same sequence, so the frontend renders
both with one code path:

    agent.response.started
    agent.response.delta*            reply text, chunk by chunk
    agent.response.recommendations   product/service cards (when any)
    agent.response.completed         the persisted message
    agent.response.failed            instead of completed, on errors

Emission is best-effort: a broken socket never fails the turn.
"""

import inspect
from uuid import uuid4

from agent.helpers.citations import CitationStreamFilter
from app.utils.logger import logger
from base.socket.events import (
    AGENT_RESPONSE_COMPLETED,
    AGENT_RESPONSE_DELTA,
    AGENT_RESPONSE_FAILED,
    AGENT_RESPONSE_RECOMMENDATIONS,
    AGENT_RESPONSE_STARTED,
)


async def emit_event(emit, payload):
    """Call a sync or async ``emit(payload)``; never raises."""
    if emit is None:
        return
    try:
        outcome = emit(payload)
        if inspect.isawaitable(outcome):
            await outcome
    except Exception:
        logger.exception("Agent stream event %s not delivered", payload.get("type"))


class TurnStream:
    def __init__(self, emit, *, mode, turn_id=None, reply_to=None, strip_citations=False):
        self.emit = emit
        self.mode = mode
        self.turn_id = str(turn_id or uuid4())
        self.reply_to = str(reply_to) if reply_to else None
        self.sequence = 0
        self._filter = CitationStreamFilter() if strip_citations else None

    async def _send(self, event_type, **data):
        await emit_event(
            self.emit,
            {"type": event_type, "data": {"turn_id": self.turn_id, **data}},
        )

    async def start(self):
        await self._send(
            AGENT_RESPONSE_STARTED, mode=self.mode, reply_to=self.reply_to,
        )

    async def delta(self, chunk):
        if self._filter is not None:
            chunk = self._filter.feed(chunk)
        await self._send_delta(chunk)

    async def _send_delta(self, chunk):
        if not chunk:
            return
        self.sequence += 1
        await self._send(AGENT_RESPONSE_DELTA, delta=chunk, sequence=self.sequence)

    async def finish(self, *, message, sources=(), recommendations=(), interrupted=False):
        """Text is complete: send the cards, then the persisted message."""
        if self._filter is not None:
            await self._send_delta(self._filter.flush())
        if recommendations:
            await self._send(
                AGENT_RESPONSE_RECOMMENDATIONS, items=list(recommendations),
            )
        await self._send(
            AGENT_RESPONSE_COMPLETED,
            mode=self.mode,
            message=message,
            sources=list(sources or ()),
            interrupted=interrupted,
        )

    async def fail(self, message):
        await self._send(AGENT_RESPONSE_FAILED, message=message)
