#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${SCRIPT_DIR}/.env"
APP_ENV="dev"

if [[ -f "${ENV_FILE}" ]]; then
  APP_ENV="$(sed -n 's/^APP_ENV=//p' "${ENV_FILE}" | tail -n 1)"
  APP_ENV="${APP_ENV:-dev}"
fi

if [[ "${APP_ENV}" == "prod" ]]; then
  docker compose -p dison --env-file "${ENV_FILE}" -f "${SCRIPT_DIR}/docker/compose.prod.yml" up --build -d "$@"
else
  docker compose -p dison --env-file "${ENV_FILE}" -f "${SCRIPT_DIR}/docker/compose.dev.yml" down --remove-orphans
  docker compose -p dison --env-file "${ENV_FILE}" -f "${SCRIPT_DIR}/docker/compose.dev.yml" up --build "$@"
fi
