DJANGO_BASE_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
]

THIRD_PARTY_APPS = [
    "django.contrib.postgres",
    "rest_framework",
    "corsheaders",
    "django_celery_results",
    "channels",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
]


# Modules are enabled one by one as each is cleaned up for the simplified
# product scope. Re-enable an app here after its own cleanup pass.
MODULER_APPS = [
    "base.socket.apps.SocketConfig",
    "base.apps.BaseConfig",
    "accounts.apps.AccountsConfig",
    "chatbot.apps.ChatbotConfig",
    "chat.apps.ChatConfig",
    "notification.apps.NotificationConfig",
    "knowledge.apps.KnowledgeConfig",
    "vector_store.apps.VectorStoreConfig",
    "analytics.apps.AnalyticsConfig",
]

INSTALLED_APPS = DJANGO_BASE_APPS + THIRD_PARTY_APPS + MODULER_APPS
