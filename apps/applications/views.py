"""Public registration and admin review views."""

from __future__ import annotations

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

from apps.academics.models import Cohort
from apps.core.htmx import is_htmx
from apps.core.pagination import paginate
from apps.core.permissions import ROLE_SENIOR_STAFF, is_admin, role_required, safe_int
from apps.entrance_exams.services import assign_exam_for_application
from apps.intake_forms.selectors import get_application_intake_state  # noqa: E402
from apps.intake_forms.services import request_intake_form_for_application  # noqa: E402
from apps.payments.services import grant_payment_waiver, revoke_payment_waiver
from apps.storage.services import StorageError, storage_service
from apps.students.services import get_open_apply_targets

from .constants import (
    DECISION_ACTION_ACCEPT,
    DECISION_ACTION_ACCOUNT_ACTIVATED,
    DECISION_ACTION_ACTIVATE,
    DECISION_ACTION_ENTRANCE_SUBMITTED,
    DECISION_ACTION_INVITED_TO_ENTRANCE,
    DECISION_ACTION_NOTE,
    DECISION_ACTION_PAYMENT_PENDING,
    DECISION_ACTION_REJECT,
    DECISION_ACTION_REQUEST_DOCUMENTS,
    DECISION_ACTION_REQUEST_INTERVIEW,
    DECISION_ACTION_REQUEST_SURVEY,
    DECISION_ACTION_REVERT,
    DECISION_ACTION_SUBMIT_DOCUMENTS,
    DECISION_ACTION_SUBMIT_SURVEY,
    DOCUMENT_REQUEST_STATUS_CANCELLED,
    DOCUMENT_REQUEST_STATUS_REQUESTED,
    DOCUMENT_REQUEST_STATUS_SUBMITTED,
    PROGRAM_APP_STATUS_ACCEPTED,
    PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED,
    PROGRAM_APP_STATUS_ACTIVATED,
    PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED,
    PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED,
    PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
    PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
    PROGRAM_APP_STATUS_INVITED,
    PROGRAM_APP_STATUS_PAYMENT_PENDING,
    PROGRAM_APP_STATUS_READY_FOR_ACTIVATION,
    PROGRAM_APP_STATUS_REJECTED,
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_APP_STATUS_SURVEY_REQUESTED,
    PROGRAM_APP_STATUS_SURVEY_SUBMITTED,
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
    ACTION_ACCEPT,
    ACTION_ACTIVATE,
    ACTION_GRANT_WAIVER,
    ACTION_INVITE_TO_ENTRANCE,
    ACTION_MARK_PAID,
    ACTION_REJECT,
    ACTION_REQUEST_DOCUMENTS,
    ACTION_REQUEST_INTAKE,
    ACTION_REQUEST_INTERVIEW,
    RegistrationData,
    accept_program_application,
    activate_application_if_ready,
    allowed_application_actions,
    get_active_application_waiver,
    has_application_paid_record,
    invite_to_entrance_exam,
    mark_application_paid,
    reject_program_application,
    request_interview_program_application,
    revert_program_application_decision,
    send_post_activation_application_email,
    submit_registration,
)

PUBLIC_REGISTRATION_SESSION_KEY = "registration_email"

REGISTRATION_STEPS = (
    {
        "title": _("Personal"),
        "fields": ("full_name", "date_of_birth", "gender", "address", "country_code", "national_id"),
    },
    {
        "title": _("Contact & Work"),
        "fields": ("phone", "email", "education", "specialization", "employer", "current_job"),
    },
    {
        "title": _("Church & Program"),
        "fields": ("church", "priest_name", "priest_contact"),
    },
)

REGISTRATION_PROGRAM_FIELDS = ("apply_program", "program_type", "diploma", "delivery_mode", "program_target")


def _registration_error_step(error_fields: set) -> int:
    """Return the 1-based step that contains the first error field."""
    for index, step in enumerate(REGISTRATION_STEPS, start=1):
        if error_fields & set(step["fields"]):
            return index
    if error_fields & set(REGISTRATION_PROGRAM_FIELDS):
        return len(REGISTRATION_STEPS)
    return 1


