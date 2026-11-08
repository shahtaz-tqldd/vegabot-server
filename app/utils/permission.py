from rest_framework.permissions import BasePermission


class IsAdmin(BasePermission):
    message = "Only staff users can perform this action."

    def has_permission(self, request, view):
        user = request.user
        return bool(user and user.is_authenticated and getattr(user, "is_staff", False))


class IsSuperAdmin(BasePermission):
    message = "Only superadmin users can perform this action."

    def has_permission(self, request, view):
        user = request.user
        return bool(
            user
            and user.is_authenticated
            and getattr(user, "is_superuser", False)
        )
