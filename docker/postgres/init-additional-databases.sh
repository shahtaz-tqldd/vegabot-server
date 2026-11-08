#!/usr/bin/env bash
set -euo pipefail

if [[ -z "${POSTGRES_ADDITIONAL_DATABASES:-}" ]]; then
  exit 0
fi

for database in ${POSTGRES_ADDITIONAL_DATABASES//,/ }; do
  database="${database//[[:space:]]/}"

  if [[ -z "${database}" ]]; then
    continue
  fi

  if psql --username "${POSTGRES_USER}" --dbname "${POSTGRES_DB}" -tAc "SELECT 1 FROM pg_database WHERE datname = '${database}'" | grep -q 1; then
    echo "Database '${database}' already exists."
  else
    echo "Creating database '${database}'."
    createdb --username "${POSTGRES_USER}" "${database}"
  fi
done
