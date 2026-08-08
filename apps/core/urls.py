"""URL patterns for the core app."""

from __future__ import annotations

from django.shortcuts import redirect
from django.urls import path

from . import views


def home(request):
    """Role-aware home (C3): anonymous users get the public landing page.

    Authenticated users go through ``accounts:post_login``, which routes each
    role to its own dashboard. The view takes no parameters and never redirects
    to client-controlled URLs, so it cannot be used as an open redirect.
    """
    if not request.user.is_authenticated:
        return redirect("core:landing")
    return redirect("accounts:post_login")


app_name = "core"

urlpatterns = [
    path("", views.landing, name="landing"),
    path("home/", home, name="home"),
    path("manage/landing/", views.landing_settings, name="landing_settings"),
    path("design-showcase/", views.design_showcase, name="design_showcase"),
    path("workspace/search/", views.workspace_search, name="workspace_search"),
]
