# Vegabot Backend

Vegabot is an AI chatbot assistant that a business can train on its own content and then talk to by **text or voice**. A chatbot is configured with any combination of three knowledge sources: general **knowledge**, a **product** catalog, and a **service** list. It answers questions grounded in that content, recommends products and services, helps a user build a cart and place a purchase, and hands the conversation to a human when it reaches the limits of what it knows.

This repository is the backend: a Django service that exposes the REST API, the WebSocket API for live text and audio conversations, and the background workers that ingest and index knowledge.

> Vegabot is built as a demonstration project. It is not a production customer-facing service.

## Highlights

- **Three knowledge sources, chosen per chatbot.** Knowledge, products and services are each switched on or off per chatbot, so a service-only business can leave products off and a retailer can leave services off. The agent only gets the tools and instructions for the sources a chatbot uses.
- **Hybrid retrieval in PostgreSQL.** pgvector HNSW similarity search and GIN-indexed full-text search are merged with Reciprocal Rank Fusion (RRF).
- **Agent built on Google ADK.** Tools for knowledge search, product/service search, recommendations, and human escalation are attached only for the sources a chatbot actually has.
- **Real-time voice conversations with Gemini Live.** Audio streams in both directions over WebSockets, with live transcripts and a configurable voice.
- **Configurable behavior.** Custom instructions, tone, guardrails, fallback and escalation rules, and the voice are all set per chatbot.
- **Fully containerized.** Docker Compose runs the API, WebSocket server, Celery workers, PostgreSQL with pgvector, and Redis.

## Features

### Training the chatbot: three knowledge sources

| Source | Input | Notes |
| --- | --- | --- |
| **Knowledge** (default) | Files (PDF, DOCX, XLSX/CSV), website URLs, raw text | Content is extracted, chunked, embedded, and indexed. Websites are rendered with Playwright/Chromium. |
| **Products** | Structured CSV/XLSX template, or one at a time | Name, SKU, category, description, price, currency, product URL, image. |
| **Services** | Structured template, or added manually | Name, service type, description, cost, currency. |

Training runs asynchronously in Celery. Each run writes a training log, and each item records its own status and any error, so a partially failed import can be inspected and retried. File size, page, row, and chunk limits are configurable (`KNOWLEDGE_*`).

Each source is indexed in its own vector *space* (`document`, `product`, `service`), with a dedicated HNSW index for each.

### Conversation capabilities

- **Grounded answers** retrieved from the chatbot's knowledge.
- **Product and service recommendations**, returned as structured cards that the client renders next to the reply.
- **Cart and order taking** (an optional add-on for products). The agent adds products to a cart, reads the cart and total back to the user, and places the order once the user confirms. Cart items and orders are stored per chat session, and each change is pushed to the client as a real-time socket event.
- **Human escalation.** When a question falls outside the chatbot's knowledge or matches its escalation rule, the agent opens an escalation with a reason and a human can take over the session.
- **Attachments** on chat messages.

### Voice mode

A chat session can switch between text and audio at any time. In audio mode:

- The user's microphone audio streams to a Gemini Live run through an ADK `LiveRequestQueue`.
- The agent's spoken reply streams back as binary audio frames, together with its transcript. The transcript uses the same events as text mode, so a client renders both modes the same way.
- The user's speech is captioned live, and the final transcript is stored as a message.
- Text and voice turns share one ADK session, so either mode sees the full conversation history.
- The voice can be chosen per chatbot, with a deployment-wide default.

### Per-chatbot configuration

- Name, business type, description, logo, language, and timezone.
- **Tone** (for example, professional or friendly).
- **Custom instructions** that shape the agent's behavior.
- **Guardrails:** a *never answer* list of topics the chatbot must refuse, and a *fallback message* for questions it cannot answer.
- **Escalation rule** that defines when the chatbot hands off to a human.
- **Welcome message** and **voice** selection.

The agent's prompt is built from a cached chatbot profile. The cache is invalidated by signals whenever the chatbot or its knowledge changes, so a conversation turn does not need extra database reads.

### Platform

- JWT authentication, email verification (OTP), and password reset.
- Real-time chat over Django Channels, with presence tracking and event broadcasting.
- Token usage and cost tracking for every AI call, covering text and Live models.
- In-app notifications.
- Cloudflare R2 object storage for public images and private knowledge files.

