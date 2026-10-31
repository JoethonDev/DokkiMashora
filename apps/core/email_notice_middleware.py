"""Middleware: report emails that were skipped because a template is missing.

Email senders never raise when no active/enabled ``EmailTemplate`` exists; they
return ``None`` so the surrounding business action still succeeds. Without
feedback an operator sees a success message while nothing was delivered. This
middleware turns the request-scoped record collected by
``apps.communications.services`` into a warning toast on the next page, so every
send path (application actions, entrance exams, payments, activation) reports
the same way. Resending stays on the affected page, where the relevant control
already lives.
"""

from __future__ import annotations

from django.contrib import messages
from django.utils.translation import gettext as _

from apps.communications.models import EmailTemplate
from apps.communications.services import (
    consume_unavailable_templates,
    reset_unavailable_templates,
)


class EmailTemplateNoticeMiddleware:
    """Surface "template missing or disabled" for the acting admin."""

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        reset_unavailable_templates()
        response = self.get_response(request)
        slugs = consume_unavailable_templates()
        # Only staff see operational delivery notices; students/staff-less users
        # must never be shown internal template state.
        if slugs and getattr(request.user, "is_staff_member", False):
            self._warn(request, slugs)
        return response

    @staticmethod
    def _warn(request, slugs: tuple[str, ...]) -> None:
        names = dict(EmailTemplate.objects.filter(slug__in=slugs).values_list("slug", "name"))
        for slug in slugs:
            label = names.get(slug) or slug
            messages.warning(
                request,
                _(
                    "Email not sent: the %(label)s template (%(slug)s) is missing "
                    "or disabled. Enable it under Email templates, then resend."
                )
                % {"label": label, "slug": slug},
            )
