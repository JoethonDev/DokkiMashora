"""Core views: landing page, error handlers, language switcher."""

from __future__ import annotations

from django.conf import settings
from django.http import (
    HttpRequest,
    HttpResponse,
    HttpResponseBadRequest,
    HttpResponseRedirect,
)
from django.shortcuts import render
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.csrf import csrf_protect
from django.views.decorators.http import require_http_methods


def landing(request: HttpRequest) -> HttpResponse:
    """Public landing page."""
    return render(request, "core/landing.html")


@csrf_protect
@require_http_methods(["POST"])
def set_language(request: HttpRequest) -> HttpResponse:
    """Switch the active language and redirect to the same page in the new language."""
    new_language = request.POST.get("language")
    next_url = request.POST.get("next", "/")
    available_codes = {code for code, _name in settings.LANGUAGES}
    if new_language not in available_codes:
        return HttpResponseBadRequest("Invalid language")
    if not url_has_allowed_host_and_scheme(
        url=next_url,
        allowed_hosts={request.get_host()},
        require_https=request.is_secure(),
    ):
        next_url = "/"
    translated_url = rewrite_language_url(next_url, new_language)
    response = HttpResponseRedirect(translated_url)
    response.set_cookie(
        settings.LANGUAGE_COOKIE_NAME,
        new_language,
        max_age=settings.LANGUAGE_COOKIE_AGE,
        path=settings.LANGUAGE_COOKIE_PATH,
        secure=request.is_secure(),
        httponly=False,
        samesite="Lax",
    )
    return response


def rewrite_language_url(url: str, new_language: str) -> str:
    """Strip any known language prefix and add the new one."""
    available = {code for code, _name in settings.LANGUAGES}
    path = url
    for code in available:
        prefix = f"/{code}"
        if path == prefix or path == f"{prefix}/":
            path = "/"
            break
        if path.startswith(f"{prefix}/"):
            path = path[len(prefix) :]
            break
    if new_language != settings.LANGUAGE_CODE:
        if not path.startswith("/"):
            path = "/" + path
        path = f"/{new_language}{path}"
    return path


@require_http_methods(["GET", "HEAD"])
def bad_request(request: HttpRequest, exception=None) -> HttpResponse:
    return render(request, "core/errors/400.html", status=400)


@require_http_methods(["GET", "HEAD"])
def permission_denied(request: HttpRequest, exception=None) -> HttpResponse:
    return render(request, "core/errors/403.html", status=403)


@require_http_methods(["GET", "HEAD"])
def page_not_found(request: HttpRequest, exception=None) -> HttpResponse:
    return render(request, "core/errors/404.html", status=404)


@require_http_methods(["GET", "HEAD"])
def server_error(request: HttpRequest) -> HttpResponse:
    return render(request, "core/errors/500.html", status=500)
