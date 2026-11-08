# Streaming AI chat replies end to end (widget + admin)

## HOW IT WORKS (backend)

One turn of an AI reply now emits, over the existing socket groups:

ai.response.started      -> generation began (already existed)
ai.response.delta*       -> streamed text chunks (new)
message.created          -> the final persisted AI message (already existed)
ai.response.failed       -> generation failed; discard the stream

`ai.response.delta` events carry, in `data`:

external_id  "ai:{visitor_message_id}" (widget) or
             "test-ai:{visitor_message_id}" (admin test chat)
delta        the text chunk for this event
sequence     1, 2, 3, ... per external_id; a gap means a chunk was dropped

Delivery uses the same groups as every chat event
(base/socket/services/broadcaster.py):

chat_session_{id}                  -> the widget socket of that session
notifications.chatbot.{chatbot_id} -> every connected admin dashboard

so widget and admin receive the same stream without joining anything new.

## SERVER SIDE

AgentClient (agent/client.py):

stream_reply_sync(       # live visitor sessions (called by the celery task)
    visitor_message=..., user_id=..., on_delta=<callback>)
stream_test_reply_sync(  # admin test sessions
    message=..., user_id=..., on_delta=<callback>)

Both run the same turn as their generate_* counterparts, with ADK
`StreamingMode.SSE`: every partial text chunk is passed to `on_delta`
(sync or async callable receiving one chunk). Tool-call argument chunks
and thought parts are never streamed; a failing callback never kills the
turn; usage/cost are still counted once, from the aggregated event.

The celery task `generate_ai_reply_task` already streams: it builds the
publisher with `build_stream_delta_publisher(session_id, chatbot_id,
external_id)` (chat/services/events.py). Admin test chat streams when the
view passes `send_test_message(..., stream=True)` (chat/api/v1/client/
views.py TestChatMessageCreateView already does).

## WIDGET SIDE (visitor sessions)

The widget socket already joins `chat_session_{id}`; no subscribe change
is needed. Pseudocode:

pending = {}   # external_id -> {text, lastSequence}

socket.on("ai.response.started", e => {
    // e.data.in_reply_to is the visitor message id; the reply's
    // external_id will be `ai:{that id}`.
});
socket.on("ai.response.delta", e => {
    const {external_id, delta, sequence} = e.data;
    const entry = pending[external_id] ??= {text: "", lastSequence: 0};
    if (sequence === entry.lastSequence + 1) {   // drop gaps/dupes
        entry.text += delta;
        entry.lastSequence = sequence;
        renderStreamingBubble(external_id, entry.text);
    }
});
socket.on("message.created", e => {
    if (e.data.external_id && pending[e.data.external_id]) {
        finalizeBubble(pending[e.data.external_id], e.data);  // keep the real message row
        delete pending[e.data.external_id];
    }
});
socket.on("ai.response.failed", e => {
    // if data.in_reply_to maps to a pending stream, drop that bubble
});

The final `message.created` is authoritative: replace the streamed text
with it (the server may have trimmed citations like [K1] from it).

## ADMIN SIDE (test sessions)

Admin dashboards receive `ai.response.delta` on the chatbot group they
already join at connect. The flow for a test chat message:

1. POST the test message (TestChatMessageCreateView with stream=True).
2. While it generates, `ai.response.delta` events tagged
   `test-ai:{visitor_message_id}` arrive over the socket; render them with
   the same accumulate/finalize logic as the widget.
3. The response body still contains the final `ai_message`; treat it as
   the authoritative finalize (same shape as `message.created`).

If the dashboard prefers no streaming for test chats, call
`send_test_message(..., stream=False)` and nothing is published.

## NOTES

- Events are best-effort; the persisted message is the source of truth.
- On `ai.response.failed` (including `reply_unavailable`, when the session
  stopped accepting AI replies mid-turn), discard the pending stream.
- Booking acknowledgments (`appointment:{id}` replies) are not streamed;
  they arrive as a single `message.created`.