## How retrieval works

```text
query ──► Gemini embedding ──► pgvector HNSW (cosine, halfvec) ──► top 20 ─┐
      └─► to_tsquery ────────► GIN index on generated tsvector ──► top 20 ─┴─► RRF (k = 60) ──► top results
```

1. **Semantic search:** the query is embedded with Gemini, and the nearest chunks in the chatbot's space are found through an HNSW index (`halfvec_cosine_ops`).
2. **Keyword search:** the content column has a generated `tsvector` with a GIN index. It is ranked with PostgreSQL full-text search, which catches exact terms such as SKUs and product names that embeddings can miss.
3. **Fusion:** both candidate lists are merged with Reciprocal Rank Fusion, `score = Σ 1 / (60 + rank)`. A chunk that ranks well in either list surfaces, and a chunk that ranks well in both rises to the top.

Search is always scoped to one chatbot and one space.

## Architecture

The application is a modular Django monolith with several runtime processes:

| Process | Responsibility | Default host port |
| --- | --- | --- |
| WSGI | REST API and Django admin | `8013` |
| ASGI | WebSockets: live text and audio chat | `8014` |
| Celery worker | Knowledge training, AI tasks, notifications | — |
| Celery beat | Periodic tasks | — |
| PostgreSQL + pgvector | Relational data, vectors, ADK session store | `5432` |
| Redis | Celery broker/results, Channels layer, cache, presence | `6379` |

```text
accounts/        Identity, authentication, email verification
chatbot/         Chatbot configuration and access control
knowledge/       Knowledge bases, products, services, training pipeline
vector_store/    pgvector embeddings and hybrid (vector + FTS + RRF) search
agent/           Google ADK root agent, tools, text replies, Gemini Live voice
chat/            Sessions, messages, attachments, human escalations
analytics/       AI usage and cost records
notification/    Application notifications
base/socket/     Channels consumers, presence, event broadcasting
app/             Settings, shared services, entry points
```

REST endpoints are mounted under `/api/v1/`, admin REST endpoints under `/api/v1/admin/`, Django admin under `/admin/`, and WebSocket routes under `/ws/`.

## Technology Stack

| Area | Technology |
| --- | --- |
| Language / framework | Python 3.12, Django 5, Django REST Framework |
| AI agent | Google Agent Development Kit (ADK) |
| Models | Gemini on Vertex AI (chat, embeddings), Gemini Live (voice) |
| Database | PostgreSQL with pgvector (HNSW) and full-text search (GIN) |
| Real-time | Django Channels, Channels Redis, Uvicorn |
| Background jobs | Celery with Redis |
| Ingestion | Playwright, MarkItDown, pypdf, python-docx, openpyxl, LangChain text splitters, tiktoken |
| Storage | Cloudflare R2 (S3-compatible, boto3) |
| Auth | Simple JWT |
| Deployment | Docker, Docker Compose, Gunicorn |

Pinned dependencies are listed in [`requirements.txt`](requirements.txt).

## Quick Start with Docker

**Prerequisites:** Docker Engine with Compose v2. AI features also need a Google Cloud project with Vertex AI enabled and a service-account key.

1. Create the environment file and fill in at least `APP_SECRET` and the Google Cloud settings:

   ```bash
   cp .env.example .env
   ```

2. Put the Google service-account key at `secrets/service-account.json`. It is mounted in the container at the path set by `GOOGLE_APPLICATION_CREDENTIALS`.

3. Start the stack:

   ```bash
   ./startapp.sh
   ```

   The script reads `APP_ENV` from `.env`, builds the images, starts PostgreSQL and Redis, applies migrations, and runs the WSGI, ASGI, Celery worker, and Celery beat services. In development, source code is bind-mounted so changes reload.

4. Open the app:
   - REST API: `http://localhost:8013/api/v1/`
   - Django admin: `http://localhost:8013/admin/`
   - WebSockets: `ws://localhost:8014/ws/`

5. Stop the stack:

   ```bash
   docker compose -p vegabot --env-file .env -f docker/compose.dev.yml down
   ```

Database and Redis data are kept in Docker volumes.

### Common commands

```bash
DC="docker compose -p vegabot --env-file .env -f docker/compose.dev.yml"

$DC exec wsgi python manage.py makemigrations
$DC exec wsgi python manage.py migrate
$DC exec wsgi python manage.py test
$DC exec wsgi python manage.py shell
$DC logs -f wsgi asgi celery-worker celery-beat
```

