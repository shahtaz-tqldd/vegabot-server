# Chatbot agent (Google ADK 2.1.0)

One customer-facing agent with two configurations, selected per turn by chat
mode. The client protocol is in [api_module/chat.md](api_module/chat.md).

| | `text` mode | `audio` mode |
| --- | --- | --- |
| Entry point | `agent.client.AgentClient` | `agent.voice.VoiceSession` |
| Model | `GEMINI_CHAT_MODEL` / DisonConfig `text_llm_model` | `GEMINI_LIVE_CHAT_MODEL` / DisonConfig `live_llm_model` (+ `voice_name`) |
| ADK run | `run_async`, `StreamingMode.SSE`, one run per message | `run_live`, `StreamingMode.BIDI`, one run per voice period |
| Reply format | text with inline `[K1]` citations | short spoken prose, no markup |
| Recommendations | cited product/service sources | `show_recommendations(refs)` tool calls |
| Cost rates | `GEMINI_INPUT/OUTPUT_COST_PER_MILLION` | `GEMINI_LIVE_INPUT/OUTPUT_COST_PER_MILLION` |

## Layout

```text
agent/
  root_agent.py     build_root_agent(chatbot, session, profile=, mode=): the one LlmAgent
  client.py         AgentClient: text turns (streamed), load_session_for_turn
  voice.py          VoiceSession: live audio turns
  reply.py          Celery task for REST-sent messages (attachments)
  tools.py          search_documents/products/services, show_recommendations, escalation
  schema.py         internal result/usage envelope
  helpers/
    instruction.py      prompt sections per knowledge space and mode
    global_instruction.py  identity + business policy
    profile.py          cached per-chatbot profile (policy, knowledge-space flags)
    model.py            per-mode model, Vertex client scope, DisonConfig overrides
    runtime.py          shared ADK app (APP_NAME), runner, session service
    stream.py           TurnStream: the agent.response.* event sequence
    citations.py        [K1] extraction + streaming filter
    recommendations.py  product/service cards from captured search results
    turn_collector.py   reply/usage/escalation extraction from a text run
    persistence.py      store agent message + AI usage
    lock.py             one agent run per chat session
    usage.py            token accounting and cost
```

## One agent, shared memory

Both modes build the agent with the same name (`assistant`), policy, knowledge
tools, and escalation tool, under one ADK app (`APP_NAME`). A chat session
therefore has a single ADK session:

- a voice period starts with the typed history (`run_live` replays it), and
- text turns see what was said, because ADK stores final transcriptions as
  conversation content.

ADK rejects writes from a stale session object, so two runs must never write to
one chat session at once. `helpers/lock.py` holds a cache lock per session
(`voice:<channel>` for a voice period, `text:<channel>` for a socket text turn,
`task:<message>` for a REST-triggered turn). The socket rejects overlapping
requests with `code: "busy"`. The Celery task waits up to 60 s. The REST send
endpoint returns 409 while voice is active.

## Knowledge-conditional tools and prompt

`helpers/profile.py` records which knowledge spaces a chatbot has (documents,
products, services). `build_tools` and `build_instruction` include only the
matching search tools and prompt sections. `show_recommendations` and the
recommendation guidance are added only for chatbots with products or services,
so the agent never searches or recommends a catalog the business doesn't have.
A chatbot with no knowledge gets an instruction to use the fallback reply and
escalate.

## Recommendations

Search tools store each result's display metadata in ADK state
(`knowledge_results`, keyed by source id, with short `ref`s like `K1`). Cards
are built from that state, so building them costs no database queries:

- text: `TurnCollector` turns the cited source ids of products/services into cards;
- audio: the agent calls `show_recommendations(["K1", ...])`, and the tool resolves refs to cards.

Cards are stored on the agent `ChatMessage.recommendations` and sent after the
reply text as `agent.response.recommendations`.

## Streaming

`TurnStream` emits `agent.response.started → delta* → recommendations? →
completed | failed` for both modes. Text deltas pass through
`CitationStreamFilter`, which strips `[K1]` markers even when they are split
across chunks. Voice deltas are the live model's output transcription, and the
agent audio goes out as binary frames. The socket consumer delivers events to
the requesting connection. The Celery task publishes them to the session group
(`apublish_session_event`).

## Persistence and usage

User messages are stored with `mode` (voice ones from the final input
transcription). Agent replies are stored with `mode`, `recommendations`, and
`response_time_ms`. AI usage is recorded per reply with the model actually
used and the cost at that mode's rates.

## Tests

```sh
CACHE_URL= CHANNEL_LAYER_BACKEND=memory env/bin/python manage.py test agent base.socket chat
```

The tests mock model calls. `agent/tests/test_voice.py` drives `VoiceSession`
with real ADK `Event` objects, and `base/socket/tests` covers mode switching
with fake agent sessions. Live Vertex calls need integration testing in a
deployment.
