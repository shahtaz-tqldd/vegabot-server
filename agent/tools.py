"""Tools of the agent, shared by text and audio mode.

Each knowledge space (documents, products, services) gets its own search
tool; tools are registered conditionally per chatbot so a chatbot without
products never sees (or pays for) the product-search tool or its prompt
section. ``show_recommendations`` exists in both modes for chatbots with
products or services: the agent searches a wide candidate set, picks the
best few, and names them with the tool; cards are sent after the reply.
Escalation is always available. Chatbots with product ordering enabled
also get the cart tools (add/remove/view cart, place order).

Tools that record session activity (escalations, cart changes, orders)
store it in the database and push a ``chat.*`` event to the session's
socket group, so the frontend can notify in realtime.
"""

from asgiref.sync import sync_to_async

from google.adk.tools import FunctionTool, ToolContext

from agent.helpers.recommendations import (
    MAX_RECOMMENDATIONS,
    recommendations_for_refs,
)
from app.utils.logger import logger
from base.socket.services.broadcaster import apublish_session_event
from chat.services import activity_events
from chat.services.commerce import (
    CartError,
    active_cart_items,
    add_to_cart,
    cart_summary,
    place_order,
    remove_from_cart,
)
from chat.services.escalations import record_escalation
from chat.utils.choices import ChatMode
from knowledge.models import Product
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
ADD_TO_CART_TOOL = "add_to_cart"
REMOVE_FROM_CART_TOOL = "remove_from_cart"
VIEW_CART_TOOL = "view_cart"
PLACE_ORDER_TOOL = "place_order"
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


async def _publish(session, event):
    """Push a session activity event; delivery never fails the tool."""
    if not event:
        return
    try:
        await apublish_session_event(session.id, event)
    except Exception:
        logger.exception("Session event %s not delivered", event.get("type"))


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
        event = (
            activity_events.escalation_created_event(self.session, escalation)
            if escalation.was_created
            else None
        )
        return {
            "status": "recorded",
            "escalation_id": str(escalation.id),
            "escalation_reason": reason,
        }, event

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
        result, event = await sync_to_async(
            self._execute_escalation,
            thread_sensitive=True,
        )(escalation_reason)
        await _publish(self.session, event)
        return result

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


def _money(value):
    return f"{value:.2f}"


