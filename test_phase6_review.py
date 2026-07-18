from pathlib import Path
from types import SimpleNamespace

from django.test import RequestFactory, SimpleTestCase

from apps.assessments.views import _parse_answers


class Phase6ReviewTests(SimpleTestCase):
    def test_assessment_rejects_malformed_question_key(self):
        class Questions:
            def prefetch_related(self, *_args):
                return self

            def all(self):
                return [SimpleNamespace(id=1, question_type="written")]

        request = RequestFactory().post("/", {"question_not_an_id": "answer"})
        with self.assertRaisesMessage(ValueError, "Invalid question."):
            _parse_answers(request, SimpleNamespace(questions=Questions()))

    def test_runner_drafts_are_account_scoped(self):
        assessment = Path("apps/assessments/templates/assessments/take_assessment.html").read_text()
        entrance = Path("apps/entrance_exams/templates/entrance_exams/take_exam.html").read_text()
        self.assertIn("_student_' + studentId + '_answers", assessment)
        self.assertIn("_student_' + studentId + '_answers", entrance)
