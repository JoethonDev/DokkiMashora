"""Public registration and admin review views."""

from __future__ import annotations

from datetime import date

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpRequest, HttpResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_http_methods

from apps.core.htmx import is_htmx
from apps.core.pagination import paginate
from apps.core.permissions import is_admin
from apps.intake_forms.selectors import get_application_intake_state  # noqa: E402
from apps.intake_forms.services import request_intake_form_for_application  # noqa: E402
from apps.storage.services import StorageError, storage_service
from apps.students.services import get_open_apply_targets

from .constants import (
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED,
    PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED,
    PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
    PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
    PROGRAM_APP_STATUS_PAYMENT_PENDING,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_TYPE_COURSE,
)
from .documents import (
    get_application_document_upload,
    get_document_request_by_token,
    get_latest_document_request,
    request_documents_program_application,
    submit_application_documents,
)
from .forms import AdminDocumentRequestForm, RegistrationForm, StudentDocumentUploadForm
from .models import ApplicationDocumentUpload, ProgramApplication
from .services import (
    RegistrationData,
    accept_program_application,
    has_application_paid_record,
    mark_application_paid,
    reject_program_application,
    request_interview_program_application,
    revert_program_application_decision,
    submit_registration,
)
from apps.entrance_exams.services import assign_exam_for_application
from .services import invite_to_entrance_exam, send_post_activation_application_email

PUBLIC_REGISTRATION_SESSION_KEY = "registration_email"


@require_http_methods(["GET"])
def program_targets(request: HttpRequest) -> HttpResponse:
    """Return program target fragments for HTMX."""
    profile = getattr(request.user, "student_profile", None)
    kind = str(request.GET.get("kind", "diploma")).strip()
    page = int(request.GET.get("page", 1) or 1)
    targets = get_open_apply_targets(kind, profile=profile, page=page, page_size=10)
    if kind == "course":
        options = [(f"course_{c.pk}", c.title) for c in targets["items"]]
        placeholder = _("Select a course")
        loader_id = "course-loader"
        load_url = f"{request.path}?kind=course&page={targets['next_page']}" if targets["has_more"] and targets["next_page"] else ""
    else:
        options = [(str(c.pk), f"{c.diploma.title} — {c.name}") for c in targets["items"]]
        placeholder = _("Select a diploma")
        loader_id = "diploma-loader"
        load_url = f"{request.path}?kind=diploma&page={targets['next_page']}" if targets["has_more"] and targets["next_page"] else ""
    return render(
        request,
        "applications/partials/_program_options.html",
        {
            "options": options,
            "placeholder": placeholder,
            "page": page,
            "loader_id": loader_id,
            "load_url": load_url,
            "has_more": targets["has_more"],
        },
    )


@require_http_methods(["GET", "POST"])
def register(request: HttpRequest) -> HttpResponse:
    """Public registration form. Authenticated users go to their dashboard."""
    if request.user.is_authenticated:
        return redirect("accounts:post_login")
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            with transaction.atomic():
                program_data = form.get_program_data()
                data = RegistrationData(
                    email=form.cleaned_data["email"],
                    full_name=form.cleaned_data["full_name"],
                    date_of_birth=form.cleaned_data["date_of_birth"],
                    gender=form.cleaned_data["gender"],
                    address=form.cleaned_data["address"],
                    phone=form.cleaned_data["phone"],
                    national_id=form.cleaned_data["national_id"],
                    country_code=form.cleaned_data.get("country_code", "EG"),
                    education=form.cleaned_data["education"],
                    specialization=form.cleaned_data.get("specialization", ""),
                    employer=form.cleaned_data.get("employer", ""),
                    current_job=form.cleaned_data.get("current_job", ""),
                    church=form.cleaned_data.get("church", ""),
                    priest_name=form.cleaned_data.get("priest_name", ""),
                    priest_contact=form.cleaned_data.get("priest_contact", ""),
                    age=(date.today() - form.cleaned_data["date_of_birth"]).days // 365,
                    program_type=program_data["program_type"],
                    cohort_id=program_data["cohort_id"],
                    course_id=program_data["course_id"],
                )
                _profile, _program_application = submit_registration(data)
            request.session[PUBLIC_REGISTRATION_SESSION_KEY] = form.cleaned_data["email"]
            messages.success(request, _("Account created! Check your email for the activation link."))
            return redirect("applications:registration_success")
    else:
        program_type = request.GET.get("program_type", "")
        target_id = request.GET.get("target_id", "")
        initial = {}
        if program_type == "diploma" and target_id.isdigit():
            initial = {"apply_program": True, "program_type": "diploma", "diploma": target_id}
        elif program_type == "course" and target_id.startswith("course_"):
            initial = {"apply_program": True, "program_type": "course", "program_target": target_id}
        form = RegistrationForm(initial=initial)
    return render(request, "applications/register.html", {"form": form})


