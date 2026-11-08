BASE_MIDDLEWARES = [
    "django.middleware.security.SecurityMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

CUSTOM_MIDDLEWARES = []

MIDDLEWARE = BASE_MIDDLEWARES + CUSTOM_MIDDLEWARES

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": (
        "app.services.jwt.SafeJWTAuthentication",
    ),
}