class CartTools:
    """Cart and order taking for chatbots with product ordering enabled.

    The agent names products by the ``ref`` values search_products
    returned; prices always come from the catalog, never from the model.
    """

    def __init__(self, chatbot_id, session):
        self.chatbot_id = chatbot_id
        self.session = session

    # --- helpers ---
    @staticmethod
    def _product_source_id(ref, tool_context):
        """The product id behind a searched product's ref, or None."""
        ref = (ref or "").strip() if isinstance(ref, str) else ""
        source_id = (tool_context.state.get(KNOWLEDGE_REFS_KEY) or {}).get(ref)
        if source_id is None:
            return None
        metadata = (
            tool_context.state.get(KNOWLEDGE_RESULTS_KEY) or {}
        ).get(str(source_id)) or {}
        if metadata.get("space") != KnowledgeSpaceTypes.PRODUCT:
            return None
        return str(source_id)

    @staticmethod
    def _refs_by_product(tool_context):
        refs = tool_context.state.get(KNOWLEDGE_REFS_KEY) or {}
        return {str(source_id): ref for ref, source_id in refs.items()}

    def _cart_for_model(self, refs_by_product):
        """The current cart in the compact form the model reads."""
        items = list(active_cart_items(self.session))
        return {
            "items": [
                {
                    "ref": refs_by_product.get(str(item.product_id), ""),
                    "name": item.product_name,
                    "quantity": item.quantity,
                    "unit_price": _money(item.unit_price),
                    "line_total": _money(item.line_total),
                    "currency": item.currency,
                }
                for item in items
            ],
            **cart_summary(items),
        }

    @staticmethod
    def _not_a_product(ref):
        return {
            "status": "not_found",
            "message": (
                f"{ref!r} is not a product from search_products in this "
                "conversation. Search for the product first."
            ),
        }

    # --- sync work (database) ---
    def _add(self, source_id, quantity, refs_by_product):
        product = Product.objects.filter(
            pk=source_id, chatbot_id=self.chatbot_id, is_enabled=True,
        ).first()
        if product is None:
            return {
                "status": "not_found",
                "message": "This product is no longer available.",
            }, None
        try:
            item = add_to_cart(
                session=self.session, product=product, quantity=quantity,
            )
        except CartError as exc:
            return {"status": "error", "message": str(exc)}, None
        event = activity_events.cart_item_event(self.session, item)
        return {
            "status": "ok",
            "added": {"name": item.product_name, "quantity": quantity},
            "cart": self._cart_for_model(refs_by_product),
        }, event

    def _remove(self, source_id, refs_by_product):
        item = remove_from_cart(session=self.session, product_id=source_id)
        if item is None:
            return {
                "status": "not_found",
                "message": "This product is not in the cart.",
                "cart": self._cart_for_model(refs_by_product),
            }, None
        event = activity_events.cart_item_event(
            self.session, item, removed=True,
        )
        return {
            "status": "ok",
            "removed": item.product_name,
            "cart": self._cart_for_model(refs_by_product),
        }, event

    def _place(self):
        try:
            order = place_order(session=self.session)
        except CartError as exc:
            return {"status": "error", "message": str(exc)}, None
        event = activity_events.order_placed_event(self.session, order)
        return {
            "status": "ok",
            "order_number": order.order_number,
            "item_count": order.item_count,
            "total": _money(order.total_amount),
            "currency": order.currency,
        }, event

    async def _run(self, func, *args):
        result, event = await sync_to_async(func, thread_sensitive=True)(*args)
        await _publish(self.session, event)
        return result

    # --- tools ---
    async def add_to_cart(
        self,
        ref: str,
        quantity: int,
        tool_context: ToolContext,
    ) -> dict:
        """Add a product to the customer's cart. Only call when the
        customer asks to add or buy a product.

        Args:
            ref: The product's `ref` from search_products (for example "K2").
            quantity: How many to add (1 unless the customer said otherwise).
        """
        source_id = self._product_source_id(ref, tool_context)
        if source_id is None:
            return self._not_a_product(ref)
        return await self._run(
            self._add, source_id, quantity, self._refs_by_product(tool_context),
        )

    async def remove_from_cart(
        self,
        ref: str,
        tool_context: ToolContext,
    ) -> dict:
        """Remove a product from the customer's cart.

        Args:
            ref: The product's `ref` (as listed by view_cart).
        """
        source_id = self._product_source_id(ref, tool_context)
        if source_id is None:
            return self._not_a_product(ref)
        return await self._run(
            self._remove, source_id, self._refs_by_product(tool_context),
        )

    async def view_cart(self, tool_context: ToolContext) -> dict:
        """Return the customer's current cart: items, quantities, line
        totals, and the cart total with its currency."""
        refs_by_product = self._refs_by_product(tool_context)
        cart = await sync_to_async(
            self._cart_for_model, thread_sensitive=True,
        )(refs_by_product)
        return {"status": "ok" if cart["items"] else "empty", "cart": cart}

    async def place_order(self) -> dict:
        """Place an order for everything in the cart. Only call after you
        have read the cart back to the customer (items and total) and they
        explicitly confirmed the order."""
        return await self._run(self._place)

    def get_tools(self) -> list:
        return [
            FunctionTool(func=self.add_to_cart),
            FunctionTool(func=self.remove_from_cart),
            FunctionTool(func=self.view_cart),
            FunctionTool(func=self.place_order),
        ]


def build_tools(
    chatbot_id, session, *, spaces, ordering=False, mode=ChatMode.TEXT,
) -> list:
    """Single source of truth for agent tools, conditional per space.

    ``ordering`` adds the cart tools; it only applies with the product
    space. ``mode`` is accepted for callers; both modes get the same tools.
    """
    spaces = set(spaces)
    tools = [
        *KnowledgeSearchTools(chatbot_id, spaces).get_tools(),
        *EscalationTools(chatbot_id, session).get_tools(),
    ]
    if spaces & {KnowledgeSpaceTypes.PRODUCT, KnowledgeSpaceTypes.SERVICE}:
        tools.extend(RecommendationTools().get_tools())
    if ordering and KnowledgeSpaceTypes.PRODUCT in spaces:
        tools.extend(CartTools(chatbot_id, session).get_tools())
    return tools
