# Chatbot agents (Google ADK 2.1.0)

`AgentClient` runs one root `LlmAgent` as a thin coordinator over dynamically
composed specialists, currently `knowledge_agent` and `appointment_agent`.

## Architecture

- **Root coordinator** (`agent/root_agent.py`): greets, delegates, and relays.
  It owns only the cross-cutting conversation tools (plan-gated lead score
  and always-available human escalation), plus dynamically generated
  specialist delegation tools. It has no `output_schema`, so its final
  answer is plain text that can be streamed token-by-token.
- **Specialists** (`agent/sub_agents/<name>/agent.py`): `mode="single_turn"`
  agents exposed to the coordinator as delegate tools. Each keeps a structured
  output schema (`content` + `source_ids`), owns its domain tools, and shares
  the conversation tools (`record_lead_score`, `request_human_escalation`).
- **Registry** (`agent/sub_agents/__init__.py`): an ordered tuple of
  `build(chatbot, session) -> LlmAgent | None` factories. A factory returns
  `None` when the chatbot lacks that feature, and the coordinator is assembled
  from whatever remains. Adding a later specialist (service recommendation,
  quotation generation, product recommendation) means adding a package with a
  `build` factory and appending it to `SUB_AGENT_FACTORIES`; coordinator
  instructions, delegate tools, and streaming adapt automatically.
- **Shared policy** (`agent/helpers/global_instruction.py`): the one source of
  truth for identity, greeting, scope, safety, fallback, lead scoring, and
  escalation. `GlobalInstructionPlugin` applies it once to the root and every
  enabled specialist. Domain prompts do not repeat it. Agents are explicitly
  prohibited from exposing Gemini or other provider identity.
- **Feature gates**: the knowledge specialist and human escalation are
  enabled by default regardless of chatbot config; the appointment
  specialist requires `Chatbot.appointment_booking_enabled`; the lead
  score tool requires the plan's lead insights feature. With no
  specialist delegations, the root becomes the whole chatbot and carries
  the conversation tools itself.

Specialists use ADK's collaborative `single_turn` delegation: the coordinator
sends a self-contained request, the specialist returns its structured answer,
and the coordinator relays it faithfully (dates, slot offers, booking
statuses, citations) to the visitor. If the coordinator emits no usable final
text after a successful delegation, `AgentClient` returns the validated
specialist outcome instead of replacing it with the generic chatbot fallback.
When several specialists handled independent intents, their latest outcomes
are preserved in delegation order for that fallback path. A coordinator reply,
when present, remains authoritative for multi-intent synthesis.

The runner is created from an ADK `App`, with event compaction every three
events, one event of overlap, and context caching for contexts of at least
2,048 tokens. Each turn lazily creates one Vertex client (HTTP pool), shared
by the coordinator and specialists (`agent/helpers/model.py`). The turn closes
both async and sync transports before its event loop exits, including on errors
and cancellation. Clients are never reused across `async_to_sync` event loops
or concurrent turns. Trusted backend booking data is invocation-scoped while
the runner records the booking event in normal conversation memory.

Booking operations and the appointment tool factory live in
`agent/sub_agents/appointment/tools.py`.

## Usage

```python
from agent.client import AgentClient

client = AgentClient(chatbot, chat_session)
reply = client.generate_reply_sync(visitor_message=message)
booking_reply = client.generate_booking_reply_sync(appointment_id=appointment.id)
```

Lifecycle entry points return visitor-facing message data and persist live
replies and usage atomically:

```json
{
  "content": "We offer …",
  "metadata": {
    "sources": []
  }
}
```

`source_ids` comes from the knowledge specialist's structured answer and is
validated against every source retrieved in this conversation: the search tool
records retrieved IDs in session state (`knowledge_source_ids`), so a
follow-up answered from prior conversation context may keep citing earlier
sources without re-searching. Duplicate, invented, and unknown IDs are
removed. Invalid specialist structured output fails the turn (ADK rejects it
before a reply is produced) instead of leaking raw output to the UI.

Token usage sums ADK model events across all calls in the invocation,
including tool-selection and final-response calls. `output_tokens` includes
thinking; `thinking_tokens` and
`cached_input_tokens` are subsets, not additional totals. `cost` is an
estimated USD amount using `GEMINI_INPUT_COST_PER_MILLION` and
`GEMINI_OUTPUT_COST_PER_MILLION`. It excludes retrieval embeddings and other
infrastructure charges and does not apply cache discounts or pricing tiers.
Configure rates for your selected model. Missing provider usage contributes
zero; this estimate is not a billing receipt.


## Appointment UI flow

1. Booking intent and date-offer follow-ups are delegated to
   `appointment_agent`, which asks for a preferred date and resolves it in the
   chatbot timezone.
2. `find_appointment_availability` checks the requested day. If unavailable, it
   returns up to three later available dates within the booking horizon.
3. The specialist returns only a visitor-facing `content` message and nullable
   `agreed_date`. It never exposes slot counts or appointment times. A requested
   available date is immediately agreed; the trusted availability-tool result
   also establishes that date in code if the model omits it. An alternative
   remains unagreed until the visitor clearly accepts it.
