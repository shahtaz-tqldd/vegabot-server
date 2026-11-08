from urllib.parse import parse_qs

from channels.db import database_sync_to_async
from django.contrib.auth.models import AnonymousUser
from rest_framework_simplejwt.exceptions import (
    AuthenticationFailed, 
    InvalidToken, 
    TokenError
)

from app.services.jwt import SafeJWTAuthentication


class JWTAuthMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        scope["user"] = await self.get_user(scope)
        return await self.app(scope, receive, send)

    @database_sync_to_async
    def get_user(self, scope):
        token = self.get_token(scope)
        if not token:
            return AnonymousUser()

        authenticator = SafeJWTAuthentication()
        try:
            validated_token = authenticator.get_validated_token(token)
            return authenticator.get_user(validated_token)
        except (AuthenticationFailed, InvalidToken, TokenError):
            return AnonymousUser()

    def get_token(self, scope):
        headers = dict(scope.get("headers") or [])
        auth_header = headers.get(b"authorization", b"").decode()
        if auth_header.lower().startswith("bearer "):
            return auth_header.split(" ", 1)[1].strip()

        query_string = scope.get("query_string", b"").decode()
        query_params = parse_qs(query_string)
        return (query_params.get("token") or [None])[0]


def JWTAuthMiddlewareStack(app):
    return JWTAuthMiddleware(app)
