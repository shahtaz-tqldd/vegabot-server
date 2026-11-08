"""ADK runtime wiring kept out of the client."""

from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from google.adk.agents.context_cache_config import ContextCacheConfig
from google.adk.apps.app import App, EventsCompactionConfig
from google.adk.plugins.global_instruction_plugin import GlobalInstructionPlugin
from google.adk.sessions import DatabaseSessionService


def build_session_service():
    if not settings.ADK_DB_URL:
        raise ImproperlyConfigured("Set ADK_DB_URL for persistent agent sessions.")
    return DatabaseSessionService(db_url=settings.ADK_DB_URL)


def build_app(
    *,
    name,
    root_agent,
    compaction_interval,
    compaction_overlap,
    cache_min_tokens,
    cache_ttl_seconds,
    cache_intervals,
    global_instruction_text=None,
):
    """Build the ADK App.

    Pass ``global_instruction_text=None`` once the policy lives inside the
    single root agent's own instruction; the plugin is then not installed.
    """
    plugins = []
    if global_instruction_text:
        plugins.append(
            GlobalInstructionPlugin(
                global_instruction=lambda _ctx: global_instruction_text
            )
        )

    return App(
        name=name,
        root_agent=root_agent,
        events_compaction_config=EventsCompactionConfig(
            compaction_interval=compaction_interval,
            overlap_size=compaction_overlap,
        ),
        context_cache_config=ContextCacheConfig(
            min_tokens=cache_min_tokens,
            ttl_seconds=cache_ttl_seconds,
            cache_intervals=cache_intervals,
        ),
        plugins=plugins,
    )


async def get_or_create_session(service, *, app_name, user_id, session_id):
    kwargs = dict(app_name=app_name, user_id=user_id, session_id=str(session_id))
    session = await service.get_session(**kwargs)
    if session is None:
        session = await service.create_session(**kwargs)
    return session