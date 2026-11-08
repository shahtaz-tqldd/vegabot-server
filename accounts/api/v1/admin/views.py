from django.db.models import Q
from rest_framework.generics import GenericAPIView
from rest_framework.parsers import FormParser, JSONParser, MultiPartParser
from rest_framework.permissions import IsAuthenticated

from accounts.api.v1.admin.serializers import (
    AccountListFilterSerializer,
    AccountListSerializer,
    AdminDetailsSerializer,
    AdminLoginSerializer,
    UpdateAdminInfoSerializer,
    UpdateAdminPasswordSerializer,
)
from accounts.models import User
from app.utils.permission import IsAdmin, IsSuperAdmin
from app.utils.pagination import CustomPagination
from app.utils.response import APIResponse


class AdminLoginAPIView(GenericAPIView):
    """Log in a staff account."""

    serializer_class = AdminLoginSerializer

    def post(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        return APIResponse.success(
            data=serializer.validated_data,
            message="Admin logged in.",
        )


class AdminDetailsAPIView(GenericAPIView):
    """Return the authenticated admin account."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = AdminDetailsSerializer

    def get(self, request, *args, **kwargs):
        return APIResponse.success(
            data=self.get_serializer(request.user).data,
            message="Admin details fetched successfully.",
        )


class UpdateAdminInfoAPIView(GenericAPIView):
    """Update the authenticated admin's name or avatar."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = UpdateAdminInfoSerializer
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def patch(self, request, *args, **kwargs):
        serializer = self.get_serializer(
            request.user,
            data=request.data,
            partial=True,
        )
        serializer.is_valid(raise_exception=True)
        admin = serializer.save()
        return APIResponse.success(
            data=AdminDetailsSerializer(
                admin,
                context=self.get_serializer_context(),
            ).data,
            message="Admin information updated successfully.",
        )


class UpdateAdminPasswordAPIView(GenericAPIView):
    """Change the authenticated admin's password."""

    permission_classes = [IsAuthenticated, IsAdmin]
    serializer_class = UpdateAdminPasswordSerializer

    def patch(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return APIResponse.success(message="Admin password updated successfully.")


class AccountListAPIView(GenericAPIView):
    """Return a searchable, filterable list of non-admin accounts."""

    permission_classes = [IsAuthenticated, IsSuperAdmin]
    serializer_class = AccountListSerializer
    pagination_class = CustomPagination

    def get_queryset(self, filters):
        queryset = (
            User.objects.filter(is_staff=False, is_superuser=False)
            .select_related("profile")
            .order_by("-created_at")
        )

        search = filters.get("search", "").strip()
        if search:
            queryset = queryset.filter(
                Q(email__icontains=search)
                | Q(name__icontains=search)
                | Q(profile__phone__icontains=search)
                | Q(profile__city__icontains=search)
                | Q(profile__country__icontains=search)
            )

        statuses = filters.get("status", [])
        if statuses:
            queryset = queryset.filter(profile__status__in=statuses)

        if "is_email_verified" in filters:
            queryset = queryset.filter(
                is_email_verified=filters["is_email_verified"],
            )

        return queryset

    def get(self, request, *args, **kwargs):
        filter_serializer = AccountListFilterSerializer(
            data=self._build_filter_data(request),
        )
        filter_serializer.is_valid(raise_exception=True)
        queryset = self.get_queryset(filter_serializer.validated_data)

        paginator = self.pagination_class()
        page = paginator.paginate_queryset(queryset, request, view=self)
        serializer = self.get_serializer(page, many=True)
        return APIResponse.success(
            data=serializer.data,
            meta={
                "count": paginator.page.paginator.count,
                "page": paginator.page.number,
                "page_size": paginator.get_page_size(request),
                "num_pages": paginator.page.paginator.num_pages,
                "next": paginator.get_next_link(),
                "previous": paginator.get_previous_link(),
            },
            message="Accounts fetched successfully.",
        )

    @staticmethod
    def _build_filter_data(request):
        data = {}

        search = request.query_params.get("search")
        if search is not None:
            data["search"] = search

        statuses = []
        for item in request.query_params.getlist("status"):
            statuses.extend(
                part.strip() for part in str(item).split(",") if part.strip()
            )
        if statuses:
            data["status"] = statuses

        is_email_verified = request.query_params.get("is_email_verified")
        if is_email_verified not in (None, ""):
            data["is_email_verified"] = is_email_verified

        return data
