FROM python:3.12-slim-bookworm AS base

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential libpq-dev netcat-openbsd \
    && rm -rf /var/lib/apt/lists/*

RUN groupadd --gid 1000 appuser \
    && useradd --uid 1000 --gid 1000 --create-home --shell /bin/bash appuser

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

RUN python -m playwright install --with-deps chromium \
    && chown -R appuser:appuser /ms-playwright

COPY . .

RUN mkdir -p /app/media \
    && chmod +x docker/entrypoint.sh startapp.sh \
    && chown -R appuser:appuser /app

USER appuser

ENTRYPOINT ["./docker/entrypoint.sh"]
