from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.utils.translation import gettext_lazy as _

from apps.core.permissions import role_required
from apps.exams.constants import ASSIGNMENT_STATUS_COMPLETED
from apps.exams.gemini import GeminiClient
from apps.exams.models import Answer, Assignment, Exam
from apps.exams.services import (
    ExamAnswerData,
    get_active_exams,
    get_exam_with_questions,
    get_pending_grading,
    get_student_assignments,
    grade_essay_answer,
    start_exam,
    submit_exam,
)


@login_required
def student_list(request):
    assignments = get_student_assignments(request.user)
    available_exams = get_active_exams().exclude(
        assignments__student=request.user,
    )
    return render(
        request,
        "exams/student_list.html",
        {
            "assignments": assignments,
            "available_exams": available_exams,
        },
    )


@login_required
def exam_instructions(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id, is_active=True)
    assignment = Assignment.objects.filter(
        exam=exam,
        student=request.user,
    ).first()
    if not assignment:
        return redirect("exams:student_list")
    return render(
        request,
        "exams/exam_instructions.html",
        {
            "exam": exam,
            "assignment": assignment,
        },
    )


@login_required
def start_exam_view(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id, is_active=True)
    assignment = get_object_or_404(
        Assignment,
        exam=exam,
        student=request.user,
    )
    start_exam(assignment)
    return redirect("exams:take_exam", exam_id=exam_id)


@login_required
def take_exam(request, exam_id):
    exam = get_exam_with_questions(exam_id)
    if not exam or not exam.is_active:
        raise Http404
    assignment = get_object_or_404(
        Assignment,
        exam=exam,
        student=request.user,
    )
    if assignment.status == ASSIGNMENT_STATUS_COMPLETED:
        return redirect("exams:results", exam_id=exam_id)
    now = timezone.now()
    if assignment.started_at:
        elapsed = (now - assignment.started_at).total_seconds()
        remaining = max(0, int(exam.duration_minutes * 60 - elapsed))
    else:
        remaining = exam.duration_minutes * 60
    questions = exam.questions.prefetch_related("choices").all()
    return render(
        request,
        "exams/take_exam.html",
        {
            "exam": exam,
            "assignment": assignment,
            "questions": questions,
            "remaining_seconds": remaining,
        },
    )


@login_required
def submit_exam_view(request, exam_id):
    if request.method != "POST":
        return redirect("exams:take_exam", exam_id=exam_id)
    exam = get_object_or_404(Exam, id=exam_id, is_active=True)
    assignment = get_object_or_404(
        Assignment,
        exam=exam,
        student=request.user,
    )
    answers = []
    for key, value in request.POST.items():
        if key.startswith("question_"):
            qid = int(key.removeprefix("question_"))
            answers.append(
                ExamAnswerData(
                    question_id=qid,
                    selected_choice_id=int(value) if value.isdigit() else None,
                    essay_text=value if not value.isdigit() else "",
                )
            )
    submit_exam(assignment, answers)
    messages.success(request, _("Exam submitted successfully."))
    return redirect("exams:results", exam_id=exam_id)


@login_required
def exam_results(request, exam_id):
    exam = get_object_or_404(Exam, id=exam_id)
    assignment = get_object_or_404(
        Assignment,
        exam=exam,
        student=request.user,
    )
    answers = assignment.answers.select_related(
        "question",
        "selected_choice",
    ).all()
    return render(
        request,
        "exams/exam_results.html",
        {
            "exam": exam,
            "assignment": assignment,
            "answers": answers,
        },
    )


@login_required
@role_required("Admin", "Super Admin", "Moderator", "Teacher")
def admin_list(request):
    exams = Exam.objects.all().select_related("created_by")
    return render(request, "exams/admin_list.html", {"exams": exams})


@login_required
@role_required("Admin", "Super Admin", "Moderator", "Teacher")
def admin_detail(request, exam_id):
    exam = get_object_or_404(
        Exam.objects.prefetch_related("questions__choices", "assignments__student"),
        id=exam_id,
    )
    return render(
        request,
        "exams/admin_detail.html",
        {
            "exam": exam,
        },
    )


@login_required
@role_required("Admin", "Super Admin", "Moderator", "Teacher")
def admin_grade_list(request):
    pending = get_pending_grading()
    return render(request, "exams/admin_grade_list.html", {"assignments": pending})


@login_required
@role_required("Admin", "Super Admin", "Moderator", "Teacher")
def admin_grade_assignment(request, assignment_id):
    assignment = get_object_or_404(
        Assignment.objects.select_related("exam", "student"),
        id=assignment_id,
    )
    questions = assignment.exam.questions.prefetch_related("choices").all()
    answers = {
        a.question_id: a for a in assignment.answers.select_related("question", "selected_choice").all()
    }
    if request.method == "POST":
        with transaction.atomic():
            for q in questions:
                answer = answers.get(q.id)
                if not answer:
                    continue
                score_key = f"score_{q.id}"
                feedback_key = f"feedback_{q.id}"
                if score_key in request.POST:
                    from decimal import Decimal

                    score = Decimal(str(request.POST[score_key]))
                    feedback = request.POST.get(feedback_key, "")
                    grade_essay_answer(answer, score, feedback, request.user)
        messages.success(request, _("Grades saved."))
        return redirect("exams:admin_grade_assignment", assignment_id=assignment_id)
    return render(
        request,
        "exams/admin_grade_assignment.html",
        {
            "assignment": assignment,
            "questions": questions,
            "answers": answers,
        },
    )


@login_required
@role_required("Admin", "Super Admin", "Moderator", "Teacher")
def admin_grade_with_gemini(request, answer_id):
    answer = get_object_or_404(
        Answer.objects.select_related("question", "assignment__exam"),
        id=answer_id,
    )
    if not answer.essay_text:
        messages.error(request, _("No essay text to grade."))
        return redirect("exams:admin_grade_assignment", assignment_id=answer.assignment_id)
    client = GeminiClient()
    if not client.is_available():
        messages.error(request, _("Gemini API is not configured."))
        return redirect("exams:admin_grade_assignment", assignment_id=answer.assignment_id)
    result = client.grade_essay(
        question_text=answer.question.text,
        max_points=answer.question.points,
        student_answer=answer.essay_text,
    )
    if result.get("error"):
        messages.error(request, result.get("feedback", _("Grading failed.")))
    else:
        grade_essay_answer(answer, result["score"], result["feedback"], request.user)
        messages.success(
            request,
            _("Gemini grade: %(score)s out of %(max)s")
            % {
                "score": result["score"],
                "max": answer.question.points,
            },
        )
    return redirect("exams:admin_grade_assignment", assignment_id=answer.assignment_id)
