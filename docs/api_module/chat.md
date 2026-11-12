# Chat API

All endpoints require authentication. Base path: `/api/v1/chat/`.

Successful responses: `{ "status", "success": true, "message", "data" }`. List responses add `meta` (`count`, `page`, `page_size`, `num_pages`, `next`, `previous`).

Chatting happens on **one websocket per session** that supports two modes, switchable at any time in the same session:

| Mode | Input | Agent model | Output |
| --- | --- | --- | --- |
| `text` (default) | typed messages | chat model (cheap) | streamed text |
| `audio` | microphone audio (and typed messages) | live model (costly) | streamed audio + streamed transcript text |

Both modes share one conversation memory: the agent remembers what was typed when the user starts talking, and vice versa. Both modes emit the **same** `agent.response.*` events, so one renderer handles every reply.

## Sessions

### Create session

`POST /sessions/create/`

Input (JSON):

```json
{ "chatbot": "<chatbot-slug>" }
```

Output `data` (session detail):

```json
{
  "id": "uuid",
  "chatbot": { "slug": "...", "chatbot_name": "...", "tone": "..." },
  "status": "open",
  "messages": [],
  "cart_item_count": 0,
  "purchased_item_count": 0,
  "order_count": 0,
  "escalation_count": 0,
  "last_activity_at": null,
  "created_at": "...",
  "updated_at": "..."
}
```

### List sessions

`GET /sessions/list/?page=<number>&page_size=<number>`

Output `data[]`:

```json
{
  "id": "uuid",
  "chatbot": { "slug": "...", "chatbot_name": "...", "tone": "..." },
  "status": "open",
  "message_count": 12,
  "cart_item_count": 3,
  "purchased_item_count": 2,
  "order_count": 1,
  "escalation_count": 1,
  "last_message": {
    "sender_type": "user",
    "content": "first 120 chars",
    "created_at": "..."
  },
  "last_activity_at": "...",
  "created_at": "..."
}
```

`page_size` defaults to 20 (max 100). `last_message` is `null` when empty.

Activity counts (also on session details):

- `cart_item_count`: product lines the agent added to the cart, whatever happened to them later.
- `purchased_item_count`: product lines that were ordered.
- `order_count`: orders placed.
- `escalation_count`: human escalations raised.

### Session details

`GET /sessions/details/?session=<uuid>`

Output `data`: session detail (same as create) with full `messages[]` timeline.

### Delete session

`DELETE /sessions/delete/?session=<uuid>`

Input: query `session` only. No body. Output: success message only.

### Session cart items

`GET /sessions/cart-items/?session=<uuid>&status=<in_cart|ordered|removed>&page=<number>`

Every product the agent added to the session's cart, oldest first. `status` is optional. Output `data[]`:

```json
{
  "id": "uuid",
  "session": "uuid",
  "product": "uuid",
  "order": "uuid or null",
  "product_name": "Trail Runner 2",
  "sku": "TR-2",
  "product_url": "https://...",
  "image_url": "https://...",
  "unit_price": "89.00",
  "currency": "USD",
  "quantity": 2,
  "line_total": "178.00",
  "status": "ordered",
  "created_at": "...",
  "updated_at": "..."
}
```

`status`: `in_cart` (still in the cart), `ordered` (part of `order`), `removed`. Adding a product that is already in the cart increases `quantity` instead of adding a line. Product details and `unit_price` are snapshots taken when the item was added. `product` is `null` when the product was later deleted.

### Session orders (purchases)

`GET /sessions/orders/?session=<uuid>&page=<number>`

Orders placed in the session, newest first. Output `data[]`:

```json
{
  "id": "uuid",
  "session": "uuid",
  "order_number": "VB-3F9A1C2B7D",
  "status": "confirmed",
  "item_count": 3,
  "total_amount": "197.00",
  "currency": "USD",
  "items": [ { ...cart item } ],
  "created_at": "..."
}
```

`item_count` is the total quantity. Payment is not collected in chat.

### Session escalations

`GET /sessions/escalations/?session=<uuid>&page=<number>`

Human escalations raised in the session, newest first. Each item has the same shape as in `GET /escalations/list/`: `id`, `session`, `message`, `chatbot`, `question`, `reason`, `status`, `resolution_note`, `created_at`.

