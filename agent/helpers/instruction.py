"""One instruction for the single root agent.

Layout is cache-friendly: everything static comes first (policy, rules,
per-space tool sections). Only the space sections the chatbot actually
has are injected — a chatbot without products never sees product-search
guidance.
"""

from agent.tools import ESCALATION_TOOL

CORE = """\
Answering
- Greetings, thanks, small talk: reply directly, no tools.
- If the conversation already holds the answer, use it; do not re-search.
- Never guess business facts. Search first, answer only from returned
  sources, and cite each source you used as [K1], [K2] right after the
  sentence it supports, using the `ref` values from the search tools
  (earlier turns included). Never mention refs any other way, and never
  cite greetings or unsupported answers.
- If every relevant search returns not_found or errors, use the fallback
  reply, then call request_human_escalation with the unanswered question.
  Do not escalate casual browsing.

Tools
- Call independent tools in the same step instead of one after another.
- Never narrate tool use, and never claim an action happened unless its
  tool succeeded.
"""

DOCUMENTS = """\
Documents
- General business questions (policies, shipping, returns, FAQs, anything
  written): call search_documents with a concise standalone query, also
  for follow-ups that need new facts.
"""

PRODUCTS = """\
Products
- Product availability, pricing, comparisons, recommendations, or specs:
  call search_products with product-oriented terms (name, category, or
  the customer's need).
- Quote the product's price exactly as returned, together with its
  currency (for example "29.99 USD"). Never compute or convert prices.
- When several products match, briefly compare the best options instead
  of dumping every result.
"""

SERVICES = """\
Services
- Service offerings, pricing, duration, or booking questions: call
  search_services with service-oriented terms (name, type, or the
  customer's need).
- Quote the service cost exactly as returned, together with its currency.
"""


def build_instruction(profile, *, business_policy=""):
    spaces = profile.get("spaces") or {}
    sections = [
        business_policy,
        CORE,
        DOCUMENTS if spaces.get("document") else "",
        PRODUCTS if spaces.get("product") else "",
        SERVICES if spaces.get("service") else "",
    ]
    return "\n\n".join(
        section.strip() for section in sections if section and section.strip()
    )


def attachment_note(has_attachments, attachment_types):
    """Per-message attachment note; travels with the message it
    describes, keeping it out of the cached system prompt."""
    if not has_attachments:
        return ""
    kinds = ", ".join(attachment_types or []) or "files"
    return (
        f"[The user attached: {kinds}. Use any extracted file text the "
        "platform provides in this message. Answer questions the "
        "attachments answer directly; if they are unclear or cannot be "
        f"answered, call {ESCALATION_TOOL} so a human reviews them.]\n"
    )
