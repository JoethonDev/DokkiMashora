from __future__ import annotations

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from apps.accounts.constants import (
    GROUP_ADMIN,
    GROUP_MODERATOR,
    GROUP_REVIEWER,
    GROUP_SECRETARY,
    GROUP_STUDENT,
    GROUP_TEACHER,
)
from apps.accounts.models import User
from apps.academics.models import Cohort, Diploma, Semester
from apps.assessments.models import ReviewerAssignment


class Phase0RoutePermissionTests(TestCase):
    def setUp(self):
        self.users = {}
        for role in (GROUP_ADMIN, GROUP_MODERATOR, GROUP_REVIEWER, GROUP_SECRETARY, GROUP_STUDENT, GROUP_TEACHER):
            user = User.objects.create_user(
                f"{role.lower().replace(' ', '_')}@phase0.test", password="pass",
            )
            user.groups.add(Group.objects.get_or_create(name=role)[0])
            self.users[role] = user
        self.super_admin = User.objects.create_superuser("super@phase0.test", password="pass")
        self.diploma = Diploma.objects.create(
            title="Phase 0 Diploma", total_semesters=1, created_by=self.users[GROUP_ADMIN],
        )
        self.cohort = Cohort.objects.create(
            diploma=self.diploma, name="Cohort 1", start_date=timezone.now().date(),
            created_by=self.users[GROUP_ADMIN],
        )
        self.semester = Semester.objects.create(diploma=self.diploma, order=1, title="Semester 1")
        self.assignment = ReviewerAssignment.objects.create(
            reviewer=self.users[GROUP_REVIEWER], cohort=self.cohort, semester=self.semester,
            assigned_by=self.users[GROUP_ADMIN],
        )

    def request_as(self, role, url_name):
        self.client.force_login(self.users[role])
        return self.client.get(reverse(url_name))

    def test_anonymous_is_redirected_and_super_admin_is_allowed(self):
        self.assertEqual(self.client.get(reverse("entrance_exams:admin_list")).status_code, 302)
        self.client.force_login(self.super_admin)
        self.assertEqual(self.client.get(reverse("entrance_exams:admin_list")).status_code, 200)

    def test_moderator_cannot_open_admin_academics_route(self):
        self.assertEqual(self.request_as(GROUP_MODERATOR, "academics:admin_diploma_list").status_code, 403)

    def test_reviewer_cannot_use_another_reviewers_assignment(self):
        other = User.objects.create_user("other-reviewer@phase0.test", password="pass")
        other.groups.add(Group.objects.get_or_create(name=GROUP_REVIEWER)[0])
        self.client.force_login(other)
        response = self.client.get(
            reverse("assessments:reviewer_section_grading", args=[self.assignment.id]),
        )
        self.assertEqual(response.status_code, 404)

    def test_delete_requires_post_and_csrf(self):
        url = reverse(
            "academics:admin_diploma_lecture_delete",
            args=[self.diploma.id, self.cohort.id, self.semester.id, 1],
        )
        self.client.force_login(self.users[GROUP_ADMIN])
        self.assertEqual(self.client.get(url).status_code, 405)
        csrf_client = self.client_class(enforce_csrf_checks=True)
        csrf_client.force_login(self.users[GROUP_ADMIN])
        self.assertEqual(csrf_client.post(url).status_code, 403)

    def test_admin_routes_allow_admin_and_forbid_student(self):
        self.assertEqual(self.request_as(GROUP_ADMIN, "entrance_exams:admin_list").status_code, 200)
        self.assertEqual(self.request_as(GROUP_STUDENT, "entrance_exams:admin_list").status_code, 403)
        self.assertEqual(self.request_as(GROUP_ADMIN, "intake_forms:admin_intake_form_list").status_code, 200)
        self.assertEqual(self.request_as(GROUP_STUDENT, "intake_forms:admin_intake_form_list").status_code, 403)

    def test_reviewer_and_secretary_routes_forbid_wrong_roles(self):
        self.assertEqual(self.request_as(GROUP_REVIEWER, "assessments:reviewer_dashboard").status_code, 200)
        self.assertEqual(self.request_as(GROUP_TEACHER, "assessments:reviewer_dashboard").status_code, 403)
        self.assertEqual(self.request_as(GROUP_SECRETARY, "attendance:attendance_session_list").status_code, 200)
        self.assertEqual(self.request_as(GROUP_REVIEWER, "attendance:attendance_session_list").status_code, 403)
