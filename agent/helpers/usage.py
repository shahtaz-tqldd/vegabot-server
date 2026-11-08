"""Token accounting and the internal response envelope."""

from django.conf import settings

from agent.schema import AgentResponseSchema


def add_event_usage(usage, metadata):
    """Accumulate one ADK event's usage_metadata into a TokenUsageSchema."""
    prompt = metadata.prompt_token_count or 0
    output = metadata.candidates_token_count or 0
    thinking = metadata.thoughts_token_count or 0

    usage.input_tokens += prompt
    usage.output_tokens += output + thinking
    usage.thinking_tokens += thinking
    usage.cached_input_tokens += metadata.cached_content_token_count or 0
    usage.total_tokens += metadata.total_token_count or (prompt + output + thinking)


def compute_cost(usage):
    return (
        usage.input_tokens * settings.GEMINI_INPUT_COST_PER_MILLION
        + usage.output_tokens * settings.GEMINI_OUTPUT_COST_PER_MILLION
    ) / 1_000_000


def build_response(result, usage):
    """Internal envelope: result + token usage + cost."""
    return AgentResponseSchema(
        result=result,
        token=usage,
        cost=round(compute_cost(usage), 10),
    ).model_dump(mode="json")