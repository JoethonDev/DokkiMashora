"""Register Django Admin with the product's active staff-role boundary."""

from django.contrib.admin.apps import AdminConfig


class RoleAwareAdminConfig(AdminConfig):
    default_site = "apps.accounts.admin_site.RoleAwareAdminSite"
