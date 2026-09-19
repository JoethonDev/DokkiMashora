from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.academics.models import Cohort, CourseVersion
from apps.applications.models import ProgramApplication
from apps.audit.constants import (
    ACTION_INTAKE_FORM_CREATED,
    ACTION_INTAKE_FORM_FIELD_CREATED,
    ACTION_INTAKE_FORM_FIELD_DELETED,
    ACTION_INTAKE_FORM_TOGGLED,
    ACTION_INTAKE_REQUEST_SENT,
)
from apps.audit.services import record_audit_event
from apps.core.pagination import paginate
from apps.core.permissions import ROLE_SENIOR_STAFF, is_admin, role_required, student_required

from .constants import (
    FIELD_TYPE_BOOLEAN,
    FIELD_TYPE_CHOICE,
    FIELD_TYPE_MULTI_CHOICE,
    INTAKE_REQUEST_STATUS_CANCELLED,
    INTAKE_REQUEST_STATUS_SUBMITTED,
    INTAKE_REQUESTABLE_STATUSES,
)
from .forms import IntakeFormBuildForm, IntakeFormFieldForm, IntakeFormSubmitForm
from .models import IntakeForm, IntakeFormField, IntakeFormRequest, IntakeFormResponse
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
    return render(request, "ui/pages/admin/intake.html", {"page_obj": page_obj, "title": _("Intake forms")})


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
            record_audit_event(
                actor=request.user,
                action=ACTION_INTAKE_FORM_CREATED,
                target_type="intake_form",
                target_id=str(intake_form.pk),
                metadata={"title": intake_form.title, "target_type": target_type},
            )
            messages.success(request, _("Intake form created."))
            return redirect("intake_forms:admin_intake_form_detail", form_id=intake_form.pk)
        for _field, errs in form.errors.items():
            for err in errs:
                messages.error(request, err)
    else:
        form = IntakeFormBuildForm()
    return render(request, "ui/pages/admin/intake.html", {"form": form, "title": _("Create intake form")})


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
                if IntakeFormField.objects.filter(
                    form=intake_form, order=field_form.cleaned_data["order"],
                ).exists():
                    messages.error(request, _("A field with this order already exists."))
                    return redirect("intake_forms:admin_intake_form_detail", form_id=intake_form.pk)
                field_type = field_form.cleaned_data["field_type"]
                if field_type in (FIELD_TYPE_CHOICE, FIELD_TYPE_MULTI_CHOICE):
                    opts_list = []
                    for key in request.POST:
                        if key.startswith("intake_choice_"):
                            val = request.POST.get(key, "").strip()
                            if val:
                                opts_list.append(val)
                else:
                    opts_raw = field_form.cleaned_data.get("options", "")
                    opts_list = [o.strip() for o in opts_raw.splitlines() if o.strip()]
                if field_type in (FIELD_TYPE_CHOICE, FIELD_TYPE_MULTI_CHOICE) and not opts_list:
                    messages.error(request, _("Choice fields need at least one option."))
                    return redirect("intake_forms:admin_intake_form_detail", form_id=intake_form.pk)
                field = IntakeFormField.objects.create(
                    form=intake_form,
                    label=field_form.cleaned_data["label"],
                    help_text=field_form.cleaned_data.get("help_text", ""),
                    field_type=field_form.cleaned_data["field_type"],
                    required=field_form.cleaned_data.get("required", False),
                    options=opts_list,
                    order=field_form.cleaned_data["order"],
                )
                record_audit_event(
                    actor=request.user,
                    action=ACTION_INTAKE_FORM_FIELD_CREATED,
                    target_type="intake_form_field",
                    target_id=str(field.pk),
                    metadata={"form_id": str(intake_form.pk), "label": field.label, "field_type": field.field_type},
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
            record_audit_event(
                actor=request.user,
                action=ACTION_INTAKE_FORM_TOGGLED,
                target_type="intake_form",
                target_id=str(intake_form.pk),
                metadata={"is_active": intake_form.is_active},
            )
            messages.success(request, _("Form active state toggled."))
            return redirect("intake_forms:admin_intake_form_detail", form_id=intake_form.pk)
        if action == "toggle_required":
            intake_form.is_required = not intake_form.is_required
            intake_form.save(update_fields=["is_required"])
            messages.success(request, _("Form required state toggled."))
            return redirect("intake_forms:admin_intake_form_detail", form_id=intake_form.pk)
    return render(request, "ui/pages/admin/intake.html", {
        "intake_form": intake_form,
        "fields": fields,
        "responses": responses,
        "field_form": IntakeFormFieldForm(),
    })


@require_http_methods(["GET"])
@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_intake_response_detail(request: HttpRequest, form_id: int, response_id: int) -> HttpResponse:
    response = get_object_or_404(
        IntakeFormResponse.objects.select_related(
            "form", "student__user", "program_application",
        ).prefetch_related("answers__field"),
        pk=response_id, form_id=form_id,
    )
    return render(request, "ui/pages/admin/intake.html", {
        "response": response,
        "form_obj": response.form,
    })


@require_http_methods(["POST"])
@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_intake_form_field_delete(request: HttpRequest, form_id: int, field_id: int) -> HttpResponse:
    if not is_admin(request.user):
        raise PermissionDenied
    field = get_object_or_404(IntakeFormField, pk=field_id, form_id=form_id)
    f_id = field.pk
    f_label = field.label
    field.delete()
    record_audit_event(
        actor=request.user,
        action=ACTION_INTAKE_FORM_FIELD_DELETED,
        target_type="intake_form_field",
        target_id=str(f_id),
        metadata={"form_id": str(form_id), "label": f_label},
    )
    messages.success(request, _("Field deleted."))
    return redirect("intake_forms:admin_intake_form_detail", form_id=form_id)


# ---------------------------------------------------------------------------
# Admin: view responses from course-version or cohort detail
# ---------------------------------------------------------------------------


@require_http_methods(["GET"])
@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_version_intake_responses(request: HttpRequest, version_id: int) -> HttpResponse:
    if not is_admin(request.user):
        raise PermissionDenied
    target = get_object_or_404(CourseVersion, pk=version_id)
    responses = get_intake_responses_for_target(target)
    return render(request, "ui/pages/admin/intake.html", {
        "target": target,
        "target_type": "course_version",
        "responses": responses,
    })


@require_http_methods(["GET"])
@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_cohort_intake_responses(request: HttpRequest, cohort_id: int) -> HttpResponse:
    if not is_admin(request.user):
        raise PermissionDenied
    target = get_object_or_404(Cohort, pk=cohort_id)
    responses = get_intake_responses_for_target(target)
    return render(request, "ui/pages/admin/intake.html", {
        "target": target,
        "target_type": "cohort",
        "responses": responses,
    })


@require_http_methods(["POST"])
@login_required
@role_required(*ROLE_SENIOR_STAFF)
def admin_intake_request_send(request: HttpRequest, application_id: int) -> HttpResponse:
    if not is_admin(request.user):
        raise PermissionDenied
    application = get_object_or_404(ProgramApplication, pk=application_id)
    if application.status not in INTAKE_REQUESTABLE_STATUSES:
        messages.error(request, _("Intake forms can only be requested after the admissions decision and required document submission."))
        return redirect("applications:program_detail", application_id=application.pk)
    try:
        intake_request = request_intake_form_for_application(
            application, actor=request.user, send_email=True,
        )
        record_audit_event(
            actor=request.user,
            action=ACTION_INTAKE_REQUEST_SENT,
            target_type="intake_form_request",
            target_id=str(intake_request.pk),
            metadata={"application_id": str(application.pk)},
        )
        messages.success(request, _("Intake request created. Survey email queued."))
    except (ValueError, PermissionError) as exc:
        messages.error(request, str(exc))
    return redirect("applications:program_detail", application_id=application.pk)


# ---------------------------------------------------------------------------
# Student: fill intake form
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
@student_required
def fill_intake_form(request: HttpRequest, token: str) -> HttpResponse:
    """Student fills an intake form via an expiring token link."""
    intake_request = get_intake_request_by_token(token)
    if not intake_request:
        messages.error(request, _("Invalid or expired intake form link."))
        return redirect("students:applications")
    profile = _get_profile_or_404(request.user)
    return _render_intake_form(
        request,
        intake_request,
        profile,
        form_action_url=reverse("intake_forms:fill_intake_form", args=[token]),
    )


@require_http_methods(["GET", "POST"])
@student_required
def student_intake_form(request: HttpRequest, request_id: int) -> HttpResponse:
    """Student fills an owned intake request from the authenticated dashboard."""
    profile = _get_profile_or_404(request.user)
    intake_request = get_object_or_404(
        IntakeFormRequest.objects.select_related(
            "form", "program_application__student__user",
        ),
        pk=request_id,
        program_application__student=profile,
    )
    return _render_intake_form(
        request,
        intake_request,
        profile,
        form_action_url=reverse("intake_forms:student_intake_form", args=[request_id]),
    )


def _render_intake_form(
    request: HttpRequest,
    intake_request: IntakeFormRequest,
    profile,
    *,
    form_action_url: str,
) -> HttpResponse:
    """Render or submit an intake request after its ownership is established."""

    application = intake_request.program_application
    if application.student != profile:
        messages.error(request, _("This intake form link is not for your account."))
        return redirect("students:applications")

    if intake_request.status == INTAKE_REQUEST_STATUS_SUBMITTED:
        return render(request, "ui/pages/student/intake_form.html", {
            "submitted": True,
            "intake_request": intake_request,
            "application": application,
        })

    if intake_request.status == INTAKE_REQUEST_STATUS_CANCELLED:
        messages.error(request, _("This intake form request has been cancelled."))
        return redirect("students:applications")

    if not intake_request.form.is_open():
        return render(request, "ui/pages/student/intake_form.html", {
            "closed": True,
            "intake_request": intake_request,
            "application": application,
        })

    if intake_request.expires_at <= timezone.now():
        return render(request, "ui/pages/student/intake_form.html", {
            "expired": True,
            "intake_request": intake_request,
            "application": application,
        })

    form_instance = intake_request.form
    active_fields = form_instance.fields.filter(is_active=True).order_by("order")

    if request.method == "POST":
        submit_form = IntakeFormSubmitForm(form_instance, request.POST, prefix="intake")
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
                return redirect("students:applications")
            except (ValueError, PermissionError) as exc:
                messages.error(request, str(exc))
        else:
            for _field, errs in submit_form.errors.items():
                for err in errs:
                    messages.error(request, err)
    else:
        submit_form = IntakeFormSubmitForm(form_instance, prefix="intake")

    return render(request, "ui/pages/student/intake_form.html", {
        "intake_request": intake_request,
        "application": application,
        "form_instance": form_instance,
        "active_fields": active_fields,
        "form": submit_form,
        "form_action_url": form_action_url,
    })
