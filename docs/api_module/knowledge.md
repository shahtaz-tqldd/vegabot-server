# Knowledge base API

All endpoints require authentication. Upload, list, and training-log requests
identify the chatbot with its slug. Detail, update, and delete requests identify
the knowledge source with its UUID.

## Endpoints

- POST /api/v1/knowledge/upload/?chatbot=<slug>&type=file
  accepts multipart file and optional title.
- POST /api/v1/knowledge/upload/?chatbot=<slug>&type=url
  accepts JSON url and optional title.
- POST /api/v1/knowledge/upload/?chatbot=<slug>&type=custom
  accepts JSON content and optional title.
- GET /api/v1/knowledge/list/?chatbot=<slug> returns a paginated
  source list.
- POST /api/v1/knowledge/search/ accepts JSON `chatbotSlug` and `query`, then
  returns up to 10 ranked retrieval chunks from enabled sources. It performs
  retrieval only and does not invoke an LLM.
- GET /api/v1/knowledge/usage/?chatbot=<slug> returns the chatbot's
  stored chunk and file-size totals with the current static limits.
- GET /api/v1/knowledge/details/?knowledge_base_id=<uuid> returns
  one source.
- PATCH /api/v1/knowledge/update/?knowledge_base_id=<uuid>&type=file
  retries training when the file's previous training failed.
- PATCH /api/v1/knowledge/update/?knowledge_base_id=<uuid>&type=url
  retrains the URL.
- PATCH /api/v1/knowledge/update/?knowledge_base_id=<uuid>&type=custom
  accepts replacement content and retrains it. The training pipeline replaces
  the previous vectors atomically after the new vectors are ready.
- DELETE /api/v1/knowledge/delete/?knowledge_base_id=<uuid> removes
  the source, its vectors, logs, and any stored file.
- GET /api/v1/knowledge/training-logs/?chatbot=<slug> returns all
  training logs for the chatbot as a paginated list.

Paginated endpoints accept page and page_size. Uploads and updates that start
training return the source and the queued training log. File responses expose a
short-lived private S3 URL; the private object key is never returned.

When training completes successfully, the backend persists a chatbot-scoped
notification and broadcasts it through `/ws/notifications/` with
`event: training_complete` and `notification_type: training_complete`.
Unchanged sources that retain their existing vectors also emit this successful
completion event with `metadata.training_stage` set to `skipped`.
