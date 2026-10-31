"""Celery signals that persist operational job history and system audit rows."""

from __future__ import annotations

import logging
import re

from celery.signals import task_failure, task_postrun, task_prerun, task_retry
from django.utils import timezone

from apps.audit.constants import (
    ACTION_SYSTEM_TASK_COMPLETED,
    ACTION_SYSTEM_TASK_FAILED,
    ACTION_SYSTEM_TASK_RETRIED,
    OUTCOME_FAILURE,
    OUTCOME_SUCCESS,
    SECTION_SYSTEM,
    SOURCE_TASK,
)
from apps.audit.services import record_system_event
from apps.core.logging import clear_task_context, set_task_context

from .models import (
    JOB_STATUS_FAILED,
    JOB_STATUS_RETRIED,
    JOB_STATUS_STARTED,
    JOB_STATUS_SUCCESS,
    SystemJobRun,
)

logger = logging.getLogger(__name__)

_SECRET_PATTERN = re.compile(
    r"(?i)(password|passwd|token|secret|api_key|authorization)\s*[=:]\s*['\"]?[^,\s'\"]+"
)
_SECRET_DICT_PATTERN = re.compile(
    r"(?i)(['\"](?:password|passwd|token|secret|api_key|authorization)['\"]\s*:\s*)['\"][^'\"]*['\"]"
)
_EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}")

# Exam autosave/flush tasks can run many times per minute during an exam. They
# are operational plumbing, not business jobs: record failures/retries, but do
# not write two history rows per successful flush.
_HIGH_FREQUENCY_TASKS = {
    "apps.assessments.tasks.flush_snapshot",
    "apps.assessments.tasks.flush_snapshot_batch",
    "apps.communications.tasks.send_queued_email_task",
    "apps.communications.tasks.send_bulk_email_chunk_task",
}


def _task_name(sender, task=None) -> str:
    return str(getattr(task, "name", "") or getattr(sender, "name", "") or "unknown-task")[:255]


def _queue(task) -> str:
    request = getattr(task, "request", None)
    delivery_info = getattr(request, "delivery_info", None) or {}
    return str(delivery_info.get("routing_key", "") or "")[:64]


def _summary(value, limit: int = 1200) -> str:
    text = repr(value)
    text = _SECRET_DICT_PATTERN.sub(r"\1'[redacted]'", text)
    text = _SECRET_PATTERN.sub(r"\1=[redacted]", text)
    text = _EMAIL_PATTERN.sub("[email]", text)
    return text[:limit]


def _audit_task(
    action: str,
    *,
    task_id: str,
    name: str,
    queue: str,
    outcome: str,
    metadata: dict,
) -> None:
    try:
        record_system_event(
            action,
            section=SECTION_SYSTEM,
            target_type="system_task",
            target_id=task_id,
            target_label=name,
            metadata={"task_name": name, "queue": queue, **metadata},
            outcome=outcome,
            correlation_id=task_id,
            source=SOURCE_TASK,
        )
    except Exception:  # noqa: BLE001 - never break task execution
        logger.exception("Could not record audit event for task %s", task_id)


