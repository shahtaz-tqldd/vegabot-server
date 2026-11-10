# Vegabot Backend

Vegabot Backend is the server-side application for a multi-tenant, AI-assisted customer support and lead engagement platform. It provides REST APIs for the management applications and public widget, WebSocket connections for live conversations, and background workers for AI and knowledge-processing workloads.

This document is intentionally focused on the engineering architecture, dependencies, configuration, and local operation of the backend.

## Capabilities

At a system level, the backend supports:

- User authentication, profile management, email verification, password recovery, and optional Firebase token verification.
- Multi-tenant workspaces with member invitations and role-based access.
- Configurable chatbots, embeddable widget settings, chatbot teams, and member permissions.
- Visitor and agent conversations with persisted messages, real-time updates, presence tracking, human takeover, and session transfer workflows.
- AI-generated replies grounded in chatbot instructions and retrieved knowledge.
- Knowledge ingestion from text, uploaded documents, spreadsheets, and websites; content is extracted, chunked, embedded, and indexed for semantic retrieval.
- Configurable lead capture and appointment scheduling within chatbot conversations.
- Subscription plans, feature limits, Stripe checkout and billing lifecycle handling.
- Notifications and AI usage/cost tracking.
- Cloud object storage for public images and private knowledge files.

## Architecture

The application is a modular Django monolith with separate runtime processes:

| Process | Responsibility | Development port |
| --- | --- | --- |
| WSGI | REST API and Django admin | `8007` |
| ASGI | WebSocket connections and HTTP fallback | `8008` |
| Celery worker | Asynchronous AI, training, notification, and maintenance tasks | — |
| Celery beat | Periodic task scheduling | — |
| PostgreSQL | Relational data, ADK session data, and vector storage | Host `5435` |
| Redis | Celery broker/results, Channels layer, and presence state | Host `6382` |

The primary domain modules are:

```text
accounts/             Identity and account lifecycle
workspace/            Tenant and membership management
chatbot/              Chatbot configuration, widget, and access control
chat/                 Sessions, messages, takeover, and transfer workflows
knowledge/            Source ingestion and asynchronous training
vector_store/         pgvector-backed embeddings and similarity search
lead_capture/         Configurable lead collection
appointment/          Availability and booking management
subscription/         Plans, entitlements, Stripe, and webhooks
analytics/            AI usage and cost records
notification/         Scoped application notifications
base/socket/          Channels consumers, presence, and event broadcasting
agent/                Google ADK agents and conversation tools
app/                  Project settings, shared services, and entry points
```

REST endpoints are mounted under `/api/v1/`, administrative REST endpoints under `/api/v1/admin/`, and Django admin under `/admin/`. WebSocket routes are served by the ASGI process under `/ws/`.

## Technology Stack

- **Language/runtime:** Python 3.12
- **Web framework:** Django 5.1 and Django REST Framework
- **Authentication:** Simple JWT; optional Firebase Admin verification
- **Real-time transport:** Django Channels, Channels Redis, Uvicorn
- **Background processing:** Celery with Redis and `django-celery-results`
- **Production HTTP server:** Gunicorn
- **Database:** PostgreSQL 16 with pgvector and HNSW vector indexing
- **AI:** Google Gemini on Vertex AI and Google Agent Development Kit (ADK)
- **Content processing:** Playwright, MarkItDown, pypdf, python-docx, openpyxl, LangChain text splitters, and tiktoken
- **Object storage:** Cloudflare R2 through its S3-compatible API and boto3
- **Payments:** Stripe
- **Containers:** Docker and Docker Compose

Pinned Python dependencies are listed in [`requirements.txt`](requirements.txt).

## Prerequisites

The recommended development workflow requires:

- Docker Engine with Docker Compose v2
- Access to ports `8007`, `8008`, `5435`, and `6382`, or alternative values configured in `.env`

Provider credentials are not required for the containers and core application to start. They are required when exercising their associated integrations; AI reply and knowledge-training tasks will fail until Google Cloud credentials are configured.

