from django.shortcuts import get_object_or_404
from rest_framework import status

from app.utils.pagination import CustomPagination
from app.utils.response import APIResponse
from chatbot.api.v1.client.serializers import ChatbotQuerySerializer


def first_error_message(errors, fallback="Request failed."):
    if isinstance(errors, dict):
        for value in errors.values():
            message = first_error_message(value, fallback="")
            if message:
                return message
        return fallback
    if isinstance(errors, (list, tuple)):
        for value in errors:
            message = first_error_message(value, fallback="")
            if message:
                return message
        return fallback
    return str(errors) if errors else fallback


def validation_error_response(errors, fallback):
    return APIResponse.error(
        errors=errors,
        message=first_error_message(errors, fallback=fallback),
        status=status.HTTP_400_BAD_REQUEST,
    )


class PaginatedListMixin:
    pagination_class = CustomPagination

    def paginated_response(self, queryset, *, message):
        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, self.request, view=self)
        serializer = self.get_serializer(page, many=True)
        return APIResponse.success(
            data=serializer.data,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(self.request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
            },
            message=message,
        )


class ChatbotObjectMixin:
    """Resolve a chatbot by the ``chatbot`` slug query parameter."""

    _chatbot = None

    def get_chatbot_queryset(self):
        raise NotImplementedError

    def get_chatbot(self):
        if self._chatbot is None:
            query_serializer = ChatbotQuerySerializer(
                data=self.request.query_params,
            )
            query_serializer.is_valid(raise_exception=True)
            self._chatbot = get_object_or_404(
                self.get_chatbot_queryset().select_related("created_by"),
                slug=query_serializer.validated_data["chatbot"],
            )
        return self._chatbot
