from decimal import Decimal

from django.conf import settings

try:
    from google import genai
except ImportError:
    genai = None


class GeminiClient:
    def __init__(self):
        self.client = None
        if genai is not None and settings.GEMINI_API_KEY:
            self.client = genai.Client(api_key=settings.GEMINI_API_KEY)
        self.model = settings.GEMINI_MODEL

    def is_available(self) -> bool:
        return self.client is not None

    def grade_essay(self, question_text: str, max_points: Decimal, student_answer: str) -> dict:
        if not self.is_available():
            return {"score": None, "feedback": "Gemini API is not configured.", "error": True}
        prompt = (
            f"You are grading a student's written answer.\n\n"
            f"Question: {question_text}\n\n"
            f"Student's answer: {student_answer}\n\n"
            f"Maximum points: {max_points}\n\n"
            "Grade the student's answer based on correctness, completeness, and clarity.\n"
            "Respond in the following format only:\n"
            f"Score: <number out of {max_points}>\n"
            "Feedback: <brief explanation of the grade>"
        )
        response = self.client.models.generate_content(
            model=self.model,
            contents=prompt,
        )
        return self._parse_response(response.text, max_points)

    def _parse_response(self, text: str, max_points: Decimal) -> dict:
        score = None
        feedback = ""
        for line in text.strip().split("\n"):
            lower = line.lower()
            if lower.startswith("score:"):
                try:
                    parsed = Decimal(line.split(":", 1)[1].strip())
                    score = min(parsed, max_points)
                except Exception:
                    score = Decimal("0.00")
            elif lower.startswith("feedback:"):
                feedback = line.split(":", 1)[1].strip()
        if score is None:
            return {"score": None, "feedback": "Could not parse Gemini response.", "error": True}
        return {"score": score, "feedback": feedback, "error": False}
