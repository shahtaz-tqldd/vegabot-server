"""Source metadata for the client from the internal envelope.

Metadata comes from the vector results captured during the turn
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