## Configuration

All configuration is read from `.env`. [`.env.example`](.env.example) lists every supported variable.

| Group | Variables | Purpose |
| --- | --- | --- |
| Core | `APP_ENV`, `APP_SECRET`, `DEBUG`, `ALLOWED_HOSTS`, `CSRF_TRUSTED_ORIGINS`, `CORS_ALLOWED_ORIGINS`, `TIME_ZONE` | Django runtime. `APP_ENV` (`dev`/`prod`) also selects the Compose file. |
| Database | `DB_*`, `ADK_DB_URL` | PostgreSQL connection, plus the separate database that stores ADK sessions. |
| Redis | `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND`, `CACHE_URL` | Celery, cache, and the Channels layer. |
| Google Cloud | `GOOGLE_GENAI_USE_VERTEXAI`, `GOOGLE_CLOUD_PROJECT_ID`, `GOOGLE_CLOUD_LOCATION`, `GOOGLE_CLOUD_LIVE_CHAT_LOCATION`, `GOOGLE_APPLICATION_CREDENTIALS` | Vertex AI project, regions (the Live model can use a different region), and credentials. |
| Gemini | `GEMINI_CHAT_MODEL`, `GEMINI_CHAT_MAX_OUTPUT_TOKENS`, `GEMINI_LIVE_CHAT_MODEL`, `GEMINI_LIVE_CHAT_VOICE` | Text and voice models, and the default voice. |
| Embeddings | `GEMINI_EMBEDDING_MODEL`, `GEMINI_EMBEDDING_DIMENSIONS`, `GEMINI_EMBEDDING_BATCH_SIZE`, `GEMINI_EMBEDDING_USE_VERTEX`, `GEMINI_API_KEY` | Embedding model and vector size. Calls go through Vertex AI or, when Vertex is disabled, the Gemini Developer API. |
| Cost tracking | `GEMINI_*_COST_PER_MILLION` | Token rates used to record AI cost for text and Live models. |
| Training | `KNOWLEDGE_*` | Ingestion limits, chunk size, and chunk overlap. |
| Storage | `R2_*` | Cloudflare R2 bucket, credentials, and key prefixes. |
| Email | `EMAIL_*`, `DEFAULT_FROM_EMAIL` | SMTP for verification and password-reset emails. |
| Frontend | `USER_FRONTEND_URL`, `ADMIN_FRONTEND_URL`, `PASSWORD_RESET_PATH` | Links included in emails. |
| Ports | `WSGI_PORT`, `ASGI_PORT`, `POSTGRES_FORWARD_PORT`, `REDIS_FORWARD_PORT` | Host port mappings. |

### Initial administrator

When `SUPERUSER_NAME`, `SUPERUSER_EMAIL`, and `SUPERUSER_PASSWORD` are set, a Django superuser is created on first start. You can also create one manually:

```bash
docker compose -p vegabot --env-file .env -f docker/compose.dev.yml exec wsgi \
  python manage.py create_superuser
```

## Running without Docker

Docker is recommended because the project needs PostgreSQL with pgvector, Redis, and Chromium. For a native setup:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env        # point DB, Redis, and ADK_DB_URL at local services
python manage.py migrate
```

Run each process in its own terminal:

```bash
python manage.py runserver 0.0.0.0:8013
uvicorn app.asgi:application --host 0.0.0.0 --port 8014 --reload
celery -A app worker -l info
celery -A app beat -l info
```

## Production Containers

Set `APP_ENV=prod` and production-safe values in `.env`, then run `./startapp.sh`. Production mode uses [`docker/compose.prod.yml`](docker/compose.prod.yml), runs detached, serves WSGI through Gunicorn and ASGI through Uvicorn workers, collects static files, and keeps data in named volumes. Put it behind a reverse proxy that terminates TLS and sends `/ws/` traffic to the ASGI service.

## Further Documentation

[`docs/`](docs/) contains detailed notes on the agent ([`docs/agent.md`](docs/agent.md)), real-time chat sessions ([`docs/realtime_chat_session.md`](docs/realtime_chat_session.md)), object storage, per-module API references ([`docs/api_module/`](docs/api_module/)), and a Postman collection.
