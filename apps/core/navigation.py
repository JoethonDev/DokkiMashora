from __future__ import annotations

from django.utils.translation import gettext_lazy as _

from apps.core.permissions import (
    is_admin,
    is_moderator,
    is_reviewer,
    is_secretary,
)


def _user_in_group(user, group_name: str) -> bool:
    return user.is_authenticated and user.groups.filter(name=group_name).exists()


def workspace_navigation(user):
    items = []

    section_keys = {
        _("Workspace"): "workspace",
        _("Learning"): "learning",
        _("Admissions"): "admissions",
        _("Administration"): "administration",
    }

    def _add(section, label, url_name, extra_active=None, keywords=""):
        active = [url_name]
        if extra_active:
            active.extend(extra_active)
        items.append({
            "section": section,
            "section_key": section_keys.get(section, section),
            "label": label,
            "url_name": url_name,
            "active_urls": active,
            "keywords": keywords,
        })

    can_admin = is_admin(user)
    can_moderator = is_moderator(user)
    can_reviewer = is_reviewer(user)
    can_secretary = is_secretary(user)
    is_super = user.is_superuser

    # --- Reviewer: anonymous Semester Exam grading only -----------------------
    # Locked nav: Dashboard + Grading assignments only. Grading lives under
    # "Learning" for grouping consistency, but the reviewer sees exactly two
    # destinations (Dashboard, Grading).
    if can_reviewer and not can_admin:
        _add(_("Workspace"), _("Dashboard"), "accounts:dashboard",
             ["accounts:dashboard", "accounts:dashboard_admin",
              "accounts:dashboard_moderator", "accounts:dashboard_secretary"])
        _add(_("Learning"), _("Grading assignments"), "assessments:reviewer_dashboard",
             ["assessments:reviewer_section_grading",
              "assessments:reviewer_online_grading"], "grade")
        return items

    # --- Moderator: assigned-cluster Homework grading only --------------------
    # Locked nav: Dashboard + Homework Review only.
    if can_moderator and not can_admin:
        _add(_("Workspace"), _("Dashboard"), "accounts:dashboard",
              ["accounts:dashboard", "accounts:dashboard_admin",
              "accounts:dashboard_moderator", "accounts:dashboard_secretary"])
        _add(_("Learning"), _("Homework review"), "assessments:admin_homework_list",
             ["assessments:admin_homework_grade_detail",
              "assessments:admin_grade_homework"], "grade")
        return items

    # --- Secretary: Attendance only ------------------------------------------
    if can_secretary and not can_admin:
        _add(_("Workspace"), _("Dashboard"), "accounts:dashboard",
              ["accounts:dashboard", "accounts:dashboard_admin",
              "accounts:dashboard_moderator", "accounts:dashboard_secretary"])
        _add(_("Learning"), _("Attendance"), "attendance:attendance_session_list",
             ["attendance:attendance_session_create",
              "attendance:attendance_session_detail",
              "attendance:attendance_session_open",
              "attendance:attendance_session_close",
              "attendance:attendance_session_records",
              "attendance:attendance_scan",
              "attendance:my_attendance"], "session")
        return items

    # --- Admin / Super Admin: full grouped workspace --------------------------
    # Locked groups: Workspace, Learning (incl assessments/grading), Admissions,
    # Administration. Navigation is display-only; backend permissions are unchanged.
    _add(_("Workspace"), _("Dashboard"), "accounts:dashboard",
              ["accounts:dashboard", "accounts:dashboard_admin", "accounts:dashboard_moderator",
          "accounts:dashboard_secretary",
           "accounts:dashboard"])

    if can_admin or is_super:
        _add(_("Learning"), _("Academics"), "academics:admin_diploma_list",
             ["academics:admin_diploma_create", "academics:admin_diploma_detail",
              "academics:admin_diploma_edit", "academics:admin_diploma_delete",
              "academics:admin_diploma_enrollment_list",
              "academics:admin_diploma_enrollment_update",
              "academics:admin_semester_create", "academics:admin_semester_edit",
              "academics:admin_cohort_create", "academics:admin_cohort_edit"],
             "diploma")
        _add(_("Learning"), _("Courses"), "academics:admin_course_list",
             ["academics:admin_course_create", "academics:admin_course_detail",
              "academics:admin_course_edit", "academics:admin_course_delete",
              "academics:admin_course_enrollment_list",
              "academics:admin_course_enrollment_migrate",
               "academics:admin_version_create", "academics:admin_version_detail",
               "academics:admin_version_delete",
              "academics:admin_version_toggle_active", "academics:admin_lecture_create",
              "academics:admin_lecture_edit", "academics:admin_lecture_delete",
              "academics:admin_lecture_preview"],
             "course")
    if can_admin or can_secretary or is_super:
        _add(_("Learning"), _("Attendance"), "attendance:attendance_session_list",
             ["attendance:attendance_session_create",
              "attendance:attendance_session_detail",
              "attendance:attendance_session_open",
              "attendance:attendance_session_close",
              "attendance:attendance_session_records",
"attendance:attendance_scan",
               "attendance:my_attendance"], "session")
    if can_admin or is_super:
        _add(_("Learning"), _("Assessments"), "assessments:admin_list",
             ["assessments:admin_create", "assessments:admin_detail",
              "assessments:admin_edit", "assessments:admin_preview",
              "assessments:admin_toggle", "assessments:admin_recalculate",
               "assessments:admin_scope_fields", "assessments:reviewer_dashboard",
               "assessments:reviewer_section_grading",
               "assessments:reviewer_online_grading",
               "assessments:admin_offline_grade_entry",
              "assessments:admin_offline_grade_section",
              "assessments:admin_generate_codes",
                "assessments:admin_export_offline_codes"], "assessment exam")
        _add(_("Learning"), _("Reviewer assignments"),
             "assessments:admin_reviewer_assignment_list",
             ["assessments:admin_reviewer_assignment_create"], "reviewer grading assignment")
    if can_admin or can_moderator or is_super:
        if can_moderator and not can_admin:
            _add(_("Learning"), _("Homework review"), "assessments:admin_homework_list",
                 ["assessments:admin_homework_list",
                  "assessments:admin_homework_grade_detail",
                  "assessments:admin_grade_homework"], "grade")
        else:
            _add(_("Learning"), _("Grading"), "assessments:admin_grade_list",
                 ["assessments:admin_grade_list", "assessments:admin_grade_detail",
                  "assessments:admin_release_grades", "assessments:admin_hide_grades",
                  "assessments:admin_grade_answer",
                  "assessments:admin_grade_homework",
                  "assessments:admin_homework_list",
                  "assessments:admin_homework_grade_detail",
                  "assessments:admin_toggle_grades",
                  "assessments:admin_exam_review_list",
                  "assessments:admin_exam_review_detail",
                  "assessments:admin_exam_review_submit",
                  "entrance_exams:admin_grade_list",
                  "entrance_exams:admin_grade_attempt"], "grade")
    if can_admin or is_super:
        _add(_("Learning"), _("Progression"), "progression:admin_dashboard",
             ["progression:admin_student_detail",
              "progression:admin_evaluate_semester",
              "progression:admin_promote",
              "progression:admin_promote_academic_year",
              "progression:admin_demote", "progression:admin_override",
              "progression:admin_lock", "progression:admin_unlock",
              "progression:admin_assign_retake",
              "progression:admin_clear_retake",
              "progression:admin_mark_complete",
               "progression:admin_mark_graduate",
               "progression:admin_component_list",
               "progression:admin_grade_sync",
               "progression:admin_semester_evaluation",
              "progression:admin_request_interview",
              "progression:admin_confirm_pass",
              "progression:admin_interview_decision",
               "progression:admin_create_rejoin_plan",
               "progression:admin_rejoin_plan_list",
               "progression:admin_rejoin_eligibility",
               "progression:admin_activate_rejoin_plan",
               "progression:admin_cancel_rejoin_plan",
               "progression:student_progress"], "progress")

    if can_admin or is_super:
        _add(_("Admissions"), _("Applications"), "applications:program_list",
             ["applications:program_detail"], "application")
        _add(_("Admissions"), _("Students"), "accounts:admin_student_list",
             ["accounts:admin_user_detail", "accounts:admin_user_edit",
              "accounts:admin_student_create", "accounts:admin_student_import",
              "accounts:admin_download_student_csv_template",
              "accounts:admin_student_import_detail",
              "accounts:admin_student_import_confirm"], "student")
        _add(_("Admissions"), _("Staff"), "accounts:admin_staff_list",
             ["accounts:admin_staff_create", "accounts:admin_staff_assignments",
              "accounts:admin_moderator_assignment_create",
              "accounts:admin_moderator_assignment_delete",
              "accounts:admin_student_cluster_create",
              "accounts:admin_student_cluster_membership_add",
              "accounts:admin_student_cluster_membership_remove"], "staff")
        _add(_("Admissions"), _("Entrance exams"), "entrance_exams:admin_list",
             ["entrance_exams:admin_cohorts_for_diploma",
              "entrance_exams:admin_create_exam", "entrance_exams:admin_detail",
              "entrance_exams:admin_edit_exam", "entrance_exams:admin_preview",
              "entrance_exams:admin_toggle_active",
              "entrance_exams:admin_finalize_pending",
              "entrance_exams:admin_add_question",
              "entrance_exams:admin_edit_question",
              "entrance_exams:admin_delete_question",
              "entrance_exams:admin_grade_list",
              "entrance_exams:admin_grade_attempt",
              "entrance_exams:admin_cohort_email",
              "entrance_exams:admin_app_email_resend",
              "entrance_exams:admin_email_log_list"], "exam")

    if can_admin or is_super:
        _add(_("Administration"), _("Payments"), "payments:index",
             ["payments:download_payment_csv_template", "payments:upload_create",
              "payments:upload_detail", "payments:upload_confirm",
              "payments:record_list", "payments:record_detail",
              "payments:record_edit", "payments:record_delete",
              "payments:enrollment_payment_toggle", "payments:waiver_list"], "payment fee")
        _add(_("Administration"), _("Communications"), "communications:bulk_email_history",
             ["communications:bulk_email_create", "communications:bulk_email_preview",
              "communications:bulk_email_detail",
              "communications:bulk_email_discard",
              "communications:admin_template_list",
              "communications:admin_template_create",
              "communications:admin_template_variables",
              "communications:admin_template_edit",
              "communications:admin_template_preview",
              "communications:admin_footer_edit"], "email message")
        _add(_("Administration"), _("Certificates"), "certificates:admin_certificate_list",
             ["certificates:admin_template_list",
              "certificates:admin_template_create",
              "certificates:admin_template_edit",
              "certificates:admin_generate_certificate",
              "certificates:admin_revoke_certificate",
              "certificates:admin_regenerate_certificate",
              "certificates:admin_download_certificate",
              "certificates:student_certificate_list",
              "certificates:student_certificate_download"], "certificate")
        _add(_("Administration"), _("Intake forms"), "intake_forms:admin_intake_form_list",
             ["intake_forms:admin_intake_form_create",
              "intake_forms:admin_intake_form_detail",
              "intake_forms:admin_intake_response_detail",
              "intake_forms:admin_intake_form_field_delete",
              "intake_forms:admin_version_intake_responses",
              "intake_forms:admin_cohort_intake_responses",
              "intake_forms:admin_intake_request_send",
              "intake_forms:fill_intake_form"], "intake form")
        _add(_("Administration"), _("Audit"), "audit:list", ["audit:detail"], "audit log")
        _add(_("Administration"), _("Landing settings"), "core:landing_settings", [], "landing")
        _add(_("Administration"), _("General settings"), "accounts:admin_settings", [], "settings")

    return items