- A Google Cloud project with Vertex AI enabled for Gemini chat, embeddings, and ADK workflows
- A Cloudflare R2 bucket for uploads and knowledge files
- A Stripe account for paid subscription workflows
- SMTP credentials for transactional email
- A Firebase service account when server-side Firebase ID-token verification is enabled

## Quick Start with Docker

1. Create the local environment file:

   ```bash
   cp .env.example .env
   ```

2. Change at least `APP_SECRET` and review the database credentials and allowed frontend origins in `.env`.

3. Start the development stack:

   ```bash
   ./startapp.sh
   ```

   The script builds the images, starts PostgreSQL and Redis, applies migrations, and runs the WSGI, ASGI, Celery worker, and Celery beat services. Development services run in the foreground and source code is bind-mounted for reloads.

4. Verify the application:

   ```bash
   docker compose -p Vegabot --env-file .env -f docker/compose.dev.yml ps
   ```

   The REST API is available at `http://localhost:8007`, WebSockets at `ws://localhost:8008`, and Django admin at `http://localhost:8007/admin/`.

5. Stop the stack:

   ```bash
   docker compose -p Vegabot --env-file .env -f docker/compose.dev.yml down
   ```

Database and Redis data are retained in Docker volumes.

## Configuration

Configuration is loaded from `.env`. Use [`.env.example`](.env.example) as the source of truth for supported settings.

### Core application

| Variable | Purpose |
| --- | --- |
| `APP_ENV` | Selects `dev` or `prod`; also controls the Compose file used by `startapp.sh`. |
| `APP_SECRET` | Django signing secret. Set a strong, unique value outside development. |
| `DEBUG` | Enables Django debug behavior. Production Compose forces this off. |
| `ALLOWED_HOSTS` | Comma-separated hosts accepted by Django. |
| `CSRF_TRUSTED_ORIGINS` | Comma-separated trusted browser origins for CSRF. |
| `CORS_ALLOWED_ORIGINS` | Comma-separated frontend origins allowed to call the API. |
| `TIME_ZONE` / `LOG_LEVEL` | Runtime timezone and application logging level. |

### Data and runtime services

| Variable | Purpose |
| --- | --- |
| `DB_NAME`, `DB_USER`, `DB_PASSWORD`, `DB_HOST`, `DB_PORT` | PostgreSQL connection settings. `DATABASE_URL` may be used as an alternative. |
| `ADK_DB_URL` | Async SQLAlchemy URL for Google ADK session persistence; Compose supplies this automatically. |
| `CELERY_BROKER_URL`, `CELERY_RESULT_BACKEND` | Redis URLs used by Celery. |
| `CHANNEL_REDIS_URL` | Redis URL used by Django Channels; defaults to the Celery broker URL. |
| `PRESENCE_REDIS_URL`, `PRESENCE_REDIS_PREFIX` | Optional dedicated Redis connection and namespace for dashboard presence. |
| `WSGI_PORT`, `ASGI_PORT` | Host ports for REST and WebSocket traffic. |
| `POSTGRES_FORWARD_PORT`, `REDIS_FORWARD_PORT` | Optional host ports for direct access to infrastructure services. |

### AI and knowledge

Gemini and ADK use Vertex AI with Application Default Credentials. Configure:

| Variable | Purpose |
| --- | --- |
| `GOOGLE_CLOUD_PROJECT_ID` | Google Cloud project containing the Vertex AI resources. |
| `GOOGLE_CLOUD_LOCATION` | Vertex AI region. |
| `GOOGLE_APPLICATION_CREDENTIALS` | Path inside the runtime to Application Default Credentials. |
| `GEMINI_CHAT_MODEL` | Gemini model used for generated responses. |
| `GEMINI_EMBEDDING_MODEL`, `GEMINI_EMBEDDING_DIMENSIONS` | Embedding model and pgvector dimensions. |
| `GEMINI_INPUT_COST_PER_MILLION`, `GEMINI_OUTPUT_COST_PER_MILLION` | Rates used for internal AI cost accounting. |
| `KNOWLEDGE_*` | File, page, row, item, chunk-size, and overlap safeguards for ingestion. |

