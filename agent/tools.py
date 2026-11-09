"""Tools of the agent, shared by text and audio mode.

Each knowledge space (documents, products, services) gets its own search
tool; tools are registered conditionally per chatbot so a chatbot without
products never sees (or pays for) the product-search tool or its prompt
section. ``show_recommendations`` exists in both modes for chatbots with
products or services: the agent searches a wide candidate set, picks the
best few, and names them with the tool; cards are sent after the reply.
Escalation is always available.
"""

from asgiref.sync import sync_to_async

from google.adk.tools import FunctionTool, ToolContext

from agent.helpers.recommendations import (
    MAX_RECOMMENDATIONS,
    recommendations_for_refs,
)
from app.utils.logger import logger
from chat.services.escalations import record_escalation
from chat.utils.choices import ChatMode
from knowledge.utils.choices import KnowledgeSpaceTypes
from vector_store.services.vectorize import KnowledgeVectorService

KNOWLEDGE_REFS_KEY = "knowledge_refs"
KNOWLEDGE_RESULTS_KEY = "knowledge_results"
KNOWLEDGE_RESULT_LIMIT = 5
# Catalog searches return a wider candidate set to choose recommendations from.
CATALOG_RESULT_LIMIT = 10
MAX_ESCALATION_REASON_LENGTH = 500

ESCALATION_TOOL = "request_human_escalation"
RECOMMENDATION_TOOL = "show_recommendations"
MAX_DESCRIPTION_CHARS = 500


def _clean_text(value, *, field_name, max_length):
    text = " ".join(str(value).split())
    if not text:
        raise ValueError(f"{field_name} must not be empty.")
    if len(text) > max_length:
        raise ValueError(f"{field_name} exceeds {max_length} characters.")
    return text


def _result_metadata(result):
    """Display metadata for one search result, from the vector store."""
    metadata = dict(result.metadata or {})
    return {
        "space": result.space,
        "name": metadata.get("name", ""),
        "url": metadata.get("product_url", "") or metadata.get("url", ""),
        "image_url": metadata.get("image_url", ""),
        # Services store their price as ``cost``.
        "price": metadata.get("price") or metadata.get("cost"),
        "currency": metadata.get("currency"),
        "sku": metadata.get("sku", ""),
        "category": metadata.get("category", ""),
        "service_type": metadata.get("service_type", ""),
    }


def _description(content):
    """The description section of an embedded catalog item's text."""
    _, marker, description = (content or "").partition("Description:")
    return " ".join(description.split())[:MAX_DESCRIPTION_CHARS] if marker else ""


def _source_id(result):
    return (
        result.knowledge_base_id
        or result.product_id
        or result.service_id
        or result.id
    )


class KnowledgeSearchTools:
    """One search tool per knowledge space, registered conditionally."""

    def __init__(self, chatbot_id, spaces, *, vector_service=None):
        self.chatbot_id = chatbot_id
        self.spaces = set(spaces)
        self.vector_service = vector_service or KnowledgeVectorService()

    async def _search(self, query, tool_context, space):
        limit = (
            KNOWLEDGE_RESULT_LIMIT
            if space == KnowledgeSpaceTypes.DOCUMENT
            else CATALOG_RESULT_LIMIT
        )
        try:
            results = await sync_to_async(
                self.vector_service.search,
                thread_sensitive=False,
            )(
                query,
                chatbot_id=self.chatbot_id,
                space=space,
                limit=limit,
            )
        except Exception:
            logger.exception(
                "Knowledge retrieval failed for chatbot %s (%s)",
                self.chatbot_id,
                space,
            )
            return {
                "status": "error",
                "sources": [],
                "message": "Search is temporarily unavailable.",
            }

        if not results:
            return {"status": "not_found", "sources": []}

        refs = dict(tool_context.state.get(KNOWLEDGE_REFS_KEY) or {})
        known_results = dict(
            tool_context.state.get(KNOWLEDGE_RESULTS_KEY) or {},
        )
        ref_by_source = {
            source_id: ref for ref, source_id in refs.items()
        }

        sources = []
        for result in results:
            source_id = _source_id(result)
            ref = ref_by_source.get(source_id)
            if ref is None:
                ref = f"K{len(refs) + 1}"
                refs[ref] = source_id
                ref_by_source[source_id] = ref

            source = {
                "ref": ref,
                "content": result.content,
                **_result_metadata(result),
            }
            sources.append(source)
            known_results[source_id] = _result_metadata(result) | {
                "ref": ref,
                "description": _description(result.content),
            }

        tool_context.state[KNOWLEDGE_REFS_KEY] = refs
        tool_context.state[KNOWLEDGE_RESULTS_KEY] = known_results

        return {"status": "ok", "sources": sources}

    async def search_documents(
        self,
        query: str,
        tool_context: ToolContext,
    ) -> dict:
        """Search the business's uploaded documents and web pages for
        policies, FAQs, and other written knowledge.

        Args:
            query: A concise standalone search query.
        """
        return await self._search(
            query, tool_context, KnowledgeSpaceTypes.DOCUMENT,
        )

    async def search_products(
        self,
        query: str,
        tool_context: ToolContext,
    ) -> dict:
        """Search the business's product catalog. Returns matching
        products with name, SKU, price, currency, description, product
        url, and image url.

        Args:
            query: Product-oriented search terms (name, category, or need).
        """
        return await self._search(
            query, tool_context, KnowledgeSpaceTypes.PRODUCT,
        )

    async def search_services(
        self,
        query: str,
        tool_context: ToolContext,
    ) -> dict:
        """Search the business's service catalog. Returns matching
        services with name, type, cost, currency, and description.

        Args:
            query: Service-oriented search terms (name, type, or need).
        """
        return await self._search(
            query, tool_context, KnowledgeSpaceTypes.SERVICE,
        )

    def get_tools(self) -> list:
        tools = []
        if KnowledgeSpaceTypes.DOCUMENT in self.spaces:
            tools.append(FunctionTool(func=self.search_documents))
        if KnowledgeSpaceTypes.PRODUCT in self.spaces:
            tools.append(FunctionTool(func=self.search_products))
        if KnowledgeSpaceTypes.SERVICE in self.spaces:
            tools.append(FunctionTool(func=self.search_services))
        return tools


