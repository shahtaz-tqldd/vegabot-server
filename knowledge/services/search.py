"""Agent-facing knowledge search across the typed vector spaces."""

from knowledge.utils.choices import KnowledgeSpaceTypes
from vector_store.services.vectorize import KnowledgeVectorService

DEFAULT_SEARCH_LIMIT = 8


def search_knowledge(
    query,
    *,
    chatbot,
    space=None,
    limit=DEFAULT_SEARCH_LIMIT,
    vector_service=None,
):
    """Search a chatbot's knowledge in the vector store.

    ``space`` optionally scopes retrieval to one knowledge type
    (``document``, ``product``, ``service``); the agent uses it to answer
    shopping, booking, and general questions from the right space.
    Returns ``VectorSearchResult`` items ordered by hybrid relevance.
    """
    if space is not None:
        space = KnowledgeSpaceTypes(space)
    service = vector_service or KnowledgeVectorService()
    return service.search(
        query,
        chatbot_id=getattr(chatbot, "pk", chatbot),
        space=space,
        limit=limit,
    )
