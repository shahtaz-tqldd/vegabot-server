from django.conf import settings
from google.adk.agents import LlmAgent

from agent.helpers.instruction import build_instruction
from agent.helpers.model import chat_model, generation_config
from agent.tools import build_tools


def build_root_agent(chatbot, session, *, profile, business_policy=""):
    """The chat agent for one conversation.

    Tools and prompt sections are conditional on the knowledge spaces the
    chatbot actually has (documents/products/services).
    """
    spaces = profile.get("spaces") or {}
    return LlmAgent(
        name="chat_agent",
        mode="chat",
        model=chat_model(),
        description="Customer-facing assistant for this business.",
        instruction=build_instruction(
            profile,
            business_policy=business_policy,
        ),
        tools=build_tools(
            chatbot.id,
            session,
            spaces=[space for space, enabled in spaces.items() if enabled],
        ),
        generate_content_config=generation_config(
            settings.GEMINI_CHAT_MAX_OUTPUT_TOKENS,
        ),
    )
