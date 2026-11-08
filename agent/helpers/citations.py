"""Turn inline [K1] markers in the model's reply into source ids.

Without a sub-agent output_schema there is no structured `source_ids` field.
The knowledge tool hands out short refs (K1, K2, ...) and the model cites them
inline. Code maps refs back to real ids and strips the markers, so unknown or
hallucinated refs are ignored automatically.
"""

import re

_GROUP = re.compile(r"\[\s*K\d+(?:\s*[,;]\s*K\d+)*\s*\]")
_REF = re.compile(r"K\d+")


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