from urllib.parse import parse_qs, urlparse

from app.settings.env import env, env_bool, env_int


def _database_config_from_url(database_url, *, fallback_name, fallback_user, fallback_password, fallback_host, fallback_port, fallback_ssl):
    if not database_url:
        return {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": fallback_name,
            "USER": fallback_user,
            "PASSWORD": fallback_password,
            "HOST": fallback_host,
            "PORT": fallback_port,
            "OPTIONS": {"sslmode": "require"} if fallback_ssl else {},
        }

    parsed = urlparse(database_url)
    query = parse_qs(parsed.query)
    sslmode = query.get("sslmode", [None])[0]

    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": (parsed.path or "").lstrip("/") or fallback_name,
        "USER": parsed.username or fallback_user,
        "PASSWORD": parsed.password or fallback_password,
        "HOST": parsed.hostname or fallback_host,
        "PORT": parsed.port or fallback_port,
        "OPTIONS": {"sslmode": sslmode or "require"} if sslmode or fallback_ssl else {},
    }


default_db = _database_config_from_url(
    env("DATABASE_URL", ""),
    fallback_name=env("DB_NAME", "dison_db"),
    fallback_user=env("DB_USER", "dison_db_owner"),
    fallback_password=env("DB_PASSWORD", "dison_db_password"),
    fallback_host=env("DB_HOST", "postgres"),
    fallback_port=env_int("DB_PORT", 5432),
    fallback_ssl=env_bool("DB_SSL_REQUIRE", False),
)

DATABASES = {
    "default": default_db,
}
