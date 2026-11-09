from google.adk.agents import LlmAgent

from agent.helpers.instruction import build_instruction
from agent.helpers.model import generation_config, live_model
from agent.tools import build_tools


def build_live_root_agent(
    chatbot, session, *, profile, business_policy="", model_config=None,
):
    """The live (voice) agent for one conversation.

    Shares the chat agent's conditional tools and prompt sections; only
    the model (and its voice configuration) differ.
    """
    spaces = profile.get("spaces") or {}
    return LlmAgent(
        name="live_agent",
        mode="chat",
        model=live_model(model_config=model_config),
        description="Customer-facing live voice assistant for this business.",
        instruction=build_instruction(
            profile,
            business_policy=business_policy,
        )
        + "\n\nVoice: your replies are spoken aloud. Keep them short and "
        "conversational — a few sentences at most, no markdown, no lists, "
        "no citation markers spoken aloud.",
        tools=build_tools(
            chatbot.id,
            session,
            spaces=[space for space, enabled in spaces.items() if enabled],
        ),
        generate_content_config=generation_config(),
    )
