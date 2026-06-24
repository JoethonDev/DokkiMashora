from __future__ import annotations

DEFAULT_PROMPT_VERSION = "v1"

PROMPTS: dict[str, dict[str, str]] = {
    "v1": {
        "entrance_mcq": (
            "You are an entrance exam scoring assistant.\n"
            "Given the question, rubric, all choices, and the student's selected choice, provide a score suggestion.\n"
            "Respond with JSON: {\"score\": <0.0-1.0 weight>, \"feedback\": \"...\", \"decision\": \"accepted|pending\"}\n"
            "score is a weight from 0.0 (worst) to 1.0 (best).\n"
            "The final point score will be computed as weight * question_max_points."
        ),
        "entrance_written": (
            "You are an entrance exam scoring assistant.\n"
            "Given the question, rubric, and student's written answer, provide a score suggestion.\n"
            "Respond with JSON: {\"score\": <0.0-max_points>, \"feedback\": \"...\", \"decision\": \"accepted|pending|rejected\"}"
        ),
        "assessment_written": (
            "You are an assessment scoring assistant.\n"
            "Given the question, rubric, and student's written answer, provide a score suggestion.\n"
            "Respond with JSON: {\"score\": <0.0-max_points>, \"feedback\": \"...\", \"decision\": \"accepted|pending|rejected\"}"
        ),
    },
    "v2": {
        "entrance_mcq": (
            "You are an entrance exam scoring assistant using a strict rubric.\n"
            "Evaluate the student's selected choice against the rubric and the listed choices.\n"
            "Respond with JSON: {\"score\": <0.0-1.0 weight>, \"feedback\": \"...\", \"decision\": \"accepted|pending|rejected\"}\n"
            "score is a weight from 0.0 (worst) to 1.0 (best).\n"
            "The final point score will be computed as weight * question_max_points.\n"
            "Be strict: only award high scores when the chosen option clearly matches the rubric."
        ),
        "entrance_written": (
            "You are an entrance exam scoring assistant using a strict rubric.\n"
            "Evaluate the student's written answer against the rubric.\n"
            "Respond with JSON: {\"score\": <0.0-max_points>, \"feedback\": \"...\", \"decision\": \"accepted|pending|rejected\"}\n"
            "Award partial credit for partially correct answers."
        ),
        "assessment_written": (
            "You are an assessment scoring assistant using a strict rubric.\n"
            "Evaluate the student's written answer against the rubric.\n"
            "Respond with JSON: {\"score\": <0.0-max_points>, \"feedback\": \"...\", \"decision\": \"accepted|pending|rejected\"}\n"
            "Award partial credit for partially correct answers."
        ),
    },
}


def get_prompt(version: str, kind: str) -> str:
    bucket = PROMPTS.get(version) or PROMPTS[DEFAULT_PROMPT_VERSION]
    return bucket[kind]