class EscalationTools:
    """Record queries the chatbot could not handle for human follow-up."""

    def __init__(self, chatbot_id, session):
        self.chatbot_id = chatbot_id
        self.session = session

    def _execute_escalation(self, escalation_reason: str) -> dict:
        reason = _clean_text(
            escalation_reason,
            field_name="escalation_reason",
            max_length=MAX_ESCALATION_REASON_LENGTH,
        )
        escalation = record_escalation(
            session=self.session,
            question=reason,
            reason="no_answer",
        )
        return {
            "status": "recorded",
            "escalation_id": str(escalation.id),
            "escalation_reason": reason,
        }

    async def request_human_escalation(
        self,
        escalation_reason: str,
    ) -> dict:
        """Record that this conversation needs human follow-up.

        Call when the user explicitly requests a human, a configured
        escalation rule applies, or you cannot safely or confidently
        answer an in-scope question.

        Args:
            escalation_reason: The question or reason that needs a human,
                quoted from the conversation.
        """
        return await sync_to_async(
            self._execute_escalation,
            thread_sensitive=True,
        )(escalation_reason)

    def get_tools(self) -> list:
        return [FunctionTool(func=self.request_human_escalation)]


class RecommendationTools:
    """The agent picks the best few searched items and names them here;
    the client renders them as cards after the reply (both modes)."""

    async def show_recommendations(
        self,
        refs: list[str],
        tool_context: ToolContext,
    ) -> dict:
        """Choose the products or services to recommend; they are shown
        to the user as cards after your reply. Call once, before writing
        the reply, with only the best matches (at most 3).

        Args:
            refs: The `ref` values (for example ["K1", "K3"]) of the 2-3
                best matching products or services, best first.
        """
        items = recommendations_for_refs(
            (refs or [])[:MAX_RECOMMENDATIONS],
            tool_context.state.get(KNOWLEDGE_REFS_KEY) or {},
            tool_context.state.get(KNOWLEDGE_RESULTS_KEY) or {},
        )
        if not items:
            return {
                "status": "not_found",
                "items": [],
                "message": "No searched product or service matches these refs.",
            }
        return {"status": "ok", "items": items}

    def get_tools(self) -> list:
        return [FunctionTool(func=self.show_recommendations)]


def build_tools(chatbot_id, session, *, spaces, mode=ChatMode.TEXT) -> list:
    """Single source of truth for agent tools, conditional per space.

    ``mode`` is accepted for callers; both modes get the same tools.
    """
    spaces = set(spaces)
    tools = [
        *KnowledgeSearchTools(chatbot_id, spaces).get_tools(),
        *EscalationTools(chatbot_id, session).get_tools(),
    ]
    if spaces & {KnowledgeSpaceTypes.PRODUCT, KnowledgeSpaceTypes.SERVICE}:
        tools.extend(RecommendationTools().get_tools())
    return tools
