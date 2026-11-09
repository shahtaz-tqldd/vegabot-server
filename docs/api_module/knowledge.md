# Knowledge API

All endpoints require authentication. Base path: `/api/v1/knowledge/`.

Knowledge is organized in three spaces plus a cross-space search:

- **Base knowledge** (`base/`): free-form sources — URLs, raw text, or
  files (PDF, DOCX, XLSX, CSV, JSON, XML).
- **Products** (`products/`): the chatbot's product catalog, ingested
  from a downloadable CSV/XLSX template.
- **Services** (`services/`): the chatbot's service catalog, added
  one-by-one, in bulk, or imported from a downloadable CSV/XLSX template.
- **Search** (`search/`): hybrid vector + full-text retrieval across the
  spaces, optionally scoped to one source.

Every source of training work is recorded in training logs
(`logs/list/`). Successful runs persist a chatbot notification and
broadcast it through `/ws/notifications/` with
`event: training_complete`.

## Base knowledge

### Upload a file

`POST /base/upload-file/` (multipart)

Provide `chatbot` (slug) and `file`; optional `title`. Allowed file
types: `pdf`, `docx`, `xlsx`, `csv`, `json`, `xml` (size and page/row
limits apply). The file is stored privately and queued for training.
Returns the created source in `data`.

### Add a URL

`POST /base/upload-url/` (JSON)

Provide `chatbot` (slug) and `url`; optional `title`. The page is
scraped and trained. Private, loopback, and non-HTTP(S) URLs are
rejected. Returns the created source in `data`.

### Add text

`POST /base/upload-text/` (JSON)

Provide `chatbot` (slug) and `content` (non-empty, up to 200,000
characters); optional `title`. The content is chunked and trained
directly. Returns the created source in `data`.

### List sources

`GET /base/list/?chatbot=<slug>&page=<number>&page_size=<number>`

Optional filters: `status` (`pending`, `processing`, `ready`,
`failed`), `source_type` (`file`, `website`, `text`). Returns paginated
sources in `data` and pagination fields in `meta`.

### Retrain a URL

`POST /base/retrain/?chatbot=<slug>&document=<uuid>` (JSON)

Retrain is available for URL sources only — files and text are
immutable; replace them by deleting and re-adding. Optional body:
`force: true` to re-embed even when content is unchanged. Returns the
queued training log in `data` with status 202. Unchanged sources keep
their existing vectors and complete with `training_stage: skipped`.

### Delete a source

`DELETE /base/delete/?chatbot=<slug>&document=<uuid>`

Removes the source, its vectors, logs, and any stored file.

## Products

### Download the template

`GET /products/template/`

Returns a CSV with the expected columns (`name`, `sku`, `category`,
`description`, `price`, `currency`, `product_url`, `image_url`) and
sample rows.

### Import a template

`POST /products/import/` (multipart)

Provide `chatbot` (slug) and `file` (the filled template as CSV or
XLSX; up to 5,000 rows). Valid rows are created and queued for
training; rows with duplicate SKUs or invalid values are skipped and
reported. Returns `created_count` and per-row `errors` in `data`.

### List products

`GET /products/list/?chatbot=<slug>&page=<number>&page_size=<number>`

Optional filters: `search` (matches name, SKU, category), `status`.
Returns paginated products in `data` and pagination fields in `meta`.

### Update a product

`PATCH /products/update/?chatbot=<slug>&product=<uuid>` (JSON)

Send changed product fields (`name`, `sku`, `category`, `description`,
`price`, `currency`, `product_url`, `image_url`). The product is marked
pending and a retrain is queued automatically. Returns
`{ product, training_log }` in `data`.

### Disable products

`POST /products/disable/` (JSON)

Provide `chatbot` (slug) and `products`: a list of 1–200 product
UUIDs. Disabled products are immediately removed from search. Returns
`disabled_count` and the disabled products in `data`.

## Services

### Download the template

`GET /services/template/`

Returns a CSV with the expected columns (`name`, `service_type`,
`description`, `cost`, `currency`) and sample rows.

### Import a template

`POST /services/import/` (multipart)

Provide `chatbot` (slug) and `file` (the filled template as CSV or
XLSX; up to 5,000 rows). Valid rows are created and queued for
training; invalid rows are skipped and reported. Returns
`created_count` and per-row `errors` in `data`.

### Add services

`POST /services/create/` (JSON) — one service: provide `chatbot`
(slug) plus `name` (required), `service_type`, `description`, `cost`,
`currency` (defaults to `USD`).

`POST /services/bulk-create/` (JSON) — multiple services: provide
`chatbot` (slug) and `services`: a list of 1–200 service objects with
the same fields.

Creation queues training automatically.

### List services

`GET /services/list/?chatbot=<slug>&page=<number>&page_size=<number>`

Optional filters: `search` (matches name, service type), `status`.
Returns paginated services in `data` and pagination fields in `meta`.

### Update a service

`PATCH /services/update/?chatbot=<slug>&service=<uuid>` (JSON)

Send changed service fields. The service is marked pending and a
retrain is queued automatically. Returns `{ service, training_log }` in
`data`.

### Remove a service

`DELETE /services/delete/?chatbot=<slug>&service=<uuid>`

Deletes the service and removes its vector from search.

## Search

`POST /search/` (JSON)

Provide `chatbot` (slug) and `query` (the user's question); optional
`source` to scope retrieval to one space: `document` (base knowledge),
`product`, or `service` — omit it to search everything; optional
`limit` (default 8, maximum 50).

Returns ranked chunks from the vector store in `data`, each with `id`,
`space`, `knowledge_base_id`, `product_id`, `service_id`,
`chunk_index`, `content`, `metadata`, and `score`. This performs
retrieval only and does not invoke an LLM.

## Training logs

`GET /logs/list/?chatbot=<slug>&page=<number>&page_size=<number>`

Optional filter: `space` (`document`, `product`, `service`). Returns a
paginated audit trail of training runs in `data` (stage, progress,
chunk counts, error message, timestamps).

## Shared conventions

- Requests identify the chatbot by `chatbot` slug; detail, update,
  retrain, and delete requests also carry the item's UUID.
- Successful responses use
  `{ "status": <code>, "success": true, "message": "...", "data": ... }`.
  List responses also include `meta` with pagination fields
  (`count`, `page`, `page_size`, `num_pages`, `next`, `previous`).
- Requests for a chatbot the user does not own return 404 (superusers
  may access every chatbot).
- Currencies are 3-letter codes; supported: USD, EUR, GBP, BDT, INR,
  AUD, CAD, JPY.
- File responses never expose the private object key.