The Docker image includes Chromium for website ingestion through Playwright. If credentials are stored in the repository at `secrets/service-account.json`, set:

```dotenv
GOOGLE_APPLICATION_CREDENTIALS=/app/secrets/service-account.json
```

### External providers

- `R2_*` configures the bucket, S3-compatible endpoint, credentials, public asset URL, object prefixes, and presigned URL lifetime.
- `STRIPE_*` configures API credentials, webhook signing, currency, and the billing portal return URL.
- `EMAIL_*` and `DEFAULT_FROM_EMAIL` configure SMTP delivery and invitation/verification messages.
- provide the service account as JSON in `firebase-account.json` in secrets/ folder.
- `USER_FRONTEND_URL`, `ADMIN_FRONTEND_URL`, `WIDGET_FRONTEND_URL`, and the related path settings configure links emitted by the backend.

### Initial administrator

Set the following values before the first start to create a Django superuser automatically:

```dotenv
SUPERUSER_NAME=Platform Admin
SUPERUSER_EMAIL=admin@example.com
SUPERUSER_PASSWORD=replace-with-a-strong-password
```

If a superuser already exists, automatic creation is skipped. It can also be run manually:

```bash
docker compose -p Vegabot --env-file .env -f docker/compose.dev.yml exec wsgi \
  python manage.py create_superuser
```

## Common Development Commands

Run Django commands inside the WSGI container:

```bash
# Create and apply migrations
docker compose -p Vegabot --env-file .env -f docker/compose.dev.yml exec wsgi \
  python manage.py makemigrations
docker compose -p Vegabot --env-file .env -f docker/compose.dev.yml exec wsgi \
  python manage.py migrate

# Run the test suite
docker compose -p Vegabot --env-file .env -f docker/compose.dev.yml exec wsgi \
  python manage.py test

# Open a Django shell
docker compose -p Vegabot --env-file .env -f docker/compose.dev.yml exec wsgi \
  python manage.py shell

# Follow service logs
docker compose -p Vegabot --env-file .env -f docker/compose.dev.yml logs -f \
  wsgi asgi celery-worker celery-beat
```

## Running without Docker

Docker is preferred because the project requires PostgreSQL with pgvector, Redis, and Chromium. For a native setup:

```bash
python3.12 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python -m playwright install chromium
cp .env.example .env
python manage.py migrate
```

Update `.env` so `DB_HOST`, `DB_PORT`, Redis URLs, `ADK_DB_URL`, and provider credential paths point to locally reachable services. Run the processes in separate terminals:

```bash
python manage.py runserver 0.0.0.0:8007
uvicorn app.asgi:application --host 0.0.0.0 --port 8008 --reload
celery -A app worker -l info
celery -A app beat -l info
```

## Production Containers

Set `APP_ENV=prod` and production-safe values in `.env`, then run:

```bash
./startapp.sh
```

Production mode uses [`docker/compose.prod.yml`](docker/compose.prod.yml), starts the stack in detached mode, serves WSGI through Gunicorn and ASGI through Uvicorn workers, collects static files, and persists PostgreSQL, Redis, and media data in named volumes.

Before deployment, configure real secrets, external hosts and trusted origins, TLS termination, provider credentials, backup/restore procedures, and monitoring. The Compose file exposes the application ports directly and is expected to run behind a production reverse proxy or load balancer that routes REST traffic to WSGI and `/ws/` traffic to ASGI.

## Additional Documentation

Focused implementation notes are available in [`docs/`](docs/), including widget and dashboard WebSocket behavior, chatbot setup, object storage, roles, and pricing. Module-specific notes also exist under `agent/`, `workspace/`, and `subscription/`.


## Open ngrok for local testing command
```
ngrok http --domain=neat-crab-sharing.ngrok-free.app 8007
```


----
Users       -> Basic user for authentication and chatbot's ownership

Chatbots    -> Create, Update, Delete
            -> Set Rules, Tone and Modification

Knowledge   -> Raw Documents
            -> Products
            -> Services

Chat        -> Text 
            -> Audio Live

Performance -> Response Time