@require_http_methods(["GET"])
def program_targets(request: HttpRequest) -> HttpResponse:
    """Return program target fragments for HTMX."""
    if not request.headers.get("HX-Request"):
        raise Http404
    profile = getattr(request.user, "student_profile", None)
    kind = str(request.GET.get("kind") or request.GET.get("program_type") or "diploma").strip()
    page = safe_int(request.GET.get("page", ""), default=1) or 1
    page = max(page, 1)
    targets = get_open_apply_targets(kind, profile=profile, page=page, page_size=10)
    field_name = request.GET.get("field_name", "program_target")
    select_id = request.GET.get("select_id", "program-option")
    if field_name not in ("target_id", "program_target"):
        field_name = "program_target"
    if select_id not in ("target-id", "program-option"):
        select_id = "program-option"
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
    if load_url and (field_name != "program_target" or select_id != "program-option"):
        load_url = f"{load_url}&field_name={field_name}&select_id={select_id}"
    return render(
        request,
        "ui/fragments/shared/_program_options.html",
        {
            "options": options,
            "placeholder": placeholder,
            "page": page,
            "loader_id": loader_id,
            "load_url": load_url,
            "has_more": targets["has_more"],
            "field_name": field_name,
            "select_id": select_id,
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
                    program_type=program_data["program_type"],
                    cohort_id=program_data["cohort_id"],
                    course_id=program_data["course_id"],
                    delivery_mode=program_data["delivery_mode"],
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
    error_fields = set(form.errors)
    registration_error_step = _registration_error_step(error_fields)
    return render(
        request,
        "ui/pages/public/register.html",
        {
            "form": form,
            "registration_error_step": registration_error_step,
            "registration_steps": REGISTRATION_STEPS,
            "registration_program_fields": REGISTRATION_PROGRAM_FIELDS,
        },
    )


@require_http_methods(["GET"])
def registration_success(request: HttpRequest) -> HttpResponse:
    email = request.session.pop(PUBLIC_REGISTRATION_SESSION_KEY, None)
    return render(request, "ui/pages/public/registration_success.html", {"email": email})


# ---------------------------------------------------------------------------
# Program Application admin
# ---------------------------------------------------------------------------


REVIEW_QUEUE_STATUSES = (
    PROGRAM_APP_STATUS_SUBMITTED,
    PROGRAM_APP_STATUS_ENTRANCE_SUBMITTED,
    PROGRAM_APP_STATUS_INTERVIEW_REQUESTED,
)


def review_queue_applications():
    """Applications awaiting a staff decision (shared queue predicate, A8).

    Both the admin review queue and the dashboard count must use this exact
    queryset so the number on the dashboard matches the queue it links to.
    """
    return ProgramApplication.objects.filter(status__in=REVIEW_QUEUE_STATUSES)


def _decision_unavailable_reason(application) -> str | None:
    """Explain why no accept/reject decision is possible right now (A10)."""
    if application.status == PROGRAM_APP_STATUS_ACCEPTED:
        return _("This application has already been accepted.")
    if application.status == PROGRAM_APP_STATUS_REJECTED:
        return _("This application has already been rejected.")
    if application.status == PROGRAM_APP_STATUS_READY_FOR_ACTIVATION:
        return _("This application is ready for activation.")
    if application.status == PROGRAM_APP_STATUS_ACTIVATED:
        return _("This application has already been activated.")
    if application.status in {
        PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED,
        PROGRAM_APP_STATUS_DOCUMENTS_SUBMITTED,
        PROGRAM_APP_STATUS_SURVEY_REQUESTED,
        PROGRAM_APP_STATUS_SURVEY_SUBMITTED,
    }:
        return _("The application must complete its requested documents and survey steps first.")
    if application.status == PROGRAM_APP_STATUS_PAYMENT_PENDING:
        return _("The application is awaiting payment confirmation.")
    return _("No decision is available for the current application status.")


@require_http_methods(["GET"])
@role_required(*ROLE_SENIOR_STAFF)
def program_application_list(request: HttpRequest) -> HttpResponse:
    """Admin list of program applications, with status filter."""
    status_filter = request.GET.get("status", "")
    program_type_filter = request.GET.get("program_type", "")
    action_filter = request.GET.get("action_needed", "")
    from_filter = request.GET.get("from", "")
    to_filter = request.GET.get("to", "")
    cohort_filter = request.GET.get("cohort", "")
    student_filter = request.GET.get("student_id", "")
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
    if cohort_filter:
        applications = applications.filter(cohort_id=cohort_filter)
    if student_filter and student_filter.isdigit():
        applications = applications.filter(student_id=int(student_filter))
    if action_filter == "review":
        applications = applications.filter(status__in=REVIEW_QUEUE_STATUSES)
    elif action_filter == "payment":
        applications = applications.filter(status=PROGRAM_APP_STATUS_PAYMENT_PENDING)
    elif action_filter == "documents":
        applications = applications.filter(status__in=(PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_DOCUMENTS_REQUESTED))
    if from_filter:
        applications = applications.filter(created_at__date__gte=from_filter)
    if to_filter:
        applications = applications.filter(created_at__date__lte=to_filter)
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
        {
            "name": "action_needed",
            "label": _("Action needed"),
            "value": action_filter,
            "options": [
                {"value": "review", "label": _("Review decision"), "selected": action_filter == "review"},
                {"value": "payment", "label": _("Payment"), "selected": action_filter == "payment"},
                {"value": "documents", "label": _("Documents"), "selected": action_filter == "documents"},
            ],
        },
        {
            "name": "cohort",
            "label": _("Cohort / program"),
            "value": cohort_filter,
            "options": [
                {"value": str(cohort.pk), "label": str(cohort), "selected": cohort_filter == str(cohort.pk)}
                for cohort in Cohort.objects.select_related("diploma").order_by("-start_date")
            ],
        },
    ]
    page_obj, pagination_qs = paginate(request, applications, per_page=15)
    context = {
        "page_obj": page_obj,
        "pagination_qs": pagination_qs,
        "search_value": q,
        "filters": filters,
        "from_filter": from_filter,
        "to_filter": to_filter,
    }
    if is_htmx(request):
        return render(request, "ui/fragments/admin/_program_table.html", context)
    return render(request, "ui/pages/admin/program_list.html", context)


@require_http_methods(["GET", "POST"])
@role_required(*ROLE_SENIOR_STAFF)
def program_application_detail(request: HttpRequest, application_id: int) -> HttpResponse:
    """Admin detail/edit view for a ProgramApplication with actions."""
    application = get_object_or_404(
        ProgramApplication.objects.select_related(
            "student__user", "reviewed_by", "cohort__diploma", "course"
        ).prefetch_related("entrance_attempts", "notes__actor"),
        pk=application_id,
    )
    allowed_actions = allowed_application_actions(application)

    if request.method == "POST":
        action = request.POST.get("action")
        send_email = request.POST.get("send_email") == "on"

        if action == "accept" and ACTION_ACCEPT in allowed_actions:
            notes = (request.POST.get("admin_notes") or "").strip()
            try:
                accept_program_application(application, request.user, notes=notes, send_email=send_email)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("Application accepted."))
            return redirect("applications:program_detail", application_id=application.id)

        if action == "reject" and ACTION_REJECT in allowed_actions:
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

        if action == "request_documents" and is_admin(request.user) and ACTION_REQUEST_DOCUMENTS in allowed_actions:
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
            if ACTION_REQUEST_INTAKE not in allowed_actions:
                messages.error(request, _("Intake forms can only be requested for applications that are not yet rejected or activated."))
            else:
                try:
                    request_intake_form_for_application(application, request.user, send_email=True)
                except ValueError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.success(request, _("Intake request created. Survey email queued."))
            return redirect("applications:program_detail", application_id=application.id)

        if action == "request_interview" and ACTION_REQUEST_INTERVIEW in allowed_actions:
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

        if action == "invite_to_entrance" and (ACTION_INVITE_TO_ENTRANCE in allowed_actions or application.status == PROGRAM_APP_STATUS_INVITED):
            send_email = request.POST.get("send_email") == "on"
            try:
                if application.status in (PROGRAM_APP_STATUS_SUBMITTED, PROGRAM_APP_STATUS_ACCOUNT_ACTIVATED):
                    invite_to_entrance_exam(application, request.user)
                assign_exam_for_application(application)
                if send_email:
                    send_post_activation_application_email(application, request.user, force_resend=True)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(request, _("Entrance exam assigned." if not send_email else "Entrance exam assigned. Invitation email queued."))
            return redirect("applications:program_detail", application_id=application.id)

        if action == "grant_waiver" and is_admin(request.user):
            try:
                waiver = grant_payment_waiver(
                    application,
                    request.user,
                    reason=request.POST.get("waiver_reason", ""),
                )
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                messages.success(
                    request,
                    _("Payment waiver granted and activation attempted."),
                )
            return redirect("applications:program_detail", application_id=application.id)

        if action == "revoke_waiver" and is_admin(request.user):
            waiver = get_active_application_waiver(application)
            if waiver is None:
                messages.error(request, _("No active payment waiver to revoke."))
            else:
                try:
                    revoke_payment_waiver(
                        waiver,
                        request.user,
                        reason=request.POST.get("waiver_revoke_reason", ""),
                    )
                except ValueError as exc:
                    messages.error(request, str(exc))
                else:
                    messages.info(request, _("Payment waiver revoked."))
            return redirect("applications:program_detail", application_id=application.id)

        if action == "activate" and is_admin(request.user):
            try:
                result = activate_application_if_ready(application, actor=request.user)
            except ValueError as exc:
                messages.error(request, str(exc))
            else:
                if result.activated:
                    messages.success(request, _("Application activated."))
                else:
                    messages.error(request, _("Activation failed: %(reason)s") % {"reason": ", ".join(result.reasons)})
            return redirect("applications:program_detail", application_id=application.id)

        messages.error(request, _("Action not allowed for the current status."))

    decision_action_tones = {
        DECISION_ACTION_ACCOUNT_ACTIVATED: "success",
        DECISION_ACTION_PAYMENT_PENDING: "warning",
        DECISION_ACTION_INVITED_TO_ENTRANCE: "info",
        DECISION_ACTION_ENTRANCE_SUBMITTED: "info",
        DECISION_ACTION_REQUEST_INTERVIEW: "info",
        DECISION_ACTION_ACCEPT: "success",
        DECISION_ACTION_REJECT: "danger",
        DECISION_ACTION_REVERT: "warning",
        DECISION_ACTION_NOTE: "neutral",
        DECISION_ACTION_REQUEST_DOCUMENTS: "warning",
        DECISION_ACTION_SUBMIT_DOCUMENTS: "success",
        DECISION_ACTION_REQUEST_SURVEY: "warning",
        DECISION_ACTION_SUBMIT_SURVEY: "success",
        DECISION_ACTION_ACTIVATE: "success",
    }

    decisions, decisions_qs = paginate(
        request,
        application.decisions.select_related("actor").order_by("-created_at"),
        per_page=15,
    )
    status_labels = dict(ProgramApplication._meta.get_field("status").choices)
    decision_display_rows = [
        (
            decision,
            decision.get_action_display(),
            decision_action_tones.get(decision.action, "neutral"),
            status_labels.get(decision.old_status, _("Unknown")),
            status_labels.get(decision.new_status, _("Unknown")),
        )
        for decision in decisions
    ]
    notes = application.notes.all()[:20]
    is_paid = has_application_paid_record(application)
    active_waiver = get_active_application_waiver(application)
    is_waived = active_waiver is not None
    can_record_payment = (
        is_admin(request.user)
        and not is_paid
        and not is_waived
        and ACTION_MARK_PAID in allowed_actions
    )

    # Document request state
    doc_request = get_latest_document_request(application)
    doc_upload = get_application_document_upload(application)
    request_doc_form = None
    if is_admin(request.user) and ACTION_REQUEST_DOCUMENTS in allowed_actions:
        request_doc_form = AdminDocumentRequestForm()

    # Intake form state
    intake_state = get_application_intake_state(application)

    return render(
        request,
        "ui/pages/admin/program_detail.html",
        {
            "application": application,
            "decisions": decisions,
            "decision_display_rows": decision_display_rows,
            "pagination_qs": decisions_qs,
            "notes": notes,
            "is_paid": is_paid,
            "active_waiver": active_waiver,
            "is_waived": is_waived,
            "can_record_payment": can_record_payment,
            "can_activate": is_admin(request.user) and ACTION_ACTIVATE in allowed_actions,
            "can_grant_waiver": (
                is_admin(request.user)
                and not is_waived
                and not is_paid
                and ACTION_GRANT_WAIVER in allowed_actions
            ),
            "can_revoke_waiver": is_admin(request.user) and is_waived,
            "can_show_revert": (
                application.status in {PROGRAM_APP_STATUS_ACCEPTED, PROGRAM_APP_STATUS_REJECTED}
                and not application.entrance_attempts.all()
            ),
            "doc_request": doc_request,
            "doc_upload": doc_upload,
            "doc_uploads": list(
                ApplicationDocumentUpload.objects.filter(
                    request__program_application=application,
                ).select_related("request").order_by("-submitted_at")
            ),
            "request_doc_form": request_doc_form,
            "intake_state": intake_state,
            "can_accept": ACTION_ACCEPT in allowed_actions,
            "can_reject": ACTION_REJECT in allowed_actions,
            "can_invite_to_entrance": ACTION_INVITE_TO_ENTRANCE in allowed_actions,
            "can_request_interview": ACTION_REQUEST_INTERVIEW in allowed_actions,
            "decision_reason": _decision_unavailable_reason(application)
            if not (ACTION_ACCEPT in allowed_actions or ACTION_REJECT in allowed_actions)
            else None,
        },
    )