@require_http_methods(["GET"])
def registration_success(request: HttpRequest) -> HttpResponse:
    email = request.session.pop(PUBLIC_REGISTRATION_SESSION_KEY, None)
    return render(request, "applications/registration_success.html", {"email": email})


# ---------------------------------------------------------------------------
# Program Application admin
# ---------------------------------------------------------------------------


def _accept_allowed(status, program_type=None):
    base = {PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED, PROGRAM_APP_STATUS_INTERVIEW_REQUESTED, PROGRAM_APP_STATUS_PAYMENT_PENDING}
    if program_type == PROGRAM_TYPE_COURSE:
        base |= {PROGRAM_APP_STATUS_SUBMITTED, PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED}
    return status in base


def _reject_allowed(status, program_type=None):
    base = {PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED, PROGRAM_APP_STATUS_INTERVIEW_REQUESTED}
    if program_type == PROGRAM_TYPE_COURSE:
        base |= {PROGRAM_APP_STATUS_SUBMITTED, PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED, PROGRAM_APP_STATUS_PAYMENT_PENDING}
    return status in base


@require_http_methods(["GET"])
@login_required
def program_application_list(request: HttpRequest) -> HttpResponse:
    """Admin list of program applications, with status filter."""
    if not is_admin(request.user):
        raise PermissionDenied
    status_filter = request.GET.get("status", "")
    program_type_filter = request.GET.get("program_type", "")
    q = request.GET.get("q", "")
    applications = ProgramApplication.objects.select_related(
        "student__user", "reviewed_by", "cohort__diploma", "course"
    ).order_by("-created_at")
    if q:
        applications = applications.filter(
            Q(student__full_name__icontains=q) | Q(student__user__email__icontains=q)
        )
    if status_filter:
        applications = applications.filter(status=status_filter)
    if program_type_filter:
        applications = applications.filter(program_type=program_type_filter)
    filters = [
        {
            "name": "status",
            "label": _("Status"),
            "value": status_filter,
            "options": [{"value": v, "label": _(label_text), "selected": status_filter == v}
                        for v, label_text in ProgramApplication._meta.get_field("status").choices],
        },
        {
            "name": "program_type",
            "label": _("Type"),
            "value": program_type_filter,
            "options": [{"value": v, "label": _(label_text), "selected": program_type_filter == v}
                        for v, label_text in ProgramApplication._meta.get_field("program_type").choices],
        },
    ]
    page_obj, pagination_qs = paginate(request, applications, per_page=15)
    context = {
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "search_value": q,
        "filters": filters,
    }
    if is_htmx(request):
        return render(request, "applications/partials/_program_list_table.html", context)
    return render(request, "applications/admin_program_list.html", context)