@task_prerun.connect
def record_task_started(sender=None, task_id=None, task=None, args=None, kwargs=None, **extra) -> None:
    if not task_id:
        return
    name = _task_name(sender, task)
    if name in _HIGH_FREQUENCY_TASKS:
        return
    set_task_context(task_id=str(task_id), task_name=name)
    try:
        SystemJobRun.objects.update_or_create(
            task_id=str(task_id),
            defaults={
                "task_name": name,
                "queue": _queue(task),
                "status": JOB_STATUS_STARTED,
                "started_at": timezone.now(),
                "finished_at": None,
                "duration_ms": None,
                "args_summary": _summary({"args": args, "kwargs": kwargs}),
                "correlation_id": str(task_id)[:64],
            },
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not record task start for %s", task_id)


@task_postrun.connect
def record_task_finished(
    sender=None,
    task_id=None,
    task=None,
    retval=None,
    state=None,
    **extra,
) -> None:
    if not task_id:
        return
    name = _task_name(sender, task)
    if name in _HIGH_FREQUENCY_TASKS:
        return
    if str(state or "").upper() == "RETRY":
        clear_task_context()
        return
    now = timezone.now()
    try:
        run = SystemJobRun.objects.filter(task_id=str(task_id)).first()
        started_at = run.started_at if run is not None and run.started_at else now
        duration_ms = max(int((now - started_at).total_seconds() * 1000), 0)
        succeeded = str(state or "").upper() == "SUCCESS"
        SystemJobRun.objects.update_or_create(
            task_id=str(task_id),
            defaults={
                "task_name": name,
                "queue": _queue(task),
                "status": JOB_STATUS_SUCCESS if succeeded else JOB_STATUS_FAILED,
                "started_at": started_at,
                "finished_at": now,
                "duration_ms": duration_ms,
                "result_summary": _summary(retval) if succeeded else "",
                "correlation_id": str(task_id)[:64],
            },
        )
        if succeeded:
            _audit_task(
                ACTION_SYSTEM_TASK_COMPLETED,
                task_id=str(task_id),
                name=name,
                queue=_queue(task),
                outcome=OUTCOME_SUCCESS,
                metadata={"duration_ms": duration_ms, "result": _summary(retval)},
            )
        else:
            logger.error(
                "celery_task_failed task_id=%s task=%s duration_ms=%s state=%s",
                task_id,
                name,
                duration_ms,
                state,
            )
    except Exception:  # noqa: BLE001
        logger.exception("Could not record task finish for %s", task_id)
    finally:
        clear_task_context()


@task_failure.connect
def record_task_failure(sender=None, task_id=None, exception=None, **extra) -> None:
    if not task_id:
        return
    name = _task_name(sender)
    set_task_context(task_id=str(task_id), task_name=name)
    error = f"{type(exception).__name__}: {exception}"[:1000] if exception else ""
    now = timezone.now()
    try:
        run = SystemJobRun.objects.filter(task_id=str(task_id)).first()
        started_at = run.started_at if run is not None and run.started_at else now
        duration_ms = max(int((now - started_at).total_seconds() * 1000), 0)
        SystemJobRun.objects.update_or_create(
            task_id=str(task_id),
            defaults={
                "task_name": name,
                "status": JOB_STATUS_FAILED,
                "finished_at": now,
                "duration_ms": duration_ms,
                "error": error,
                "correlation_id": str(task_id)[:64],
            },
        )
        _audit_task(
            ACTION_SYSTEM_TASK_FAILED,
            task_id=str(task_id),
            name=name,
            queue="",
            outcome=OUTCOME_FAILURE,
            metadata={"error": error, "duration_ms": duration_ms},
        )
        logger.error(
            "celery_task_failed task_id=%s task=%s duration_ms=%s error=%s",
            task_id,
            name,
            duration_ms,
            error,
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not record task failure for %s", task_id)
    finally:
        clear_task_context()


@task_retry.connect
def record_task_retry(request=None, reason=None, **extra) -> None:
    task_id = getattr(request, "id", None)
    if not task_id:
        return
    name = str(getattr(request, "task", "") or "unknown-task")[:255]
    set_task_context(task_id=str(task_id), task_name=name)
    try:
        SystemJobRun.objects.update_or_create(
            task_id=str(task_id),
            defaults={
                "task_name": name,
                "status": JOB_STATUS_RETRIED,
                "error": str(reason or "")[:1000],
                "correlation_id": str(task_id)[:64],
            },
        )
        _audit_task(
            ACTION_SYSTEM_TASK_RETRIED,
            task_id=str(task_id),
            name=name,
            queue="",
            outcome=OUTCOME_SUCCESS,
            metadata={"reason": str(reason or "")[:500]},
        )
        logger.warning(
            "celery_task_retry task_id=%s task=%s reason=%s",
            task_id,
            name,
            str(reason or "")[:500],
        )
    except Exception:  # noqa: BLE001
        logger.exception("Could not record task retry for %s", task_id)
    finally:
        clear_task_context()
