from datetime import timedelta

from celery.schedules import crontab

from app.settings.env import (
    PROJECT_DIR,
    env,
    env_bool,
    env_float,
    env_int,
    env_list,
)

APP_ENV = env("APP_ENV", "dev")
SECRET_KEY = env("APP_SECRET", "django-insecure-change-me")
DEBUG = APP_ENV == "dev" or env_bool("DEBUG", False)
ALLOWED_HOSTS = env_list("ALLOWED_HOSTS")
CSRF_TRUSTED_ORIGINS = env_list("CSRF_TRUSTED_ORIGINS")
CORS_ALLOWED_ORIGINS = env_list("CORS_ALLOWED_ORIGINS")
CORS_ALLOW_CREDENTIALS = True
SECURE_PROXY_SSL_HEADER = (
    ("HTTP_X_FORWARDED_PROTO", "https")
    if env_bool("SECURE_PROXY_SSL_HEADER_ENABLED", APP_ENV == "prod")
    else None
)

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
LOG_LEVEL = env("LOG_LEVEL", "INFO").strip().upper()

LOGGING = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "console": {
            "format": "%(levelname)s %(asctime)s %(name)s %(filename)s:%(lineno)d %(message)s",
        },
    },
    "handlers": {
        "console": {
            "class": "logging.StreamHandler",
            "formatter": "console",
        },
    },
    "loggers": {
        "app": {
            "handlers": ["console"],
            "level": LOG_LEVEL,
            "propagate": False,
        },
    },
}

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(hours=24),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=7),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# EMAILS
EMAIL_BACKEND = "django.core.mail.backends.smtp.EmailBackend"
EMAIL_USE_TLS = env_bool("EMAIL_USE_TLS", True)
EMAIL_HOST = env("EMAIL_HOST", "")
EMAIL_PORT = env_int("EMAIL_PORT", 587)
EMAIL_HOST_USER = env("EMAIL_HOST_USER", "")
EMAIL_HOST_PASSWORD = env("EMAIL_HOST_PASSWORD", "")
DEFAULT_FROM_EMAIL = env(
    "DEFAULT_FROM_EMAIL", EMAIL_HOST_USER or "no-reply@vegabot.com"
)
EMAIL_VERIFICATION_OTP_TTL_MINUTES = env_int("EMAIL_VERIFICATION_OTP_TTL_MINUTES", 10)

# STATIC AND MEDIA
STATIC_URL = "/static/"
STATIC_ROOT = PROJECT_DIR / "staticfiles"
STATICFILES_DIRS = [PROJECT_DIR / "static"] if (PROJECT_DIR / "static").exists() else []

MEDIA_URL = "/media/"
MEDIA_ROOT = PROJECT_DIR / "media"

# CELERY
CELERY_BROKER_URL = env("CELERY_BROKER_URL", "redis://redis:6379/0")
CELERY_BROKER_CONNECTION_RETRY_ON_STARTUP = True
CELERY_RESULT_BACKEND = env("CELERY_RESULT_BACKEND", CELERY_BROKER_URL)
CELERY_ACCEPT_CONTENT = ["json"]
CELERY_TASK_SERIALIZER = "json"
CELERY_RESULT_SERIALIZER = "json"
CELERY_TIMEZONE = env("CELERY_TIMEZONE", "UTC")
CELERY_RESULT_EXTENDED = True
CELERY_IMPORTS = (
    "base.tasks",
    "accounts.tasks",
    "knowledge.tasks",
    "agent.reply",
)
CELERY_BEAT_SCHEDULE = {
    "daily-platform-maintenance": {
        "task": "base.tasks.run_daily_maintenance",
        "schedule": crontab(hour=0, minute=15),
    },
}

# CHANNELS
CHANNEL_LAYER_BACKEND = env("CHANNEL_LAYER_BACKEND", "redis")
CHANNEL_REDIS_URL = env("CHANNEL_REDIS_URL", CELERY_BROKER_URL)
if CHANNEL_LAYER_BACKEND == "redis":
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels_redis.core.RedisChannelLayer",
            "CONFIG": {
                "hosts": [CHANNEL_REDIS_URL],
            },
        },
    }
else:
    CHANNEL_LAYERS = {
        "default": {
            "BACKEND": "channels.layers.InMemoryChannelLayer",
        },
    }

# CACHE — Redis-backed when CACHE_URL is set (for example
# redis://redis:6379/2); otherwise a per-process LocMemCache.
CACHE_URL = env("CACHE_URL", "")
CHATBOT_CACHE_TTL_SECONDS = env_int("CHATBOT_CACHE_TTL_SECONDS", 300)

if CACHE_URL:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.redis.RedisCache",
            "LOCATION": CACHE_URL,
        },
    }
else:
    CACHES = {
        "default": {
            "BACKEND": "django.core.cache.backends.locmem.LocMemCache",
            "LOCATION": "argbx",
        },
    }

# ADK
ADK_DB_URL = env("ADK_DB_URL")

# Google cloud
GOOGLE_CLOUD_PROJECT_ID = env("GOOGLE_CLOUD_PROJECT_ID", "")
GOOGLE_CLOUD_LOCATION = env("GOOGLE_CLOUD_LOCATION", "")
GOOGLE_CLOUD_LIVE_CHAT_LOCATION = env("GOOGLE_CLOUD_LIVE_CHAT_LOCATION", "")

