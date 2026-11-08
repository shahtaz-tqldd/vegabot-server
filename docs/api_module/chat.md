# Chat API

All endpoints require authentication. Base path: `/api/v1/chat/`.

Successful responses: `{ "status", "success": true, "message", "data" }`. List responses add `meta` (`count`, `page`, `page_size`, `num_pages`, `next`, `previous`).

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

### List messages

`GET /messages/list/?session=<uuid>&page=<number>&page_size=<number>`

Output `data[]`:

```json
{
  "id": "uuid",
  "sender_type": "user",
  "content": "...",
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
  "response_time_ms": 1234,
  "created_at": "..."
}
```

Enums: `sender_type` = `user` | `agent`; `attachment_type` = `image` | `document` | `audio` | `video` | `other`; `status` = `pending` | `processed` | `failed`. `response_time_ms` set on `agent` messages only.

### Send message

`POST /messages/send/`

Input (multipart): `session` (uuid, required), `content` (string, optional, max 8000), `attachments` (files, optional, max 5; images ≤ 5 MB, documents ≤ 10 MB). Content or at least one attachment required.

Output `data`: created user message (same shape as list items). Agent reply arrives later via socket event `chat.message.created`.

## Sockets

Auth: JWT via `Authorization: Bearer <token>` header or `?token=<access-token>` query param.

Close codes: `4401` unauthenticated, `4403` session not found/forbidden.

### Session channel

`WS /ws/chat/sessions/<session_id>/`

On connect, server sends:

```json
{ "type": "connection.ready", "data": { "session_id": "uuid" } }
```

Client → server:

```json
{ "type": "ping" }
{ "type": "message.send", "content": "text, required, max 8000" }
```

Server → client:

```json
{ "type": "pong" }
{ "type": "message.accepted", "session_id": "uuid", "message_id": "uuid" }
{ "type": "error", "message": "..." }
{ "type": "chat.message.created", "session_id": "uuid", "message": { ...message object } }
{ "type": "notification.created", "data": { ...notification } }
```

`chat.message.created` fires for every new message in the session (both `user` and `agent` senders). Use it to render agent replies and messages sent from other tabs. `notification.created` arrives only for owned chatbots (e.g. training completion).

### Live voice channel

`WS /ws/chat/sessions/<session_id>/live/`

Same auth, connect events, and `message.send` support as above. Voice protocol:

Client → server: JSON `{"type": "live.start"}`, `{"type": "live.stop"}`, `{"type": "live.interrupt"}`; binary frames = user audio chunks (send only after `live.started`).

Server → client:

```json
{ "type": "live.started", "data": { "session_id": "uuid" } }
{ "type": "live.stopped" }
{ "type": "live.transcript", ... }
{ "type": "live.error", "message": "..." }
```

Binary frames = agent audio; `live.audio` events also possible.
