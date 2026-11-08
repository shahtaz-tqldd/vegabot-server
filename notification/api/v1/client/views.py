from django.db.models import Exists, OuterRef, Subquery
from django.shortcuts import get_object_or_404
from django.utils import timezone
from rest_framework import status
from rest_framework.generics import GenericAPIView
from rest_framework.permissions import IsAuthenticated

from app.utils.pagination import CustomPagination
from app.utils.response import APIResponse
from notification.api.v1.client.serializers import NotificationSerializer
from notification.models import Notification, NotificationRead


class NotificationQuerysetMixin:
    def get_user_notifications(self):
        """Notifications for the chatbots the requester owns; superusers
        see every chatbot's notifications."""
        read_receipts = NotificationRead.objects.filter(
            notification=OuterRef("pk"),
            user=self.request.user,
        )
        queryset = Notification.objects.filter(chatbot__is_deleted=False)
        if not self.request.user.is_superuser:
            queryset = queryset.filter(chatbot__created_by=self.request.user)
        return queryset.select_related("chatbot").annotate(
            is_read=Exists(read_receipts),
            read_at=Subquery(read_receipts.values("read_at")[:1]),
        ).order_by("-created_at")

    def apply_filters(self, queryset, include_unread_filter=True):
        params = self.request.query_params
        chatbot_id = params.get("chatbot_id")
        if chatbot_id:
            queryset = queryset.filter(chatbot_id=chatbot_id)

        unread_only = params.get("unread_only", "").lower() in {"1", "true", "yes"}
        if include_unread_filter and unread_only:
            queryset = queryset.filter(is_read=False)
        return queryset


class NotificationListAPIView(NotificationQuerysetMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]
    pagination_class = CustomPagination

    def get(self, request, *args, **kwargs):
        paginator = self.pagination_class()
        queryset = self.apply_filters(self.get_user_notifications())
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = NotificationSerializer(
            page,
            many=True,
            context={"request": request},
        )
        unread_count = (
            self.apply_filters(
                self.get_user_notifications(),
                include_unread_filter=False,
            ).filter(is_read=False).count()
        )
        return APIResponse.success(
            data=serializer.data,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
                "unread_count": unread_count,
            },
            message="Notifications fetched successfully.",
        )


class NotificationReadAPIView(NotificationQuerysetMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, *args, **kwargs):
        notification = get_object_or_404(
            self.get_user_notifications(),
            pk=kwargs["notification_id"],
        )
        read, _ = NotificationRead.objects.get_or_create(
            notification=notification,
            user=request.user,
            defaults={"created_by": request.user},
        )
        return APIResponse.success(
            data={
                "id": str(notification.id),
                "is_read": True,
                "read_at": read.read_at or timezone.now(),
            },
            message="Notification marked as read.",
        )


class NotificationReadAllAPIView(NotificationQuerysetMixin, GenericAPIView):
    permission_classes = [IsAuthenticated]

    def patch(self, request, *args, **kwargs):
        notifications = self.apply_filters(self.get_user_notifications()).filter(
            is_read=False,
        )
        receipts = [
            NotificationRead(
                notification=notification,
                user=request.user,
                created_by=request.user,
            )
            for notification in notifications
        ]
        NotificationRead.objects.bulk_create(receipts, ignore_conflicts=True)
        return APIResponse.success(
            data={"marked_read_count": len(receipts)},
            message="Notifications marked as read.",
            status=status.HTTP_200_OK,
        )
