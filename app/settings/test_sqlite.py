"""Test settings: run the suite on SQLite without external services."""

from app.settings import *  # noqa: F401,F403

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

# Run celery tasks inline during tests (no broker); vector persistence and
# embeddings are faked per-test at the service boundary.
CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

# Never reach for the redis cache backend in tests.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
        "LOCATION": "dison-tests",
    }
}
