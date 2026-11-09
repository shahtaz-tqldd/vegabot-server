"""Collect what we need from one single-agent turn's event stream.

    collector = TurnCollector(root_name=agent.name)
    async for event in events:
        collector.observe(event)
    result, source = collector.build_result(session_state=..., fallback_message=...)
"""

from agent.helpers.citations import extract_citations
from agent.helpers.recommendations import MAX_RECOMMENDATIONS
from agent.helpers.usage import add_event_usage
from agent.schema import (
    AgentResultSchema,
    EscalationSchema,
    TokenUsageSchema,
)
from agent.tools import (
    ESCALATION_TOOL,
    KNOWLEDGE_REFS_KEY,
    KNOWLEDGE_RESULTS_KEY,
    RECOMMENDATION_TOOL,
)
from app.utils.logger import logger


class TurnCollector:
    def __init__(self, *, root_name):
        self.root_name = root_name
        self.raw_reply = ""
        self.usage = TokenUsageSchema()
        self.selected_tools = []
        self.refs = {}
        self.results = {}
        self.escalation = None
        self.recommendations = []
        self._seen_usage_events = set()

    def observe(self, event):
        if event.error_code:
            raise RuntimeError(f"Agent execution failed: {event.error_code}")
        self._track_tool_calls(event)
        self._track_usage(event)
        self._track_state(event)
        self._track_tool_responses(event)
        self._track_reply(event)

    # ---------------------------------------------------------------- tracking
    def _track_tool_calls(self, event):
        for call in event.get_function_calls():
            if call.name not in self.selected_tools:
                self.selected_tools.append(call.name)

    def _track_usage(self, event):
        if (
            event.usage_metadata
            and not event.partial
            and event.id not in self._seen_usage_events
        ):
            self._seen_usage_events.add(event.id)
            add_event_usage(self.usage, event.usage_metadata)

    def _track_state(self, event):
        delta = getattr(event.actions, "state_delta", None) or {}
        if refs := delta.get(KNOWLEDGE_REFS_KEY):
            self.refs = dict(refs)  # tool writes the full merged map
        if results := delta.get(KNOWLEDGE_RESULTS_KEY):
            self.results = dict(results)

    def _track_tool_responses(self, event):
        for response in event.get_function_responses():
            payload = response.response or {}
            if response.name == RECOMMENDATION_TOOL:
                self._add_recommendations(payload.get("items") or [])
            if response.name == ESCALATION_TOOL:
                try:
                    self.escalation = EscalationSchema.model_validate(payload)
                except Exception:
                    self.escalation = EscalationSchema(
                        escalation_reason=str(payload)[:500] or "escalated",
                    )

    def _add_recommendations(self, items):
        known = {item.get("id") for item in self.recommendations}
        for item in items:
            if len(self.recommendations) >= MAX_RECOMMENDATIONS:
                break
            if item.get("id") not in known:
                known.add(item.get("id"))
                self.recommendations.append(item)

    def _track_reply(self, event):
        if (
            event.author == self.root_name
            and event.is_final_response()
            and event.content
            and not event.partial
        ):
            text = "".join(
                p.text for p in event.content.parts or [] if p.text and not p.thought
            )
            if text.strip():
                self.raw_reply = text.strip()

    # ------------------------------------------------------------------ result
    def build_result(self, *, session_state, fallback_message):
        # session_state is the pre-run snapshot (earlier turns); self.refs
        # holds anything the tool added this turn.
        refs = {**(session_state.get(KNOWLEDGE_REFS_KEY) or {}), **self.refs}
        self.results = {
            **(session_state.get(KNOWLEDGE_RESULTS_KEY) or {}),
            **self.results,
        }
        content, source_ids = extract_citations(self.raw_reply, refs)

        source = "root_agent" if content else "configured_fallback"
        result = AgentResultSchema(
            content=content or fallback_message,
            source_ids=source_ids,
            # The items the agent chose with show_recommendations.
            recommendations=list(self.recommendations) if content else [],
            escalation=self.escalation,
        )
        return result, source

    # ----------------------------------------------------------------- logging
    def log_summary(self, *, chatbot_id, session_id, source, fallback_message):
        logger.info(
            "Agent turn completed chatbot_id=%s session_id=%s "
            "selected_tools=%s response_source=%s "
            "reply_matches_fallback=%s total_tokens=%s",
            chatbot_id,
            session_id,
            self.selected_tools,
            source,
            (
                self.raw_reply.strip() == fallback_message.strip()
                if self.raw_reply
                else True
            ),
            self.usage.total_tokens,
        )
