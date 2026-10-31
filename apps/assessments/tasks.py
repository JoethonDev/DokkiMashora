"""Asynchronous assessment persistence tasks."""

from datetime import timedelta

from celery import shared_task
from django.db import transaction
from django.utils import timezone

from .constants import (
    ASSESSMENT_TYPE_SEMESTER_EXAM,
    ATTEMPT_STATUS_IN_PROGRESS,
    SUBMISSION_STATUS_FAILED,
    SUBMISSION_STATUS_PENDING,
    SUBMISSION_STATUS_PROCESSING,
)
from .models import Assessment, AssessmentAttempt, AssessmentSubmission
from .services import (
    diploma_autosave_attempt,
    diploma_autosave_attempt_batch,
    prepare_diploma_assessment_attempts,
    process_durable_diploma_submission,
)
from .write_behind import (
    claim_pending,
    claim_schedule,
    discard,
    flush,
    flush_batch,
    has_pending,
    release_schedule,
    requeue_pending,
)


@shared_task(ignore_result=True)
def prepare_upcoming_exam_attempts() -> None:
    """Prepare the current enrollment roster for exams opening within one hour."""
    try:
        recover_queued_diploma_submissions.delay()
    except Exception:
        # Redis keys remain durable; the next existing beat run retries recovery.
        pass
    now = timezone.now()
    assessments = Assessment.objects.filter(
        assessment_type=ASSESSMENT_TYPE_SEMESTER_EXAM,
        is_active=True,
        open_at__gt=now,
        open_at__lte=now + timedelta(hours=1),
        cohort__isnull=False,
        semester__isnull=False,
        course_version__isnull=True,
    ).order_by("pk")
    for assessment in assessments:
        while True:
            created, _skipped = prepare_diploma_assessment_attempts(assessment)
            if not created:
                break


@shared_task(
    bind=True,
    ignore_result=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
    queue="exam_autosave",
)
def flush_snapshot(self, attempt_id: int, generation: int) -> None:
    """Durably flush a versioned pending snapshot to PostgreSQL."""
    try:
        with transaction.atomic():
            attempt = AssessmentAttempt.objects.select_for_update().get(pk=attempt_id)

            def persist(values):
                if attempt.status == ATTEMPT_STATUS_IN_PROGRESS:
                    diploma_autosave_attempt(
                        attempt,
                        values,
                        write_behind=False,
                        flush_accepted_delta=True,
                    )

            if attempt.status == ATTEMPT_STATUS_IN_PROGRESS:
                flush(attempt_id, generation, persist)
            else:
                transaction.on_commit(
                    lambda aid=attempt_id: discard(aid),
                    robust=True,
                )
    except AssessmentAttempt.DoesNotExist:
        discard(attempt_id)
        return
    finally:
        release_schedule(attempt_id)
        if has_pending(attempt_id) and claim_schedule(attempt_id):
            self.apply_async(args=[attempt_id, generation], countdown=1)


@shared_task(
    bind=True,
    ignore_result=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 5},
    queue="exam_autosave",
)
def flush_snapshot_batch(self, attempt_id: int, generation: int) -> None:
    """Flush up to 50 pending attempts in one PostgreSQL bulk update."""
    attempt_ids = claim_pending(50)
    if attempt_id not in attempt_ids:
        attempt_ids.append(attempt_id)
    try:
        with transaction.atomic():
            locked_attempts = list(
                AssessmentAttempt.objects.select_for_update()
                .filter(pk__in=attempt_ids)
                .order_by("pk")
            )
            in_progress_ids = {
                attempt.pk
                for attempt in locked_attempts
                if attempt.status == ATTEMPT_STATUS_IN_PROGRESS
            }
            flush_batch(attempt_ids, diploma_autosave_attempt_batch)
            for stale_id in set(attempt_ids) - in_progress_ids:
                transaction.on_commit(
                    lambda aid=stale_id: discard(aid),
                    robust=True,
                )
    except Exception:
        requeue_pending(attempt_ids)
        raise
    finally:
        for pending_id in attempt_ids:
            release_schedule(pending_id)
            if has_pending(pending_id) and claim_schedule(pending_id):
                self.apply_async(args=[pending_id, generation], countdown=1)


@shared_task(
    bind=True,
    ignore_result=True,
    autoretry_for=(Exception,),
    retry_backoff=True,
    retry_kwargs={"max_retries": 8},
    queue="exam_submission",
)
def process_diploma_submission(self, submission_id: str) -> None:
    """Process one PostgreSQL submission row idempotently."""
    try:
        process_durable_diploma_submission(submission_id)
    except AssessmentSubmission.DoesNotExist:
        # A queued delivery can outlive a maintenance cleanup of its durable row.
        return


@shared_task(ignore_result=True)
def recover_queued_diploma_submissions() -> int:
    """Dispatch durable rows left behind by a broker, worker, or web outage."""
    stale_before = timezone.now() - timedelta(minutes=10)
    AssessmentSubmission.objects.filter(
        processing_status=SUBMISSION_STATUS_PROCESSING,
        processing_started_at__lt=stale_before,
    ).update(
        processing_status=SUBMISSION_STATUS_PENDING,
        processing_started_at=None,
    )
    dispatched = 0
    pending = AssessmentSubmission.objects.filter(
        processing_status__in=[SUBMISSION_STATUS_PENDING, SUBMISSION_STATUS_FAILED],
    ).order_by("accepted_at").values_list("submission_id", flat=True)[:500]
    for submission_id in pending:
        process_diploma_submission.delay(submission_id)
        dispatched += 1
    return dispatched