@require_http_methods(["GET", "POST"])
@login_required
def program_application_detail(request: HttpRequest, application_id: int) -> HttpResponse:
    """Admin detail/edit view for a ProgramApplication with actions."""
    if not is_admin(request.user):
        raise PermissionDenied
    application = get_object_or_404(
        ProgramApplication.objects.select_related(
            "student__user", "reviewed_by", "cohort__diploma", "course"
        ).prefetch_related("entrance_attempts", "notes__actor"),
        pk=application_id,
    )

    if request.method == "POST":
        action = request.POST.get("action")
        send_email = request.POST.get("send_email") == "on"

        if action == "accept" and _accept_allowed(application.status, application.program_type):
            notes = (request.POST.get("admin_notes") or "").strip()
            try:
                accept_program_application(application, request.user, notes=notes, send_email=send_email)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("Application accepted."))
            return redirect("applications:program_detail", application_id=application.id)

        if action == "reject" and _reject_allowed(application.status, application.program_type):
            reason = (request.POST.get("rejection_reason") or "").strip()
            if not reason:
                messages.error(request, _("Rejection reason is required."))
                return redirect("applications:program_detail", application_id=application.id)
            admin_notes = (request.POST.get("admin_notes") or "").strip()
            try:
                reject_program_application(application, request.user, reason=reason, notes=admin_notes, send_email=send_email)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.warning(request, _("Application rejected."))
            return redirect("applications:program_detail", application_id=application.id)

        if action == "revert" and application.status in {PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_REJECTED}:
            try:
                revert_program_application_decision(
                    application,
                    request.user,
                    reason=(request.POST.get("revert_reason") or "").strip(),
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.info(request, _("Application reverted."))
                return redirect("applications:program_detail", application_id=application.id)

        if action == "mark_paid" and is_admin(request.user):
            try:
                mark_application_paid(
                    application,
                    request.user,
                    amount=request.POST.get("amount", 0),
                    external_reference=request.POST.get("external_reference", ""),
                    notes=request.POST.get("notes", ""),
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("Payment recorded and activation attempted."))
            return redirect("applications:program_detail", application_id=application.id)

        if action == "request_documents" and is_admin(request.user) and application.status in (PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED) and application.program_type == "diploma":
            form = AdminDocumentRequestForm(request.POST)
            if form.is_valid():
                try:
                    request_documents_program_application(
                        application,
                        request.user,
                        expires_at=form.cleaned_data["expires_at"],
                        notes=form.cleaned_data.get("notes", ""),
                        send_email=form.cleaned_data.get("send_email", True),
                    )
                except ValueError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(request, _("Documents requested."))
            else:
                for _field, errs in form.errors.items():
                    for err in errs:
                        messages.error(request, err)
            return redirect("applications:program_detail", application_id=application.id)

        if action == "request_intake" and is_admin(request.user):
            try:
                request_intake_form_for_application(application, request.user, send_email=True)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("Intake request sent."))
            return redirect("applications:program_detail", application_id=application.id)

        if action == "request_interview" and application.status == PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED:
            interview_at_str = (request.POST.get("interview_at") or "").strip()
            phone_number = (request.POST.get("phone_number") or "").strip()
            expires_at_str = (request.POST.get("expires_at") or "").strip()
            notes = (request.POST.get("notes") or "").strip()
            interview_at = None
            if interview_at_str:
                try:
                    dt = timezone.datetime.fromisoformat(interview_at_str)
                    interview_at = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
                except (ValueError, TypeError):
                    messages.error(request, _("Invalid interview date/time format."))
                    return redirect("applications:program_detail", application_id=application.id)
            expires_at = None
            if expires_at_str:
                try:
                    dt = timezone.datetime.fromisoformat(expires_at_str)
                    expires_at = timezone.make_aware(dt) if timezone.is_naive(dt) else dt
                except (ValueError, TypeError):
                    messages.error(request, _("Invalid expiry date/time format."))
                    return redirect("applications:program_detail", application_id=application.id)
            if not expires_at:
                messages.error(request, _("Expiry time is required for interview request."))
                return redirect("applications:program_detail", application_id=application.id)
            try:
                request_interview_program_application(
                    application, request.user,
                    interview_at=interview_at,
                    phone_number=phone_number,
                    expires_at=expires_at,
                    notes=notes,
                    send_email=send_email,
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("Interview requested."))
            return redirect("applications:program_detail", application_id=application.id)

        if action == "invite_to_entrance" and application.program_type == "diploma" and application.status in {"submitted", "account_activated", "invited_to_entrance"}:
            send_email = request.POST.get("send_email") == "on"
            try:
                if application.status in {"submitted", "account_activated"}:
                    invite_to_entrance_exam(application, request.user)
                assign_exam_for_application(application)
                if send_email:
                    send_post_activation_application_email(application, request.user, force_resend=True)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("Entrance exam assigned." if not send_email else "Entrance exam assigned and invitation sent."))
            return redirect("applications:program_detail", application_id=application.id)

        messages.error(request, _("Action not allowed for the current status."))

    decisions, decisions_qs = paginate(
        request,
        application.decisions.select_related("actor").order_by("-created_at"),
        per_page=15,
    )
    notes = application.notes.all()[:20]
    is_paid = has_application_paid_record(application)

    # Document request state
    doc_request = get_latest_document_request(application)
    doc_upload = get_application_document_upload(application)
    request_doc_form = None
    if is_admin(request.user) and application.status in (PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED) and application.program_type == "diploma":
        request_doc_form = AdminDocumentRequestForm()

    # Intake form state
    intake_state = get_application_intake_state(application)

    return render(
        request,
        "applications/admin_program_detail.html",
        {
            "application": application,
            "decisions": decisions,
            "pagination_qs": decisions_qs,
            "notes": notes,
            "is_paid": is_paid,
            "doc_request": doc_request,
            "doc_upload": doc_upload,
            "request_doc_form": request_doc_form,
            "intake_state": intake_state,
        },
    )


