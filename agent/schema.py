from pydantic import BaseModel, Field


class TokenUsageSchema(BaseModel):
    input_tokens: int = 0
    output_tokens: int = 0  # Includes billable thinking tokens.
    thinking_tokens: int = 0
    cached_input_tokens: int = 0
    total_tokens: int = 0
    # Per-modality breakdown reported by the model (the Live model bills
    # each modality at its own rate). Text = the rest of the tokens.
    audio_input_tokens: int = 0
    media_input_tokens: int = 0  # image + video
    audio_output_tokens: int = 0
    # Audio actually streamed, measured by the voice session.
    audio_input_seconds: float = 0.0
    audio_output_seconds: float = 0.0


class EscalationSchema(BaseModel):
    status: str = "recorded"
    escalation_reason: str = Field(min_length=1, max_length=500)


class AgentResultSchema(BaseModel):
    """What the collector extracted from one single-agent turn."""

    content: str = Field(min_length=1)
    source_ids: list[str] = Field(default_factory=list)
    recommendations: list[dict] = Field(default_factory=list)
    escalation: EscalationSchema | None = None


class AgentResponseSchema(BaseModel):
    result: AgentResultSchema
    token: TokenUsageSchema = Field(default_factory=TokenUsageSchema)
    cost: float = Field(default=0.0, ge=0)  # Estimated USD.
