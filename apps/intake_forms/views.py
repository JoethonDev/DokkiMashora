from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.academics.models import Cohort, CourseVersion
from apps.applications.models import ProgramApplication
from apps.core.pagination import paginate
from apps.core.permissions import ROLE_SENIOR_STAFF, is_admin, role_required, student_required

from .constants import FIELD_TYPE_BOOLEAN, INTAKE_REQUEST_STATUS_EXPIRED, INTAKE_REQUEST_STATUS_SUBMITTED
from .forms import IntakeFormBuildForm, IntakeFormFieldForm, IntakeFormSubmitForm
from .models import IntakeForm, IntakeFormField, IntakeFormResponse
from .selectors import get_intake_responses_for_target
from .services import (
    get_intake_request_by_token,
    request_intake_form_for_application,
    submit_intake_form_response,
)


def _get_profile_or_404(user):
    if not hasattr(user, "student_profile"):
        raise Http404(_("No student profile."))
    return user.student_profile


# ---------------------------------------------------------------------------
# Admin: list and manage intake forms
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
@role_required(*ROLE_SENIOR_STAFF)
def admin_intake_form_list(request: HttpRequest) -> HttpResponse:
    forms_qs = IntakeForm.objects.select_related(
        "course_version__course", "cohort__diploma", "created_by",
    ).order_by("-created_at")
    page_obj, _qs = paginate(request, forms_qs)
    return render(request, "intake_forms/admin_form_list.html", {"forms": page_obj})


@require_http_methods(["GET", "POST"])
@role_required(*ROLE_SENIOR_STAFF)
def admin_intake_form_create(request: HttpRequest) -> HttpResponse:
    if request.method == "POST":
        form = IntakeFormBuildForm(request.POST)
        if form.is_valid():
            target_type = form.cleaned_data["target_type"]
            if target_type == "course_version":
                intake_form = IntakeForm.objects.create(
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data.get("description", ""),
                    course_version=form.cleaned_data["course_version"],
                    is_required=form.cleaned_data.get("is_required", False),
                    is_active=form.cleaned_data.get("is_active", True),
                    open_at=form.cleaned_data.get("open_at"),
                    close_at=form.cleaned_data.get("close_at"),
                    created_by=request.user,
                )
            else:
                intake_form = IntakeForm.objects.create(
                    title=form.cleaned_data["title"],
                    description=form.cleaned_data.get("description", ""),
                    cohort=form.cleaned_data["cohort"],
                    is_required=form.cleaned_data.get("is_required", False),
                    is_active=form.cleaned_data.get("is_active", True),
                    open_at=form.cleaned_data.get("open_at"),
                    close_at=form.cleaned_data.get("close_at"),
                    created_by=request.user,
                )
            messages.success(request, _("Intake form created."))
            return redirect("intake_forms:admin_intake_form_detail", form_id=intake_form.pk)
        for _field, errs in form.errors.items():
            for err in errs:
                messages.error(request, err)
    else:
        form = IntakeFormBuildForm()
    return render(request, "intake_forms/admin_form_create.html", {"form": form})


@require_http_methods(["GET", "POST"])
@role_required(*ROLE_SENIOR_STAFF)
def admin_intake_form_detail(request: HttpRequest, form_id: int) -> HttpResponse:
    intake_form = get_object_or_404(
        IntakeForm.objects.select_related("course_version__course", "cohort__diploma"),
        pk=form_id,
    )
    fields = intake_form.fields.order_by("order").all()
    responses = IntakeFormResponse.objects.filter(form=intake_form).select_related(
        "student__user", "program_application",
    ).order_by("-submitted_at")
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add_field":
            field_form = IntakeFormFieldForm(request.POST)
            if field_form.is_valid():
                opts_raw = field_form.cleaned_data.get("options", "")
                opts_list = [o.strip() for o in opts_raw.splitlines() if o.strip()]
                IntakeFormField.objects.create(
                    form=intake_form,
                    label=field_form.cleaned_data["label"],
                    help_text=field_form.cleaned_data.get("help_text", ""),
                    field_type=field_form.cleaned_data["field_type"],
                    required=field_form.cleaned_data.get("required", False),
                    options=opts_list,
                    order=field_form.cleaned_data["order"],
                )
                messages.success(request, _("Field added."))
            else:
                for _field, errs in field_form.errors.items():
                    for err in errs:
                        messages.error(request, err)
            return redirect("intake_forms:admin_intake_form_detail", form_id=intake_form.pk)
        if action == "toggle_active":
            intake_form.is_active = not intake_form.is_active
            intake_form.save(update_fields=["is_active"])
            messages.success(request, _("Form active state toggled."))
            return redirect("intake_forms:admin_intake_form_detail", form_id=intake_form.pk)
        if action == "toggle_required":
            intake_form.is_required = not intake_form.is_required
            intake_form.save(update_fields=["is_required"])
            messages.success(request, _("Form required state toggled."))
            return redirect("intake_forms:admin_intake_form_detail", form_id=intake_form.pk)
    return render(request, "intake_forms/admin_form_detail.html", {
        "intake_form": intake_form,
        "fields": fields,
        "responses": responses,
        "field_form": IntakeFormFieldForm(),
    })


