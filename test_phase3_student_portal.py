from datetime import date

from django.test import TestCase
from django.urls import reverse
from django.contrib.auth.models import Group

from apps.accounts.models import User
from apps.accounts.constants import GROUP_STUDENT
from apps.applications.constants import PROGRAM_APP_STATUS_PAYMENT_PENDING
from apps.applications.models import ProgramApplication
from apps.students.models import StudentProfile
from apps.students.selectors import get_dashboard_data


class Phase3StudentPortalTests(TestCase):
    def setUp(self):
        user = User.objects.create_user("student-phase3@test.local", password="pass", full_name="Student Phase Three")
        self.profile = StudentProfile.objects.create(
            user=user, full_name="Student Phase Three", date_of_birth=date(1990, 1, 1),
            gender="female", address="Cairo", phone="01000000003", national_id="300000000003",
            education="bachelor",
        )
        self.profile.user.groups.add(Group.objects.get_or_create(name=GROUP_STUDENT)[0])

    def test_home_context_exposes_canonical_shape_and_payment_action(self):
        ProgramApplication.objects.create(student=self.profile, status=PROGRAM_APP_STATUS_PAYMENT_PENDING)
        data = get_dashboard_data(self.profile)
        self.assertEqual(set(data["summary"]), {
            "active_learning_count", "actionable_assessment_count",
            "open_entrance_exam_count", "available_certificate_count",
        })
        self.assertEqual(data["next_actions"][0]["kind"], "payment")
        self.assertEqual(data["next_actions"][0]["url"], reverse("students:applications"))

    def test_student_portal_has_four_canonical_destinations(self):
        self.client.force_login(self.profile.user)
        for name in ("dashboard", "learning", "applications", "record"):
            self.assertEqual(self.client.get(reverse(f"students:{name}")).status_code, 200)
