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

### Session details

`GET /sessions/details/?session=<uuid>`

Output `data`: session detail (same as create) with full `messages[]` timeline.

### Delete session

`DELETE /sessions/delete/?session=<uuid>`

Input: query `session` only. No body. Output: success message only.

## Messages

### Message object

Returned by the list endpoint, `chat.message.created`, and `agent.response.completed`:

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
  "created_at": "..."
}
```

- `sender_type`: `user` | `agent`
- `mode`: `text` (typed / text reply) | `audio` (spoken / voice reply). For a `user` message in `audio` mode, `content` is the transcript of what the user said.
- `recommendations`: product/service cards shown with an agent reply (always `[]` on user messages). Same shape as [Recommendation card](#recommendation-card), so history reloads render the same cards as live replies.
- `attachment_type`: `image` | `document` | `audio` | `video` | `other`; attachment `status`: `pending` | `processed` | `failed`
- `response_time_ms`: set on `agent` messages only

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
| `agent.response.completed` | The reply is final and stored |
| `agent.response.failed` | The reply failed; discard the pending bubble |
| `chat.message.created` | Any new message in the session (user or agent, from any tab or REST) |
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

{ "type": "error", "message": "...", "code": "busy" }
```

Field notes:

- `turn_id` groups every event of one reply. Text replies use the user message's id (`reply_to` is the same id); voice replies get a fresh id (`reply_to` is `null`).
- `delta`: append chunks in `sequence` order (1, 2, 3, …). Citation markers such as `[K1]` are removed before streaming. The `content` in `completed.message` is the final, cleaned text, so replace the streamed text with it.
- `voice.transcript`: while `partial` is `true`, `text` is the **next chunk** of the utterance; append it. The final event (`partial: false`) carries the **whole** utterance; replace the caption with it. Its `message_id` is the stored user message.
- `completed.message` is `null` only when a voice reply was interrupted before anything was said.
- `interrupted: true` (audio): the user talked over the agent and the agent stopped. **Flush the audio playback queue.** The stored message holds what had been said.
- `sources`: the documents/products/services cited by a text reply (empty in audio mode).

### Recommendation card

Sent when the agent recommends products or services. Text replies get a card for every product/service they cite. Voice replies get the items the agent chose to display. Every key is always present:

```json
{
  "type": "product",
  "id": "uuid",
  "name": "Trail Runner 2",
  "description": "Lightweight trail shoe with ...",
  "price": "89.00",
  "currency": "USD",
  "url": "https://shop.example.com/trail-runner-2",
  "sku": "TR-2",
  "category": "Shoes",
  "service_type": ""
}
```

- `type`: `product` | `service`. `id` is the product/service id.
- `price`: decimal string, or `null` when unknown (for services this is the service cost). Show it with `currency` and never recompute it.
- `url`, `sku`, `category`: products only (`""` for services). `service_type`: services only (`""` for products).
- A chatbot without trained products or services never sends cards: the agent does not get the product/service tools or instructions.

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
