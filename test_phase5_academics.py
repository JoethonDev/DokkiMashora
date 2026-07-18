from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.academics.views import _lecture_navigation, _lecture_progress


class Phase5AcademicPresentationTests(SimpleTestCase):
    def test_progress_uses_only_the_supplied_scope(self):
        first = SimpleNamespace(id=1)
        second = SimpleNamespace(id=2)
        done = SimpleNamespace(lecture_id=1, status="completed")
        hidden_done = SimpleNamespace(lecture_id=99, status="completed")

        result = _lecture_progress([first, second], [done, hidden_done])

        self.assertEqual(result["completed_count"], 1)
        self.assertEqual(result["percent"], 50)
        self.assertIs(result["next_lecture"], second)

    def test_lecture_navigation_is_bounded_to_the_current_version(self):
        version = SimpleNamespace(id=11)
        first = SimpleNamespace(id=1)
        second = SimpleNamespace(id=2)

        result = _lecture_navigation([first, second], first, version=version)

        self.assertIsNone(result["previous_url"])
        self.assertIn("/academics/my-courses/11/lectures/2/", result["next_url"])
