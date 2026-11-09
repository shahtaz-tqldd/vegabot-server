from django.conf import settings
from google.adk.agents import LlmAgent

from agent.helpers.instruction import build_instruction
from agent.helpers.model import build_model, generation_config
from agent.tools import build_tools
from chat.utils.choices import ChatMode

# One name in both modes: ADK replays history authored by this agent as
# the agent's own turns, so text and voice read as one conversation.
AGENT_NAME = "assistant"


def build_root_agent(chatbot, session, *, profile, mode=ChatMode.TEXT, model_config=None):
    """The customer-facing agent for one conversation, in one chat mode.

    Policy, knowledge tools, and escalation are identical in both modes;
    the mode selects the model (cheap chat model for text, live model with
    voice for audio), the reply format, and the recommendation mechanism.
    Tools and prompt sections are conditional on the knowledge spaces the
    chatbot actually has (documents/products/services).
    """
    spaces = profile.get("spaces") or {}
    return LlmAgent(
        name=AGENT_NAME,
        mode="chat",
        model=build_model(mode, model_config=model_config),
        description="Customer-facing assistant for this business.",
        instruction=build_instruction(
            profile,
            business_policy=profile.get("policy", ""),
            mode=mode,
        ),
        tools=build_tools(
            chatbot.id,
            session,
            spaces=[space for space, enabled in spaces.items() if enabled],
            mode=mode,
        ),
        generate_content_config=generation_config(
            settings.GEMINI_CHAT_MAX_OUTPUT_TOKENS,
        ),
    )