4. After `agreed_date` is returned, Python fetches that day's full choices and
   adds them directly to `metadata.appointments.available_slots` for the booking
   UI. The model does not generate, select, or describe slots.

For live visitor replies, `AgentClient.generate_reply_sync()` converts the
internal agent envelope into `{content, metadata}`, saves the AI message, and
records its usage in one transaction. Metadata includes only sections produced
by that turn: `appointments`, `lead_analytics`, `escalation`, and enriched
`sources` records. The Celery task handles capacity, lifecycle events, and
retries only.

5. POST the selected `starts_at` and customer `collected_fields` to
   `/api/v1/chatbots/{public_key}/book-appointment/?session_id={session_id}`
   using the conversation bearer token. The endpoint rechecks availability
   while locking the booking configuration, derives `ends_at`, and stores
   the trusted chat-session association itself. In the same transaction it
   appends an idempotent visitor message with blank content and an
   `appointment.submitted` metadata payload containing the selected slot and
   collected fields, so the booking appears in the conversation timeline.
6. After saving, the endpoint queues `generate_ai_reply_task` with the trusted
   appointment and session IDs. The task asks `AgentClient` to verify the saved
   booking, generate the acknowledgment, persist its usage, and save the next AI
   chat message under `appointment:<id>`. The normal `message.created` signal
   delivers that message to the UI. The booking response contains
   `reply_queued`; it does not synchronously return another chat message.

The acknowledgment uses the saved pending/confirmed status. Repeated booking
submissions do not enqueue another reply once its chat message exists. If AI is
disabled or generation fails, `AgentClient` saves a truthful deterministic
acknowledgment without an AI usage record.

## In-conversation lead scoring

Specialists call `record_lead_score` only after a meaningful qualification
signal, such as a concrete need, timeline, budget, booking intent, or
committed next step. The tool validates a 0–100 integer, updates
`Lead.lead_score`, and returns its concise rationale for that turn's message
metadata. It does not store the changing rationale in `ChatSession.metadata`.
If the session has not yet been associated with a lead, the tool returns
`recorded: false` and writes nothing.

## Human escalation

Specialists and the root call
`request_human_escalation` for explicit human requests, configured
escalation rules, questions they cannot safely or confidently answer, or
required tool failures; the tool is always exposed, independent of
chatbot config. The tool atomically sets
`requires_attention`, stores its concise escalation explanation directly in
the `attention_reason` text field, and updates `attention_requested_at` on
`ChatSession`. It creates the system timeline event and chatbot
`AI_NOTIFICATION` in the same transaction; repeated calls with the same active
reason are idempotent.
Test chat sessions get the same tool so the agent never calls an
unexposed tool, but the backend runs as a dry run there: it returns
success without setting attention fields, writing timeline events, or
notifying the team.
Existing resolution/takeover services remain responsible for clearing the
attention fields.

## Configuration and integration boundary

Initialize Django before importing the client. The existing settings are
used: `GEMINI_CHAT_MODEL`, `GEMINI_CHAT_MAX_OUTPUT_TOKENS`,
`GOOGLE_CLOUD_PROJECT_ID`, `GOOGLE_CLOUD_LOCATION`, ADC credentials, and the
two model cost settings. `ADK_DB_URL` is required unless a session service is
explicitly injected (for example `InMemorySessionService` in tests). Use a
compatible async SQLAlchemy URL for your database, e.g.
`postgresql+asyncpg://...`.

Conversation IDs use `ChatSession.id`; default ADK user identity is scoped to
the chatbot and conversation. An optional `user_id` must remain stable across
turns. Callers must serialize turns per conversation across workers.

AgentClient does not register HTTP endpoints. It persists generated assistant
messages, public metadata, and AI usage; `chat.tasks` handles dispatch,
capacity, retries, and lifecycle events.

The existing `google-adk==2.1.0` dependency is retained. Specialists use
structured output with tools via native model support or ADK's
`set_model_response` fallback; Python validates the final response again,
and malformed specialist output is surfaced as a failed turn.

References checked September 17, 2026:
- [ADK collaborative sub-agent modes](https://adk.dev/workflows/collaboration/)
- [ADK LLM agents and structured output](https://google.github.io/adk-docs/agents/llm-agents/)
- [ADK run config streaming modes](https://google.github.io/adk-docs/runtime/run-config/)
- [Function tools and invocation-local state](https://google.github.io/adk-docs/tools-custom/function-tools/)
- [Async session services](https://google.github.io/adk-docs/sessions/session/)

Run deterministic tests (no model API calls or database needed):

```sh
env/bin/python manage.py test agent.tests.test_workflows
```

Tests exercise the real ADK runner with scripted model responses, including
streaming partials, dynamic specialist composition, and context-based
citations. `agent.tests.test_conversation_tools` additionally needs a
database. ORM/retrieval and their thread adapters are mocked; live Vertex
calls, database persistence, and concurrent booking writers require
integration testing in your deployment.
