"""Turn inline [K1] markers in the model's reply into source ids.

Without a sub-agent output_schema there is no structured `source_ids` field.
The knowledge tool hands out short refs (K1, K2, ...) and the model cites them
inline. Code maps refs back to real ids and strips the markers, so unknown or
hallucinated refs are ignored automatically.
"""

import re

_GROUP = re.compile(r"\[\s*K\d+(?:\s*[,;]\s*K\d+)*\s*\]")
_REF = re.compile(r"K\d+")
# An unterminated tail that could still become a citation group.
_OPEN_TAIL = re.compile(r"\[\s*(?:K\d*(?:\s*[,;]\s*(?:K\d*)?)*)?\s*$")
_MAX_HELD_CHARS = 64


def extract_citations(text, refs):
    """Return (clean_text, source_ids) for a reply and a {ref: source_id} map."""
    source_ids = []

    def _collect(match):
        for ref in _REF.findall(match.group(0)):
            source_id = refs.get(ref)
            if source_id and source_id not in source_ids:
                source_ids.append(source_id)
        return ""

    clean = _GROUP.sub(_collect, text or "")
    clean = re.sub(r"[ \t]+([.,;:!?])", r"\1", clean)  # "word [K1]." -> "word."
    clean = re.sub(r"[ \t]{2,}", " ", clean)
    return clean.strip(), source_ids


class CitationStreamFilter:
    """Strip [K1] markers from streamed chunks, even when split across
    chunks: a tail that may still become a marker is held back until the
    next chunk (or ``flush``) decides it."""

    def __init__(self):
        self._held = ""

    def feed(self, chunk):
        text = _GROUP.sub("", self._held + (chunk or ""))
        self._held = ""
        start = text.rfind("[")
        if start != -1 and len(text) - start <= _MAX_HELD_CHARS:
            if _OPEN_TAIL.fullmatch(text[start:]):
                self._held = text[start:]
                text = text[:start]
        return text

    def flush(self):
        text, self._held = _GROUP.sub("", self._held), ""
        return text