The three lists return `404` for sessions the user cannot see.

## Messages

### Message object

Returned by the list endpoint, session details, `chat.message.created`, and `agent.response.completed`:

```json
{
  "id": "uuid",
  "sender_type": "agent",
  "mode": "audio",
  "content": "We offer screen repairs for 75.00 USD.",
  "attachments": [
    {
      "id": "uuid",
      "attachment_type": "image",
      "file_url": "...",
      "file_name": "...",
      "mime_type": "...",
      "file_size": 1024,
      "status": "processed",
      "text_preview": "first 200 chars of extracted text",
      "created_at": "..."
    }
  ],
  "recommendations": [ { ...recommendation card } ],
  "response_time_ms": 1234,
  "usage": {
    "total_tokens": 4500,
    "input_tokens": 4000,
    "output_tokens": 500,
    "thinking_tokens": 0,
    "cached_input_tokens": 0,
    "audio_input_tokens": 3000,
    "audio_output_tokens": 400,
    "audio_input_seconds": 14.2,
    "audio_output_seconds": 9.6,
    "cost": 0.015,
    "model": "gemini-live-..."
  },
  "created_at": "..."
}
```

- `sender_type`: `user` | `agent`
- `mode`: `text` (typed / text reply) | `audio` (spoken / voice reply). For a `user` message in `audio` mode, `content` is the transcript of what the user said.
- `recommendations`: product/service cards shown with an agent reply (always `[]` on user messages). Same shape as [Recommendation card](#recommendation-card), so history reloads render the same cards as live replies.
- `attachment_type`: `image` | `document` | `audio` | `video` | `other`; attachment `status`: `pending` | `processed` | `failed`
- `response_time_ms`: set on `agent` messages only. The delay between the user's input and the agent's **first** output, which is what the user waits for. Text mode measures from the user message to the first streamed text chunk. Audio mode measures from the user's last speech caption (or typed message) to the first chunk of agent audio or transcript. The time spent speaking a long reply is not included.
- `usage`: tokens and estimated cost (USD) of generating an `agent` message, and the model that produced it. It is `null` on `user` messages, because the user's input (including audio) is counted in the reply's `input_tokens`. It is also `null` when the model reported no usage. Usage is stored per message in `AIUsage`.
  - Voice replies are billed per modality: text, audio, and image/video each have their own rate. `audio_*_tokens` is the audio share of the input and output tokens. `audio_input_seconds` is the user audio streamed since the previous reply, and `audio_output_seconds` is the agent audio produced.
  - `cost` prices each modality with the `GEMINI_LIVE_*_COST_PER_MILLION` rates. When the model reports no per-modality breakdown, audio is priced by duration with the `GEMINI_LIVE_AUDIO_*_COST_PER_MINUTE` rates.

### List messages

`GET /messages/list/?session=<uuid>&page=<number>&page_size=<number>`

Output `data[]`: message objects, oldest first.

### Send message (attachments)

`POST /messages/send/`

Use this endpoint only to send **files**; send typed text over the socket (`message.send`), which streams the reply to the sender directly.

Input (multipart): `session` (uuid, required), `content` (string, optional, max 8000), `attachments` (files, optional, max 5; images ≤ 5 MB, documents ≤ 10 MB). Content or at least one attachment required.

Output `data`: the created user message. The agent answers in the background, in `text` mode. Its reply streams to every open socket of the session as `agent.response.*` events, followed by `chat.message.created`.

Errors: `409` when the session is in `audio` mode on a socket (switch to `text` first).

## Socket

```text
ws://<host>/ws/chat/sessions/<session_id>/?token=<access_token>
```

Auth: JWT via `?token=<access-token>` or the `Authorization: Bearer <token>` header. Close codes: `4401` unauthenticated, `4403` session not found/forbidden.

On connect the socket is in `text` mode:

```json
{ "type": "connection.ready", "data": { "session_id": "uuid", "mode": "text" } }
```

Mode is per connection. After a reconnect the socket is back in `text` mode: send `mode.set` again to resume voice.

### Client → server

| Frame | Meaning |
| --- | --- |
| `{"type": "ping"}` | Keep-alive; answered with `{"type": "pong"}` |
| `{"type": "mode.set", "mode": "audio"}` | Start voice: opens the live model session |
| `{"type": "mode.set", "mode": "text"}` | Stop voice: closes the live model session (stops the costly model) |
| `{"type": "message.send", "content": "..."}` | Typed message (required, max 8000). Answered in the current mode: text reply in `text`, spoken reply in `audio` |
| binary frame | Microphone audio; `audio` mode only |

Only the session owner can send `mode.set` and `message.send`.

### Server → client

| Event | When |
| --- | --- |
| `mode.changed` | Mode switched (also the reply to a `mode.set` for the current mode) |
| `message.accepted` | A `message.send` was stored |
| `voice.transcript` | Live caption of the user's speech (`audio` mode) |
| `agent.response.started` | The agent began a reply |
| `agent.response.delta` | Next chunk of the reply text |
| binary frame | Next chunk of the agent's voice (`audio` mode) |
| `agent.response.recommendations` | Product/service cards, after the text is complete (only when the reply recommends something) |
| `agent.debug` | Diagnostics of the turn: tool calls, tool results, and recommendation decisions. Only sent when `AGENT_CLIENT_DEBUG` is on (defaults to `DEBUG`). For development; never render to end users |
| `agent.response.completed` | The reply is final and stored |
| `agent.response.failed` | The reply failed; discard the pending bubble |
| `chat.message.created` | Any new message in the session (user or agent, from any tab or REST) |
| `chat.cart.item_added` | The agent added a product to the cart (or increased its quantity) |
| `chat.cart.item_removed` | The agent removed a product from the cart |
| `chat.order.placed` | The agent placed an order for the cart |
| `chat.escalation.created` | The agent escalated the conversation to a human |
| `notification.created` | Notification for a chatbot you own (e.g. training completed) |
| `error` | A request was rejected; see [Errors](#errors) |

Payloads:

```json
{ "type": "mode.changed", "data": { "mode": "audio" } }

{ "type": "message.accepted", "session_id": "uuid", "message_id": "uuid", "mode": "text" }

{ "type": "voice.transcript", "data": { "partial": true, "text": "what are your" } }
{ "type": "voice.transcript", "data": { "partial": false, "text": "What are your opening hours?", "message_id": "uuid" } }

{ "type": "agent.response.started", "data": { "turn_id": "uuid", "mode": "text", "reply_to": "uuid" } }

{ "type": "agent.response.delta", "data": { "turn_id": "uuid", "delta": "We open at ", "sequence": 1 } }

{ "type": "agent.response.recommendations", "data": { "turn_id": "uuid", "items": [ { ...recommendation card } ] } }

{
  "type": "agent.response.completed",
  "data": {
    "turn_id": "uuid",
    "mode": "text",
    "message": { ...message object },
    "sources": [ { "id": "uuid", "space": "document", "name": "Returns policy", "url": "...", "ref": "K1", ... } ],
    "interrupted": false
  }
}

{ "type": "agent.response.failed", "data": { "turn_id": "uuid", "message": "The assistant could not answer right now. Please try again." } }

{ "type": "chat.message.created", "session_id": "uuid", "message": { ...message object } }

{ "type": "notification.created", "data": { ...notification } }

{
  "type": "chat.cart.item_added",
  "data": {
    "session_id": "uuid",
    "item": { ...cart item },
    "cart": { "items": [ { ...cart item } ], "item_count": 3, "total": "197.00", "currency": "USD" },
    "counts": { "cart_item_count": 2, "purchased_item_count": 0, "order_count": 0, "escalation_count": 0 }
  }
}

{ "type": "chat.cart.item_removed", "data": { "session_id": "uuid", "item": { ...cart item }, "cart": { ... }, "counts": { ... } } }

{ "type": "chat.order.placed", "data": { "session_id": "uuid", "order": { ...order with items }, "counts": { ... } } }

{ "type": "chat.escalation.created", "data": { "session_id": "uuid", "escalation": { ...escalation }, "counts": { ... } } }

{ "type": "error", "message": "...", "code": "busy" }
```

Field notes:

- `turn_id` groups every event of one reply. Text replies use the user message's id (`reply_to` is the same id); voice replies get a fresh id (`reply_to` is `null`).
- `delta`: append chunks in `sequence` order (1, 2, 3, …). Citation markers such as `[K1]` are removed before streaming. The `content` in `completed.message` is the final, cleaned text, so replace the streamed text with it.
- `voice.transcript`: while `partial` is `true`, `text` is the **next chunk** of the utterance; append it. The final event (`partial: false`) carries the **whole** utterance; replace the caption with it. Its `message_id` is the stored user message.
- `completed.message` is `null` only when a voice reply was interrupted before anything was said.
- `interrupted: true` (audio): the user talked over the agent and the agent stopped. **Flush the audio playback queue.** The stored message holds what had been said.
- `sources`: the documents/products/services cited by a text reply (empty in audio mode).
- `chat.cart.*`, `chat.order.placed`, `chat.escalation.created` arrive **while** the agent is replying, as soon as the tool succeeds, in text and audio mode alike. Use them for toasts. `counts` holds the session's updated activity counts, and `cart` (cart events) holds the whole current cart, so you never need to compute either. An escalation for a question that is already open sends no second event.

### Recommendation card

Sent when the agent recommends products or services: the 2-3 items the agent chose (best first), in text and voice mode alike, after the reply text. Every key is always present:

```json
{
  "type": "product",
  "id": "uuid",
  "name": "Trail Runner 2",
  "description": "Lightweight trail shoe with ...",
  "price": "89.00",
  "currency": "USD",
  "url": "https://shop.example.com/trail-runner-2",
  "image_url": "https://shop.example.com/images/trail-runner-2.jpg",
  "sku": "TR-2",
  "category": "Shoes",
  "service_type": ""
}
```

- `type`: `product` | `service`. `id` is the product/service id.
- `price`: decimal string, or `null` when unknown (for services this is the service cost). Show it with `currency` and never recompute it.
- `url`, `image_url`, `sku`, `category`: products only (`""` for services). `service_type`: services only (`""` for products).
- A chatbot without trained products or services never sends cards: the agent does not get the product/service tools or instructions.
- Cart and order events are only sent by chatbots with `product_ordering_enabled`; other chatbots never get the cart tools.

### Text mode flow

```text
client                                   server
  message.send {content}          ->
                                  <-     message.accepted
                                  <-     chat.message.created   (user message, also to other tabs)
                                  <-     agent.response.started
                                  <-     agent.response.delta × n
                                  <-     agent.response.recommendations   (optional)
                                  <-     agent.response.completed
                                  <-     chat.message.created   (agent message)
```

Send one message at a time. While a reply is in progress, `message.send` returns `error` with code `busy`.

### Audio mode flow

```text
client                                   server
  mode.set {mode: "audio"}        ->
                                  <-     mode.changed {mode: "audio"}
  binary PCM frames (continuous)  ->
                                  <-     voice.transcript (partial) × n
                                  <-     voice.transcript (final, message_id)
                                  <-     chat.message.created   (user message)
                                  <-     agent.response.started
                                  <-     binary PCM frames × n            (agent voice)
                                  <-     agent.response.delta × n         (what the agent says)
                                  <-     agent.response.recommendations  (optional)
                                  <-     agent.response.completed
                                  <-     chat.message.created   (agent message)
  ... keep streaming the mic; the next turn starts when the user speaks ...
  mode.set {mode: "text"}         ->
                                  <-     mode.changed {mode: "text"}
```

- Turn-taking is automatic: the server detects when the user starts and stops speaking. Keep the microphone streaming during the whole audio session, including while the agent talks, so the user can interrupt it.
- Agent audio and its transcript (`delta`) arrive interleaved. Render the text as it comes and play the audio from a queue.
- `message.send` during audio mode is answered by voice and stored with `mode: "audio"`.
- If the voice session fails, the server sends `error` (`code: "voice_failed"`) followed by `mode.changed` to `text`. Show a notice and continue in text, or send `mode.set` `audio` to retry.
- Switch back to `text` when the user leaves voice. The live model is billed while audio mode is on.

### Audio format

Both directions use raw PCM: mono, 16-bit signed little-endian, **16 kHz** upstream. Agent audio downstream is 16-bit mono PCM at **24 kHz** (the live model's output rate).

Send microphone chunks of ~20–100 ms as binary frames:

```ts
const ctx = new AudioContext({ sampleRate: 16000 });
const mic = await navigator.mediaDevices.getUserMedia({ audio: true });
await ctx.audioWorklet.addModule("pcm16-worklet.js");     // posts Int16Array chunks
const worklet = new AudioWorkletNode(ctx, "pcm16-worklet");
ctx.createMediaStreamSource(mic).connect(worklet);
worklet.port.onmessage = (e) => ws.send((e.data as Int16Array).buffer);
```

### Errors

`{"type": "error", "message": "...", "code"?: "..."}`

| `code` | Cause |
| --- | --- |
| `busy` | A reply is still in progress, or voice mode is active for this session on another connection |
| `voice_unavailable` | Voice mode could not start; the socket stays in `text` |
| `voice_failed` | The running voice session ended; followed by `mode.changed` → `text` |
| (none) | Invalid request, e.g. empty/too long message, unknown event, invalid `mode`, audio frame in `text` mode, closed session, deactivated chatbot |

### Deduplication and history

- `chat.message.created` is the history event for every message, from every tab and from REST. Key messages by `id`. An agent message arrives in both `agent.response.completed` and `chat.message.created`, so upsert it by `id` and keep one copy.
- Socket events are not replayed. After reconnecting, reload history with `GET /messages/list/?session=<uuid>`. Stored agent messages include their `recommendations`.

### Minimal client

```ts
ws.binaryType = "arraybuffer";
const replies = new Map<string, { text: string; seq: number }>();

ws.onmessage = (ev) => {
  if (typeof ev.data !== "string") return enqueuePcm(ev.data);   // agent voice
  const msg = JSON.parse(ev.data);
  switch (msg.type) {
    case "mode.changed":       setMode(msg.data.mode); msg.data.mode === "audio" ? startMic() : stopMic(); break;
    case "voice.transcript":   renderUserCaption(msg.data.text, msg.data.partial); break;
    case "agent.response.started":
      replies.set(msg.data.turn_id, { text: "", seq: 0 }); break;
    case "agent.response.delta": {
      const r = replies.get(msg.data.turn_id);
      if (r && msg.data.sequence === r.seq + 1) { r.text += msg.data.delta; r.seq++; renderBubble(msg.data.turn_id, r.text); }
      break;
    }
    case "agent.response.recommendations": renderCards(msg.data.turn_id, msg.data.items); break;
    case "agent.response.completed":
      if (msg.data.interrupted) flushPlayback();
      if (msg.data.message) upsertMessage(msg.data.message);  // final text + cards
      replies.delete(msg.data.turn_id); break;
    case "agent.response.failed": dropBubble(msg.data.turn_id); break;
    case "chat.message.created": upsertMessage(msg.message); break;
    case "error": showError(msg.message, msg.code); break;
  }
};

const sendText = (content: string) => ws.send(JSON.stringify({ type: "message.send", content }));
const startVoice = () => ws.send(JSON.stringify({ type: "mode.set", mode: "audio" }));
const stopVoice = () => ws.send(JSON.stringify({ type: "mode.set", mode: "text" }));
```

### Debug events (`agent.debug`)

When `AGENT_CLIENT_DEBUG` is on, each agent turn also emits diagnostics with the same `turn_id` as its `agent.response.*` events. They are written to the server log too.

```json
{ "type": "agent.debug", "data": { "turn_id": "uuid", "mode": "audio", "stage": "tool_result",
  "tool": "show_recommendations", "status": "ok",
  "items": [ { "id": "uuid", "name": "Relaxed Fit Linen Shirt", "type": "product" } ] } }
```

| `stage` | Meaning |
| --- | --- |
| `tool_call` | The agent called `tool` with `args` |
| `tool_result` | `tool` returned `status`; search tools list `results` (`ref`, `name`, `space`); `show_recommendations` lists `items` |
| `recommendations_pending` | Voice: cards were chosen during a tool round and are held for the spoken reply |
| `recommendations_dropped` | Voice: the user interrupted before the reply was spoken (`reason`, `items`) |
| `recommendations_sent` | The cards (`items`) are about to be sent as `agent.response.recommendations` |
| `no_recommendations` | The reply finished without cards (text mode also lists `selected_tools`) |
