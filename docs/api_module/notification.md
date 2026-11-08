# Notification API

Base path: `/api/v1/notifications/`

All REST endpoints require an authenticated bearer token.

## Data model

`recipient_type` answers who receives the notification:

| Value | Address field | Availability |
| --- | --- | --- |
| `global` | none | Available now; visible to every authenticated user |
| `user` | `recipient` (internal user FK) | Available now; visible only to that user |
| `workspace` | `workspace_id` | Visible to active workspace members |
| `chatbot` | `chatbot_id` | Visible to users assigned to the chatbot |
| `chat_session` | `target_id` | Storage and realtime group ready; membership lookup is pending the chat-session model |

`notification_type` answers what happened:

- `general`
- `update`
- `maintenance`
- `notify`
- `new_message`
- `session_started`
- `ai_notification`
- `training_complete`

Global notifications normally use `update`, `maintenance`, or `notify`.
Chat-session notifications normally use `new_message`, `session_started`,
or `ai_notification`. These pairings are conventions rather
than database constraints, so other valid event types are not rejected.

Workspace and chatbot recipients are foreign keys. Only chat-session references
remain UUIDs until that domain model exists.

## List notifications

```http
GET /api/v1/notifications/
Authorization: Bearer <access_token>
```

This returns global, direct-user, workspace, and chatbot notifications visible
to the authenticated user. Chat-session visibility will be connected when that
domain model exists.

Query parameters:

| Param | Description |
| --- | --- |
| `page` | Page number |
| `page_size` | Page size, up to the configured pagination maximum |
| `recipient_type` | Filter by `global` or `user` |
| `notification_type` | Filter by a notification event type |
| `type` | Backward-compatible alias for `notification_type` |
| `workspace_id` | Restrict results to an accessible workspace |
| `chatbot_id` | Restrict results to an assigned chatbot |
| `unread_only` | `true`, `1`, or `yes` returns only unread items |

Example:

```http
GET /api/v1/notifications/?recipient_type=global&notification_type=maintenance&unread_only=true
```

Response:

```json
{
  "status": 200,
  "success": true,
  "message": "Notifications fetched successfully.",
  "meta": {
    "count": 1,
    "page": 1,
    "page_size": 20,
    "num_pages": 1,
    "next": null,
    "previous": null,
    "unread_count": 1
  },
  "data": [
    {
      "id": "3af8f742-4ac4-4f65-912f-2c0c238d2928",
      "recipient_type": "global",
      "notification_type": "maintenance",
      "title": "Scheduled maintenance",
      "message": "The service will be unavailable for 10 minutes.",
      "metadata": {},
      "workspace_id": null,
      "chatbot_id": null,
      "target_id": null,
      "is_read": false,
      "read_at": null,
      "created_at": "2026-08-18T13:42:00Z"
    }
  ]
}
```

`meta.unread_count` follows the `recipient_type` and notification-type
filters. The `unread_only` flag does not change the count.

## Mark one notification read

```http
PATCH /api/v1/notifications/<notification_id>/read/
```

The notification must be visible to the authenticated user.

## Mark all notifications read

```http
PATCH /api/v1/notifications/read-all/
```

This accepts the same filters as the list endpoint.

## Realtime WebSocket

```text
ws://<host>/ws/notifications/?token=<access_token>
```

An authenticated connection subscribes to:

- `notifications.global`
- `notifications.user.<user_id>`
- `notifications.workspace.<workspace_id>` for active memberships
- `notifications.chatbot.<chatbot_id>` for active assignments

Events use the serialized REST notification shape with an additional top-level
`event` field that mirrors `notification_type`. Chat-session consumers will use
`notifications.chat_session.<chat_session_id>`.

Successful knowledge training emits a chatbot notification with
`event: training_complete` and `notification_type: training_complete`. Its
metadata includes `knowledge_base_id`, `training_log_id`, `source_type`,
`training_stage`, and `force_retrain`.

## Creating notifications in application code

```python
from notification.models import NotificationType
from notification.services import (
    create_chat_session_notification,
    create_global_notification,
    create_user_notification,
)

create_global_notification(
    notification_type=NotificationType.MAINTENANCE,
    title="Scheduled maintenance",
)

create_user_notification(
    recipient=user,
    title="Welcome",
)

create_chat_session_notification(
    chat_session_id=session_id,
    notification_type=NotificationType.NEW_MESSAGE,
    title="New message",
)
```

`create_workspace_notification` and `create_chatbot_notification` follow the
same pattern and accept `workspace` and `chatbot` model instances.

## Demo command

```bash
python manage.py create_demo_notification \
  --recipient-type global \
  --notification-type maintenance \
  --title "Scheduled maintenance"
```

For a user notification, pass `--recipient-type user --recipient-id <uuid>`.
For workspace, chatbot, or chat-session notifications, pass `--target-id <uuid>`.
