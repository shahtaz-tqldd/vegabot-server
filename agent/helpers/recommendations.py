"""Structured product/service cards for a reply.

Cards are built from the search results already captured in tool state
(``KNOWLEDGE_RESULTS_KEY``), so no reply-time database queries. Text
replies recommend what they cite; voice replies recommend what the agent
passes to ``show_recommendations``.
"""

from knowledge.utils.choices import KnowledgeSpaceTypes

RECOMMENDABLE_SPACES = (KnowledgeSpaceTypes.PRODUCT, KnowledgeSpaceTypes.SERVICE)
MAX_RECOMMENDATIONS = 6


def recommendation_item(source_id, metadata):
    """One card; every key is always present so clients can rely on it."""
    return {
        "type": metadata.get("space"),
        "id": str(source_id),
        "name": metadata.get("name") or "",
        "description": metadata.get("description") or "",
        "price": metadata.get("price"),
        "currency": metadata.get("currency"),
        "url": metadata.get("url") or "",
        "sku": metadata.get("sku") or "",
        "category": metadata.get("category") or "",
        "service_type": metadata.get("service_type") or "",
    }


def build_recommendations(source_ids, known_results):
    """Cards for the product/service sources among ``source_ids``, in order."""
    items = []
    seen = set()
    for source_id in source_ids or []:
        key = str(source_id)
        metadata = (known_results or {}).get(key)
        if (
            key in seen
            or not metadata
            or metadata.get("space") not in RECOMMENDABLE_SPACES
        ):
            continue
        seen.add(key)
        items.append(recommendation_item(key, metadata))
        if len(items) >= MAX_RECOMMENDATIONS:
            break
    return items


def recommendations_for_refs(refs, ref_map, known_results):
    """Resolve model-facing refs (K1, K2, ...) to cards; unknown refs drop."""
    source_ids = [
        ref_map[ref.strip()]
        for ref in refs or []
        if isinstance(ref, str) and ref.strip() in (ref_map or {})
    ]
    return build_recommendations(source_ids, known_results)
