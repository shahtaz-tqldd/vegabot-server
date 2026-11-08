# Live Chat API (voice)

Realtime voice conversation over one chat session. Text chat and voice share the same socket.

Prerequisite: create a session first — `POST /api/v1/chat/sessions/create/` with `{"chatbot": "<slug>"}`.

## 1. Connect

```text
ws://<host>/ws/chat/sessions/<session_id>/live/?token=<access_token>
```

JWT via `?token=` or `Authorization: Bearer` header. Close codes: `4401` unauthenticated, `4403` session not found/forbidden.

On connect the server sends:

```json
{ "type": "connection.ready", "data": { "session_id": "uuid" } }
```

## 2. Start / stop the live session

Client → server:

```json
{ "type": "live.start" }
{ "type": "live.stop" }
{ "type": "live.interrupt" }
```

Server → client:

```json
{ "type": "live.started", "data": { "session_id": "uuid" } }
{ "type": "live.stopped" }
{ "type": "live.error", "message": "..." }
{ "type": "live.not_available", "message": "..." }
```

Rules:

- Send `live.start` once; a second `live.start` returns `live.error "Live session already started."`
- Stream audio only after `live.started`; audio before it returns `{"type": "error", "message": "Start the live session before sending audio."}`
- `live.stop` ends the voice session; the socket stays open for text chat
- `live.interrupt` when the user speaks again while the agent is talking (barge-in); it cuts the agent's current turn
- Disconnecting the socket closes the live session automatically
- `live.not_available`: live voice is disabled on this deployment

## 3. Stream audio up (user mic)

Binary frames, raw PCM: mono, 16-bit little-endian, 16 kHz (`audio/pcm;rate=16000`).

Capture and downsample with the Web Audio API and send chunks as binary websocket frames every ~100 ms:

```ts
const ctx = new AudioContext({ sampleRate: 16000 });
const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
const source = ctx.createMediaStreamSource(stream);
const processor = ctx.createAudioWorklet(module, "pcm16-worker");  // or ScriptProcessorNode
// worklet posts Int16Array chunks:
ws.send(int16Chunk.buffer);  // binary frame
```

## 4. Stream audio down (agent voice)

The server sends agent speech as binary websocket frames — same PCM format (16 kHz, mono, 16-bit).

Maintain a play queue: append incoming `ArrayBuffer`s to a buffer list and schedule playback (AudioWorklet ring buffer or queued AudioBufferSourceNodes). Keep the queue drained on `live.stopped`.

## 5. Transcripts

```json
{
  "type": "live.transcript",
  "partial": true,
  "channel": "user",
  "text": "what are your opening"
}
```

- `channel`: `user` (speech-to-text of the mic) or `agent` (what the agent speaks)
- `partial: true`: interim caption — replace the previous partial for that channel
- `partial: false`: final transcript

Finals are persisted as chat messages and broadcast to the session group:

```json
{ "type": "chat.message.created", "session_id": "uuid", "message": { "id": "uuid", "sender_type": "user|agent", "content": "...", "attachments": [], "response_time_ms": null, "created_at": "..." } }
```

Treat `chat.message.created` as the canonical history event; use `live.transcript` only for live captions. Dedupe: when a `chat.message.created` arrives, drop the pending final caption for that channel.

## 6. Text chat on the same socket

Voice and text share one socket:

```json
{ "type": "ping" }                -> { "type": "pong" }
{ "type": "message.send", "content": "text (required, max 8000)" }
                                  -> { "type": "message.accepted", "session_id": "uuid", "message_id": "uuid" }
                                  -> { "type": "chat.message.created", ... }  // user echo + agent reply
{ "type": "notification.created", "data": { ... } }   // owned chatbots only
{ "type": "error", "message": "..." }
```

## 7. Suggested flow

1. `POST /api/v1/chat/sessions/create/` → `session_id`
2. Open the live socket, wait for `connection.ready`
3. Send `live.start`, wait for `live.started` (handle `live.error` / `live.not_available` — offer text-only fallback)
4. Start mic capture → binary frames up; play binary frames down; render transcript captions
5. On barge-in (mic input while agent audio playing): send `live.interrupt`, flush the playback queue
6. End call: send `live.stop`, stop mic, drain playback, wait `live.stopped`
7. Reconnect/refetch: live state is not resumed — reload history via `GET /api/v1/chat/messages/list/?session=<uuid>` and `live.start` again if needed

## 8. Minimal skeleton

```ts
async function startLive(ws: WebSocket, sessionId: string) {
  ws.binaryType = "arraybuffer";
  ws.onmessage = (ev) => {
    if (typeof ev.data === "string") {
      const msg = JSON.parse(ev.data);
      switch (msg.type) {
        case "connection.ready": ws.send(JSON.stringify({ type: "live.start" })); break;
        case "live.started": startMic(ws); break;
        case "live.transcript": renderCaption(msg.channel, msg.text, msg.partial); break;
        case "chat.message.created": commitMessage(msg.message); break;
        case "live.stopped": stopMic(); drainPlayback(); break;
        case "live.error":
        case "live.not_available": showError(msg.message); stopMic(); break;
      }
    } else {
      enqueuePcm(ev.data);  // agent audio
    }
  };
  ws.onclose = (ev) => { if (ev.code === 4401) reauth(); };
  connect(ws, sessionId);
}

function interrupt(ws: WebSocket) { ws.send(JSON.stringify({ type: "live.interrupt" })); }
function stopLive(ws: WebSocket) { ws.send(JSON.stringify({ type: "live.stop" })); }
```
