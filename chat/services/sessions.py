"""Session visibility shared by the REST API and the socket layer."""

from django.db.models import Q

from chat.models import ChatSession


def visible_sessions(user):
    """Sessions the user may read: their own, or everything for superusers."""
    queryset = (
        ChatSession.objects.select_related("chatbot", "created_by").filter(
            chatbot__is_deleted=False,
        )
    )
    if not user.is_superuser:
        queryset = queryset.filter(created_by=user)
    return queryset


def session_for_user(user, session_id):
    """Return one visible session, or None. Accepts str/UUID ids."""
    try:
        return visible_sessions(user).get(pk=session_id)
    except (ChatSession.DoesNotExist, ValueError, TypeError):
        return None
