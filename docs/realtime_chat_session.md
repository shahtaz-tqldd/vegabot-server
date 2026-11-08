# Frontend: real-time chat-session management

This contract covers takeover, assignment, and transfer changes in the
authenticated dashboard. A successful backend transaction broadcasts the
change to every connected chatbot member who has chat-session access. The
actor does not need to update other users manually.

REST remains the source of truth. Socket events are transient, so refetch the
session list after reconnecting.

## 1. Connect once per browser tab

```text
wss://{api-host}/ws/dashboard/?token={access-token}
```

Wait for `connection.ready`, then send `presence.heartbeat` at the interval in
that event. Chatbot-wide events are subscribed automatically from the signed-in
user's active chatbot memberships. Do not send `session.subscribe` to receive
session-management changes; that command is only needed for notifications
addressed specifically to one session.

## 2. Handle the canonical event

Use `session.updated` for session-list and ownership UI updates:

```json
{
  "type": "session.updated",
  "session_id": "8f205f23-4a10-4b84-a444-bc80a22f4f9a",
  "data": {
    "change": "session.transfer_requested",
    "status": "open",
    "ai_enabled": false,
    "assigned_to": {
      "id": "b4fb8489-6592-4851-b308-05e4fd37bbf1",
      "name": "Current Agent"
    },
    "has_pending_transfer": true,
    "transfer_requested_to": {
      "id": "6e3f95e6-0dac-4d86-a42c-45f513e61d88",
      "name": "Next Agent"
    },
    "transfer_id": "b569d7cb-ef48-40ba-a30a-afef03768e35",
    "transfer_status": "pending",
    "takeover_id": "d8b836f6-dff2-4ab8-babe-d9227731ca11",
    "from_agent_id": "b4fb8489-6592-4851-b308-05e4fd37bbf1",
    "to_agent_id": "6e3f95e6-0dac-4d86-a42c-45f513e61d88"
  }
}
```

The stable state fields are:

| Field | Type | Frontend use |
| --- | --- | --- |
| `session_id` | UUID | Find the session to patch |
| `data.change` | string | Identify the transition |
| `data.status` | `open`, `resolved`, or `closed` | Update/move status-filtered rows |
| `data.ai_enabled` | boolean | Update the AI/human mode |
| `data.assigned_to` | `{id, name}` or `null` | Current owner; `null` means unassigned |
| `data.has_pending_transfer` | boolean | Show or hide pending-transfer state |
| `data.transfer_requested_to` | `{id, name}` or `null` | Pending recipient shown in the inbox |

Transition-specific IDs and fields are additive. Frontend state should not
depend on them when the stable fields above already express the current state.

## 3. Changes and expected state

| `data.change` | Meaning | Expected management state |
| --- | --- | --- |
| `session.taken_over` | An agent took or force-took the session | `assigned_to` is the new agent; no pending transfer |
| `session.transfer_requested` | A transfer awaits a recipient decision | Owner is unchanged; `has_pending_transfer` is `true` |
| `session.transferred` | The recipient accepted | `assigned_to` is the recipient; pending transfer is cleared |
| `session.transfer_declined` | The recipient declined | Owner is unchanged; pending transfer is cleared |
| `session.transfer_cancelled` | The requester cancelled, or a forced takeover superseded it | Pending transfer is cleared; use `assigned_to` from the event |
| `session.released` | The owner released the session | `assigned_to` is `null` |
| `session.resolved` | The owner resolved the issue, or another member force-resolved an attention-required session | `assigned_to` is `null`; the session remains `open` |
| `session.closed` | The owner ended the session | `assigned_to` is `null`; update `status` to `closed` |

For a forced takeover, `session.taken_over` also contains `is_forced: true` and
`takeover_reason`. If it supersedes a pending transfer, a
`session.transfer_cancelled` event is emitted as well. Applying each event as
an idempotent patch leaves the same final state. A force-resolve of an
attention-required session emits `session.resolved` with `is_forced: true` and
the optional `note`.

## 4. Suggested reducer

```ts
type AgentSummary = { id: string; name: string };

type SessionManagementUpdate = {
  type: "session.updated";
  session_id: string;
  data: {
    change: string;
    status: "open" | "resolved" | "closed";
    ai_enabled: boolean;
    assigned_to: AgentSummary | null;
    has_pending_transfer: boolean;
    transfer_requested_to: AgentSummary | null;
  } & Record<string, unknown>;
};

function applySessionUpdate(event: SessionManagementUpdate) {
  sessions.update(event.session_id, (session) => ({
    ...session,
    status: event.data.status,
    ai_enabled: event.data.ai_enabled,
    assigned_to: event.data.assigned_to,
    transfer_requested_to: event.data.transfer_requested_to,
  }));
}
```

If the current list is filtered by `assignment=mine`, compare
`assigned_to.id` with the current chatbot-membership ID and insert/remove the
row as appropriate. For status or assignment filters where the client does not
have enough data to construct a newly eligible row, refetch the current list.
The REST list also accepts `assigned_to=<email>` for current assignments and
live incoming transfer requests, plus `search=<text>` for message content and
visitor name/email.

## 5. Avoid duplicate handling

For backward compatibility, the socket also sends the specific event named in
`data.change` (for example, `session.transferred`) with the same state fields.
Use either those specific events or `session.updated`, not both. New frontend
code should use only `session.updated`.

## 6. Reconnect and error behavior

- Refresh the REST session list after every successful reconnect; events missed
  while disconnected are not replayed.
- Treat duplicate events as harmless patches keyed by `session_id`.
- Keep the optimistic update for the actor only until its REST request fails or
  the socket/REST response provides the authoritative state.
- Membership or permission revocation stops delivery after the server's access
  recheck.
