"""Token accounting and the internal response envelope."""

from django.conf import settings

from agent.schema import AgentResponseSchema
from chat.utils.choices import ChatMode


def _modality_tokens(details, *modalities):
    wanted = {modality.upper() for modality in modalities}
    total = 0
    for detail in details or ():
        modality = getattr(detail.modality, "value", detail.modality)
        if str(modality or "").upper() in wanted:
            total += detail.token_count or 0
    return total


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

    usage.audio_input_tokens += _modality_tokens(
        metadata.prompt_tokens_details, "AUDIO",
    )
    usage.media_input_tokens += _modality_tokens(
        metadata.prompt_tokens_details, "IMAGE", "VIDEO",
    )
    usage.audio_output_tokens += _modality_tokens(
        metadata.candidates_tokens_details, "AUDIO",
    )


def _per_million(tokens, rate):
    return tokens * rate / 1_000_000


def live_cost(usage):
    """Estimated USD of a Live (voice) turn.

    Live bills text, audio, and image/video at separate rates. With the
    model's per-modality token breakdown, each modality is priced by its
    tokens; without one, audio is priced by the measured duration (per
    minute) and the remaining tokens as text.
    """
    has_breakdown = bool(usage.audio_input_tokens or usage.audio_output_tokens)
    if has_breakdown:
        audio_in = _per_million(
            usage.audio_input_tokens,
            settings.GEMINI_LIVE_AUDIO_INPUT_COST_PER_MILLION,
        )
        audio_out = _per_million(
            usage.audio_output_tokens,
            settings.GEMINI_LIVE_AUDIO_OUTPUT_COST_PER_MILLION,
        )
        text_in_tokens = max(
            0,
            usage.input_tokens
            - usage.audio_input_tokens
            - usage.media_input_tokens,
        )
        text_out_tokens = max(0, usage.output_tokens - usage.audio_output_tokens)
    else:
        audio_in = (
            usage.audio_input_seconds / 60
            * settings.GEMINI_LIVE_AUDIO_INPUT_COST_PER_MINUTE
        )
        audio_out = (
            usage.audio_output_seconds / 60
            * settings.GEMINI_LIVE_AUDIO_OUTPUT_COST_PER_MINUTE
        )
        # Without a breakdown the audio is inside the token counts; only
        # tokens beyond the audio's duration are priced as text.
        text_in_tokens = 0 if usage.audio_input_seconds else usage.input_tokens
        text_out_tokens = 0 if usage.audio_output_seconds else usage.output_tokens

    return (
        audio_in
        + audio_out
        + _per_million(
            usage.media_input_tokens,
            settings.GEMINI_LIVE_MEDIA_INPUT_COST_PER_MILLION,
        )
        + _per_million(
            text_in_tokens, settings.GEMINI_LIVE_TEXT_INPUT_COST_PER_MILLION,
        )
        + _per_million(
            text_out_tokens, settings.GEMINI_LIVE_TEXT_OUTPUT_COST_PER_MILLION,
        )
    )


def compute_cost(usage, mode=ChatMode.TEXT):
    """Estimated USD; voice turns are priced at the live model's rates."""
    if mode == ChatMode.AUDIO:
        return live_cost(usage)
    return (
        usage.input_tokens * settings.GEMINI_INPUT_COST_PER_MILLION
        + usage.output_tokens * settings.GEMINI_OUTPUT_COST_PER_MILLION
    ) / 1_000_000


def build_response(result, usage, mode=ChatMode.TEXT):
    """Internal envelope: result + token usage + cost."""
    return AgentResponseSchema(
        result=result,
        token=usage,
        cost=round(compute_cost(usage, mode), 10),
    ).model_dump(mode="json")