# ---------------------------------------------------------------------------
# Document upload (student)
# ---------------------------------------------------------------------------


@require_http_methods(["GET", "POST"])
@login_required
def document_upload(request: HttpRequest, token: str) -> HttpResponse:
    """Student document upload via expiring token link."""
    doc_request = get_document_request_by_token(token)
    if not doc_request:
        messages.error(request, _("Invalid or expired document upload link."))
        return redirect("students:application_history")

    application = doc_request.program_application

    # Verify ownership
    if doc_request.program_application.student.user != request.user:
        messages.error(request, _("This document upload link is not for your account."))
        return redirect("students:application_history")

    if doc_request.status == "submitted":
        messages.info(request, _("Documents have already been submitted for this request."))
        return render(request, "applications/document_upload.html", {"submitted": True, "doc_request": doc_request, "application": application})

    if doc_request.expires_at <= timezone.now():
        messages.error(request, _("This document upload link has expired."))
        return render(request, "applications/document_upload.html", {"expired": True, "doc_request": doc_request, "application": application})

    if request.method == "POST":
        form = StudentDocumentUploadForm(request.POST, request.FILES)
        if form.is_valid():
            try:
                submit_application_documents(
                    doc_request,
                    request.user,
                    persona_file=request.FILES["persona_image"],
                    attachment_files=request.FILES.getlist("attachments"),
                )
                messages.success(request, _("Documents submitted successfully."))
                return redirect("students:application_history")
            except (ValueError, StorageError) as exc:
                messages.error(request, str(exc))
        else:
            for _field, errs in form.errors.items():
                for err in errs:
                    messages.error(request, err)
    else:
        form = StudentDocumentUploadForm()

    return render(request, "applications/document_upload.html", {
        "form": form,
        "doc_request": doc_request,
        "application": application,
    })


# ---------------------------------------------------------------------------
# Protected document downloads
# ---------------------------------------------------------------------------


@login_required
@require_http_methods(["GET"])
def download_persona_image(request: HttpRequest, upload_id: int) -> HttpResponse:
    """Protected download for persona image. Student owner or admin can access."""
    upload = get_object_or_404(
        ApplicationDocumentUpload.objects.select_related("request__program_application__student__user"),
        pk=upload_id,
    )
    application = upload.request.program_application
    user = request.user

    if not _can_download_document(user, application):
        raise PermissionDenied

    try:
        url = storage_service.get_protected_download_url(
            upload.persona_image_storage_key,
            user=user,
            permission_fn=lambda u: _can_download_document(u, application),
        )
        return redirect(url)
    except StorageError as exc:
        messages.error(request, str(exc))
        return redirect("students:application_history")


@login_required
@require_http_methods(["GET"])
def download_attachments_pdf(request: HttpRequest, upload_id: int) -> HttpResponse:
    """Protected download for attachments PDF. Student owner or admin can access."""
    upload = get_object_or_404(
        ApplicationDocumentUpload.objects.select_related("request__program_application__student__user"),
        pk=upload_id,
    )
    application = upload.request.program_application
    user = request.user

    if not _can_download_document(user, application):
        raise PermissionDenied

    try:
        url = storage_service.get_protected_download_url(
            upload.attachments_pdf_storage_key,
            user=user,
            permission_fn=lambda u: _can_download_document(u, application),
        )
        return redirect(url)
    except StorageError as exc:
        messages.error(request, str(exc))
        return redirect("students:application_history")


def _can_download_document(user, application) -> bool:
    """Check if user can download documents for this application."""
    if is_admin(user):
        return True
    return hasattr(user, "student_profile") and user.student_profile == application.student


def _get_profile_or_404(user):
    if not hasattr(user, "student_profile"):
        raise Http404(_("No student profile."))
    return user.student_profile
