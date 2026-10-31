"""Django Admin site restricted to active product staff roles."""

from django.contrib.admin import AdminSite

from .constants import GROUP_ADMIN, GROUP_SUPER_ADMIN


class RoleAwareAdminSite(AdminSite):
    """Prevent historical/deferred roles from entering any admin module."""

    def has_permission(self, request):
        if not super().has_permission(request):
            return False
        user = request.user
        return user.is_superuser or user.groups.filter(
            name__in=(GROUP_SUPER_ADMIN, GROUP_ADMIN),
        ).exists()
