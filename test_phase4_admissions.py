from datetime import date

from django.contrib.auth.models import Group
from django.test import TestCase
from django.urls import reverse

from apps.accounts.constants import GROUP_ADMIN
from apps.accounts.models import User
from apps.applications.models import ProgramApplication
from apps.students.models import StudentProfile


class Phase4AdmissionsTests(TestCase):
    def test_registration_keeps_optional_program_and_step_guidance(self):
        response = self.client.get(reverse("applications:register"))
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Registration steps")
        self.assertContains(response, 'name="apply_program"')

    def test_application_list_requires_staff_and_filters_action_queue(self):
        self.assertEqual(self.client.get(reverse("applications:program_list")).status_code, 302)
        admin = User.objects.create_user("phase4-admin@test.local", password="pass")
        admin.groups.add(Group.objects.get_or_create(name=GROUP_ADMIN)[0])
        profile = StudentProfile.objects.create(
            user=admin, full_name="Phase Four Student", date_of_birth=date(1990, 1, 1),
            gender="female", address="Cairo", phone="01000000004", national_id="300000000004",
            education="bachelor",
        )
        ProgramApplication.objects.create(student=profile, status="submitted")
        self.client.force_login(admin)
        response = self.client.get(reverse("applications:program_list") + "?action_needed=review")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Phase Four Student")
