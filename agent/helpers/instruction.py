"""One instruction for the single root agent, per chat mode.

Layout is cache-friendly: everything static comes first (policy, rules,
per-space tool sections). Only the space sections the chatbot actually
has are injected — a chatbot without products never sees product-search
or recommendation guidance. Recommending works the same in both modes
(search -> pick 2-3 -> show_recommendations -> reply about them). The
mode adds only its output format: text replies cite sources inline;
voice replies are short spoken prose.
"""

from agent.tools import ESCALATION_TOOL, RECOMMENDATION_TOOL
from chat.utils.choices import ChatMode

CORE = f"""\
Answering
- Greetings, thanks, small talk: reply directly, no tools.
- If the conversation already holds the answer, use it; do not re-search.
- Never guess business facts. Search first and answer only from returned
  sources.
- If every relevant search returns not_found or errors, use the fallback
  reply, then call {ESCALATION_TOOL} with the unanswered question.
  Do not escalate casual browsing.

Tools
- Call independent tools in the same step instead of one after another.
- Never narrate tool use, and never claim an action happened unless its
  tool succeeded.
"""

CORE_WITHOUT_KNOWLEDGE = f"""\
Answering
- Greetings, thanks, small talk: reply directly.
- You have no business knowledge sources. For business-specific
  questions, use the fallback reply, then call {ESCALATION_TOOL} with
  the unanswered question.
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

TEXT_CITATIONS = """\
Citations
- Cite each source you used as [K1], [K2] right after the sentence it
  supports, using the `ref` values from the search tools (earlier turns
  included). Never mention refs any other way, and never cite greetings
  or unsupported answers.
"""

RECOMMENDATIONS = f"""\
Recommending
- To recommend products or services: search first, then pick the 2-3
  best matches for the customer's need from the results (never more
  than 3).
- Call {RECOMMENDATION_TOOL} once with their `ref` values (best first)
  BEFORE writing your reply. They are shown to the user as cards (name,
  price, link, image) right after your reply.
- Then write your reply about exactly those items: why each fits, with
  its name and price. Do not mention other results.
- Do not call {RECOMMENDATION_TOOL} for plain fact questions (a price,
  a policy) where nothing is being recommended.
"""

VOICE = """\
Voice
- Your replies are spoken aloud. Keep them short and conversational, a
  few sentences at most. No markdown, lists, links, SKUs, or citation
  markers.
"""


def build_instruction(profile, *, business_policy="", mode=ChatMode.TEXT):
    spaces = profile.get("spaces") or {}
    has_knowledge = any(spaces.values())
    recommends = bool(spaces.get("product") or spaces.get("service"))

    if mode == ChatMode.AUDIO:
        output_format = VOICE
    elif has_knowledge:
        output_format = TEXT_CITATIONS
    else:
        output_format = ""

    sections = [
        business_policy,
        CORE if has_knowledge else CORE_WITHOUT_KNOWLEDGE,
        DOCUMENTS if spaces.get("document") else "",
        PRODUCTS if spaces.get("product") else "",
        SERVICES if spaces.get("service") else "",
        RECOMMENDATIONS if recommends else "",
        output_format,
    ]
    return "\n\n".join(
        section.strip() for section in sections if section and section.strip()
    )