# ---------------------------------------------------------------------------
# Document upload (student)
# ---------------------------------------------------------------------------


def _render_document_upload(request: HttpRequest, doc_request) -> HttpResponse:
    """Shared document-upload GET/POST handling and render for a doc request.

    Used by the emailed token link and the authenticated portal route.
    """
    application = doc_request.program_application
    if doc_request.status == DOCUMENT_REQUEST_STATUS_SUBMITTED:
        messages.info(request, _("Documents have already been submitted for this request."))
        return render(request, "ui/pages/public/document_upload.html", {"submitted": True, "doc_request": doc_request, "application": application})

    if doc_request.status == DOCUMENT_REQUEST_STATUS_CANCELLED:
        messages.error(request, _("This document request has been cancelled."))
        return render(request, "ui/pages/public/document_upload.html", {"cancelled": True, "doc_request": doc_request, "application": application})

    if doc_request.expires_at <= timezone.now():
        messages.error(request, _("This document upload link has expired."))
        return render(request, "ui/pages/public/document_upload.html", {"expired": True, "doc_request": doc_request, "application": application})

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
                return redirect("students:applications")
            except (ValueError, StorageError) as exc:
                messages.error(request, str(exc))
        else:
            for _field, errs in form.errors.items():
                for err in errs:
                    messages.error(request, err)
    else:
        form = StudentDocumentUploadForm()

    return render(request, "ui/pages/public/document_upload.html", {
        "form": form,
        "doc_request": doc_request,
        "application": application,
    })