@require_http_methods(["POST"])
@login_required
def admin_intake_form_field_delete(request: HttpRequest, form_id: int, field_id: int) -> HttpResponse:
    if not is_admin(request.user):
        raise PermissionDenied
    field = get_object_or_404(IntakeFormField, pk=field_id, form_id=form_id)
    field.delete()
    messages.success(request, _("Field deleted."))
    return redirect("intake_forms:admin_intake_form_detail", form_id=form_id)


# ---------------------------------------------------------------------------
# Admin: view responses from course-version or cohort detail
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
@login_required
def admin_version_intake_responses(request: HttpRequest, version_id: int) -> HttpResponse:
    if not is_admin(request.user):
        raise PermissionDenied
    target = get_object_or_404(CourseVersion, pk=version_id)
    responses = get_intake_responses_for_target(target)
    return render(request, "intake_forms/admin_response_list.html", {
        "target": target,
        "target_type": "course_version",
        "responses": responses,
    })


@require_http_methods(["GET"])
@login_required
def admin_cohort_intake_responses(request: HttpRequest, cohort_id: int) -> HttpResponse:
    if not is_admin(request.user):
        raise PermissionDenied
    target = get_object_or_404(Cohort, pk=cohort_id)
    responses = get_intake_responses_for_target(target)
    return render(request, "intake_forms/admin_response_list.html", {
        "target": target,
        "target_type": "cohort",
        "responses": responses,
    })


@require_http_methods(["POST"])
@login_required
def admin_intake_request_send(request: HttpRequest, application_id: int) -> HttpResponse:
    if not is_admin(request.user):
        raise PermissionDenied
    application = get_object_or_404(ProgramApplication, pk=application_id)
    try:
        request_intake_form_for_application(
            application, actor=request.user, send_email=True,
        )
        messages.success(request, _("Intake form request sent."))
    except ValueError as exc:
        messages.error(request, str(exc))
    return redirect("applications:program_detail", application_id=application.pk)


# ---------------------------------------------------------------------------
# Student: fill intake form
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
@student_required
def fill_intake_form(request: HttpRequest, token: str) -> HttpResponse:
    """Student fills an intake form via expiring token link."""
    profile = _get_profile_or_404(request.user)
    intake_request = get_intake_request_by_token(token)
    if not intake_request:
        messages.error(request, _("Invalid or expired intake form link."))
        return redirect("students:application_history")

    application = intake_request.program_application
    if application.student != profile:
        messages.error(request, _("This intake form link is not for your account."))
        return redirect("students:application_history")

    if intake_request.status == INTAKE_REQUEST_STATUS_SUBMITTED:
        return render(request, "intake_forms/fill_form.html", {
            "submitted": True,
            "intake_request": intake_request,
            "application": application,
        })

    if intake_request.expires_at <= timezone.now():
        intake_request.status = INTAKE_REQUEST_STATUS_EXPIRED
        intake_request.save(update_fields=["status"])
        return render(request, "intake_forms/fill_form.html", {
            "expired": True,
            "intake_request": intake_request,
            "application": application,
        })

    form_instance = intake_request.form
    active_fields = form_instance.fields.filter(is_active=True).order_by("order")

    if request.method == "POST":
        submit_form = IntakeFormSubmitForm(form_instance, request.POST)
        if submit_form.is_valid():
            answers = {}
            for key, value in submit_form.cleaned_data.items():
                if key.startswith("field_"):
                    field_id = key.replace("field_", "")
                    answers[field_id] = value if value is not None else ""
            # ponytail: convert string booleans to Python bools for consistent JSON storage
            for field in active_fields:
                if field.field_type == FIELD_TYPE_BOOLEAN:
                    pk = str(field.pk)
                    if pk in answers:
                        answers[pk] = answers[pk] == "true"
            try:
                submit_intake_form_response(
                    intake_request, request.user,
                    application=application, answers=answers,
                )
                messages.success(request, _("Intake form submitted successfully."))
                return redirect("students:application_history")
            except ValueError as exc:
                messages.error(request, str(exc))
        else:
            for _field, errs in submit_form.errors.items():
                for err in errs:
                    messages.error(request, err)
    else:
        submit_form = IntakeFormSubmitForm(form_instance)

    return render(request, "intake_forms/fill_form.html", {
        "intake_request": intake_request,
        "application": application,
        "form_instance": form_instance,
        "active_fields": active_fields,
        "form": submit_form,
    })
