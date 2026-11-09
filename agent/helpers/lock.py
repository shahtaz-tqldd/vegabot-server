"""One agent run per chat session at a time.

Text turns and the voice session share one ADK session; ADK rejects
writes from a stale session object, so a text turn running while voice
is live would crash the voice run. The lock lives in the shared cache
and records its holder (``voice:...``, ``text:...``, ``task:...``).
"""

from django.core.cache import cache

TEXT_TURN_TTL_SECONDS = 180
VOICE_TTL_SECONDS = 90  # refreshed by the voice session's heartbeat


class AgentBusyError(Exception):
    """Another text turn or voice session is running on this session."""


def _key(session_id):
    return f"agent:session-busy:{session_id}"


def acquire_session_lock(session_id, owner, ttl):
    return cache.add(_key(session_id), owner, ttl)


def refresh_session_lock(session_id, owner, ttl):
    if cache.get(_key(session_id)) == owner:
        cache.set(_key(session_id), owner, ttl)


def release_session_lock(session_id, owner):
    if cache.get(_key(session_id)) == owner:
        cache.delete(_key(session_id))


def session_lock_holder(session_id):
    return cache.get(_key(session_id))


def voice_active(session_id):
    return str(session_lock_holder(session_id) or "").startswith("voice:")


async def aacquire_session_lock(session_id, owner, ttl):
    return await cache.aadd(_key(session_id), owner, ttl)


async def arefresh_session_lock(session_id, owner, ttl):
    if await cache.aget(_key(session_id)) == owner:
        await cache.aset(_key(session_id), owner, ttl)


async def arelease_session_lock(session_id, owner):
    if await cache.aget(_key(session_id)) == owner:
        await cache.adelete(_key(session_id))
