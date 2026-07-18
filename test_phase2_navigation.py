from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import GROUP_ADMIN, GROUP_MODERATOR, GROUP_SECRETARY, GROUP_TEACHER
from apps.accounts.models import User


class Phase2NavigationTests(TestCase):
    def user_with_role(self, role, email):
        user = User.objects.create_user(email, password="pass")
        user.groups.add(Group.objects.get_or_create(name=role)[0])
        return user

    def test_secretary_lands_on_scoped_attendance_home(self):
        self.client.force_login(self.user_with_role(GROUP_SECRETARY, "secretary-phase2@test.local"))
        response = self.client.get(reverse("accounts:dashboard"))
        self.assertRedirects(response, reverse("accounts:dashboard_secretary"))

    def test_moderator_empty_state_is_assignment_based(self):
        self.client.force_login(self.user_with_role(GROUP_MODERATOR, "moderator-phase2@test.local"))
        response = self.client.get(reverse("accounts:dashboard_moderator"))
        self.assertContains(response, "No active assignments")

    def test_staff_teacher_can_open_scoped_grading_queue(self):
        teacher = self.user_with_role(GROUP_TEACHER, "teacher-phase2@test.local")
        teacher.is_staff = True
        teacher.save(update_fields=["is_staff"])
        self.client.force_login(teacher)
        response = self.client.get(reverse("assessments:admin_grade_list"))
        self.assertEqual(response.status_code, 200)

    def test_teacher_post_grading_handlers_pass_role_gate(self):
        teacher = self.user_with_role(GROUP_TEACHER, "teacher-post-phase2@test.local")
        teacher.is_staff = True
        teacher.save(update_fields=["is_staff"])
        self.client.force_login(teacher)
        answer_response = self.client.get(reverse("assessments:admin_grade_answer", args=[999999]))
        homework_response = self.client.get(reverse("assessments:admin_grade_homework", args=[999999]))
        self.assertEqual(answer_response.status_code, 405)
        self.assertEqual(homework_response.status_code, 405)

    def test_admin_dashboard_links_settings_instead_of_rendering_configuration(self):
        self.client.force_login(self.user_with_role(GROUP_ADMIN, "admin-phase2@test.local"))
        response = self.client.get(reverse("accounts:dashboard_admin"))
        self.assertContains(response, reverse("accounts:admin_settings"))
        self.assertNotContains(response, "AI Configuration")