@require_http_methods(["GET", "POST"])
@login_required
def document_upload(request: HttpRequest, token: str) -> HttpResponse:
    """Student document upload via expiring token link."""
    doc_request = get_document_request_by_token(token)
    if not doc_request:
        messages.error(request, _("Invalid or expired document upload link."))
        return redirect("students:applications")

    # Verify ownership
    if doc_request.program_application.student.user != request.user:
        messages.error(request, _("This document upload link is not for your account."))
        return redirect("students:applications")

    return _render_document_upload(request, doc_request)


@require_http_methods(["GET", "POST"])
@login_required
def my_document_upload(request: HttpRequest, application_id: int) -> HttpResponse:
    """Authenticated document upload for the student's own application.

    Lets a student reach an open document request from their applications
    portal without the emailed token link.
    """
    application = get_object_or_404(ProgramApplication, id=application_id)
    if application.student.user != request.user:
        raise PermissionDenied
    doc_request = application.document_requests.select_related("program_application").filter(
        status=DOCUMENT_REQUEST_STATUS_REQUESTED,
        expires_at__gt=timezone.now(),
    ).first()
    if not doc_request:
        messages.info(request, _("No open document request for this application."))
        return redirect("students:applications")
    return _render_document_upload(request, doc_request)


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
        return redirect("students:applications")


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
        return redirect("students:applications")


def _can_download_document(user, application) -> bool:
    """Check if user can download documents for this application."""
    if is_admin(user):
        return True
    return hasattr(user, "student_profile") and user.student_profile == application.student


def _get_profile_or_404(user):
    if not hasattr(user, "student_profile"):
        raise Http404(_("No student profile."))
    return user.student_profile