# GEMINI
GEMINI_EMBEDDING_MODEL = env("GEMINI_EMBEDDING_MODEL", "gemini-embedding-2")
GEMINI_EMBEDDING_DIMENSIONS = env_int("GEMINI_EMBEDDING_DIMENSIONS", 1536)
GEMINI_EMBEDDING_REQUEST_DELAY_SECONDS = env_float("GEMINI_EMBEDDING_REQUEST_DELAY_SECONDS", 13.0)
# Texts per embedding request where the backend supports batching.
GEMINI_EMBEDDING_BATCH_SIZE = env_int("GEMINI_EMBEDDING_BATCH_SIZE", 100)
# Vertex AI (ADC) by default; set False to use the Gemini Developer API key,
# whose batchEmbedContents batches gemini-embedding-2 in one request.
GEMINI_EMBEDDING_USE_VERTEX = env_bool("GEMINI_EMBEDDING_USE_VERTEX", True)
GEMINI_API_KEY = env("GEMINI_API_KEY", "")
GEMINI_CHAT_MODEL = env("GEMINI_CHAT_MODEL", "gemini-2.5-flash")
GEMINI_LIVE_CHAT_MODEL = env("GEMINI_LIVE_CHAT_MODEL", "gemini-3.8-live")
GEMINI_LIVE_CHAT_VOICE = env("GEMINI_LIVE_CHAT_VOICE", "Puck")
GEMINI_CHAT_MAX_OUTPUT_TOKENS = env_int("GEMINI_CHAT_MAX_OUTPUT_TOKENS", 1024)
# Send agent.debug events (tool calls, results, recommendation decisions)
# to the chat socket. Always logged server-side.
AGENT_CLIENT_DEBUG = env_bool("AGENT_CLIENT_DEBUG", DEBUG)
GEMINI_INPUT_COST_PER_MILLION = env_float("GEMINI_INPUT_COST_PER_MILLION", 0.30)
GEMINI_OUTPUT_COST_PER_MILLION = env_float("GEMINI_OUTPUT_COST_PER_MILLION", 2.50)
# Live (voice) model rates; default to the chat rates until configured.
GEMINI_LIVE_INPUT_COST_PER_MILLION = env_float(
    "GEMINI_LIVE_INPUT_COST_PER_MILLION", GEMINI_INPUT_COST_PER_MILLION,
)
GEMINI_LIVE_OUTPUT_COST_PER_MILLION = env_float(
    "GEMINI_LIVE_OUTPUT_COST_PER_MILLION", GEMINI_OUTPUT_COST_PER_MILLION,
)

# CLOUDFLARE R2 STORAGE
R2_ACCOUNT_ID = env("R2_ACCOUNT_ID", "")
R2_ACCESS_KEY_ID = env("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = env("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = env("R2_BUCKET_NAME", "")
R2_ENDPOINT_URL = env("R2_ENDPOINT_URL", "") or (
    f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"
    if R2_ACCOUNT_ID
    else None
)
R2_PUBLIC_URL = env("R2_PUBLIC_URL", "").rstrip("/")
R2_REGION_NAME = env("R2_REGION_NAME", "auto")
R2_FILES_PREFIX = env("R2_FILES_PREFIX", "files")
R2_IMAGES_PREFIX = env("R2_IMAGES_PREFIX", "images")
R2_IMAGE_CACHE_CONTROL = env(
    "R2_IMAGE_CACHE_CONTROL",
    "public, max-age=31536000, immutable",
)
R2_PRESIGNED_URL_TTL = env_int("R2_PRESIGNED_URL_TTL", 900)

# BASE APP FILE UPLOADS
BASE_FILE_MAX_SIZE_MB = env_int("BASE_FILE_MAX_SIZE_MB", 10)

# CHAT MESSAGE ATTACHMENTS
CHAT_ATTACHMENT_MAX_COUNT = env_int("CHAT_ATTACHMENT_MAX_COUNT", 5)
CHAT_ATTACHMENT_MAX_FILE_SIZE_MB = env_int(
    "CHAT_ATTACHMENT_MAX_FILE_SIZE_MB",
    10,
)

# KNOWLEDGE SOURCE SAFEGUARDS
KNOWLEDGE_MAX_FILE_SIZE_MB = env_int("KNOWLEDGE_MAX_FILE_SIZE_MB", 10)
KNOWLEDGE_MAX_PDF_PAGES = env_int("KNOWLEDGE_MAX_PDF_PAGES", 200)
KNOWLEDGE_MAX_DOCX_PAGES = env_int("KNOWLEDGE_MAX_DOCX_PAGES", 200)
KNOWLEDGE_DOCX_WORDS_PER_PAGE = env_int("KNOWLEDGE_DOCX_WORDS_PER_PAGE", 500)
KNOWLEDGE_MAX_SPREADSHEET_ROWS = env_int("KNOWLEDGE_MAX_SPREADSHEET_ROWS", 50_000)
KNOWLEDGE_MAX_CSV_ROWS = env_int("KNOWLEDGE_MAX_CSV_ROWS", 50_000)
KNOWLEDGE_MAX_STRUCTURED_ITEMS = env_int("KNOWLEDGE_MAX_STRUCTURED_ITEMS", 50_000)
KNOWLEDGE_CHUNK_SIZE = env_int("KNOWLEDGE_CHUNK_SIZE", 400)
KNOWLEDGE_CHUNK_OVERLAP = env_int("KNOWLEDGE_CHUNK_OVERLAP", 50)

# FRONTEND URL
USER_FRONTEND_URL = env("USER_FRONTEND_URL", "http://localhost:5173")
PASSWORD_RESET_PATH = env("PASSWORD_RESET_PATH", "/reset-password")

# LANGUAGE AND TIME
LANGUAGE_CODE = "en-us"
TIME_ZONE = env("TIME_ZONE", "UTC")
USE_I18N = True
USE_TZ = True
