from pathlib import Path

from django.test import SimpleTestCase


ROOT = Path(__file__).resolve().parent


class Phase7PresentationTests(SimpleTestCase):
    def test_attendance_and_progression_surfaces_expose_safe_states(self):
        scan = (ROOT / "apps/attendance/templates/attendance/scan.html").read_text()
        record = (ROOT / "apps/students/templates/students/profile/record.html").read_text()
        detail = (ROOT / "apps/progression/templates/progression/admin_student_detail.html").read_text()

        self.assertIn("Camera permission was denied", scan)
        self.assertIn("attendance_percent", record)
        self.assertIn("Current state", detail)

    def test_attendance_summary_uses_full_history_and_explicit_semester_scope(self):
        selector = (ROOT / "apps/students/selectors.py").read_text()
        diploma = (ROOT / "apps/academics/templates/academics/student_diploma_detail.html").read_text()

        self.assertIn('totals = attendance_qs.aggregate', selector)
        self.assertIn("diploma_access.current_semester.title", diploma)
