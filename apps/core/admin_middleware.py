"""Middleware: force English for admin, handle 405 gracefully."""

from urllib.parse import urlparse

from django.contrib import messages
from django.http import HttpResponseNotAllowed, HttpResponseRedirect
from django.utils import translation
from django.utils.translation import gettext_lazy as _


class ForceEnglishAdminMiddleware:
    """Activate English for /admin/ requests and management commands."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        if request.path.startswith("/admin/"):
            translation.activate("en")
            request.LANGUAGE_CODE = "en"
        return self.get_response(request)


class MethodNotAllowedMiddleware:
    """Redirect bare 405 responses to a safe URL with a message."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        if not isinstance(response, HttpResponseNotAllowed):
            return response
        if request.method == "POST":
            messages.error(request, _("This action was not valid. Please try again."))
            referrer = request.META.get("HTTP_REFERER", "")
            if referrer and urlparse(referrer).netloc == request.get_host():
                return HttpResponseRedirect(referrer)
            return HttpResponseRedirect("/")
        messages.warning(request, _("This page requires a form submission."))
        return HttpResponseRedirect("/")
