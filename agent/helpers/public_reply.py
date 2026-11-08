"""Build the visitor-facing reply payload from the internal envelope.

Source metadata comes from the vector results captured during the turn
(already in tool state) — no reply-time database queries.
"""


def build_source_metadata(result, collector_results):
    """Metadata for the sources cited in this reply, keyed by source id."""
    if not collector_results:
        return []
    sources = []
    for source_id in result.get("source_ids") or []:
        metadata = collector_results.get(str(source_id))
        if metadata:
            sources.append({"id": str(source_id), **metadata})
    return sources


def build_public_reply(response, collector_results):
    result = response["result"]
    return {
        "content": result["content"],
        "sources": build_source_metadata(result, collector_results),
        "escalation": (
            {
                "reason": response["result"]["escalation"]["escalation_reason"],
            }
            if response["result"].get("escalation")
            else None
        ),
    }
