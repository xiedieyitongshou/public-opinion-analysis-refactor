"""State transition rules for agent tasks and report drafts."""

from datetime import UTC, datetime
from enum import StrEnum

from app.models import AgentTask, DailyReport


class TaskStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    RETRYING = "retrying"
    BLOCKED = "blocked"


class DraftStatus(StrEnum):
    DRAFT = "draft"
    DRAFT_READY_FOR_REVIEW = "draft_ready_for_review"
    DRAFT_NEEDS_REVIEW = "draft_needs_review"
    DRAFT_BLOCKED_FOR_PUBLISH = "draft_blocked_for_publish"
    PUBLISHED = "published"
    PUBLISHED_WITH_WARNING = "published_with_warning"


TASK_TRANSITIONS: dict[TaskStatus, set[TaskStatus]] = {
    TaskStatus.PENDING: {TaskStatus.RUNNING, TaskStatus.BLOCKED},
    TaskStatus.RUNNING: {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.BLOCKED},
    TaskStatus.FAILED: {TaskStatus.RETRYING, TaskStatus.BLOCKED},
    TaskStatus.RETRYING: {TaskStatus.RUNNING, TaskStatus.BLOCKED},
    TaskStatus.SUCCEEDED: set(),
    TaskStatus.BLOCKED: set(),
}

DRAFT_TRANSITIONS: dict[DraftStatus, set[DraftStatus]] = {
    DraftStatus.DRAFT: {
        DraftStatus.DRAFT_READY_FOR_REVIEW,
        DraftStatus.DRAFT_NEEDS_REVIEW,
        DraftStatus.DRAFT_BLOCKED_FOR_PUBLISH,
    },
    DraftStatus.DRAFT_NEEDS_REVIEW: {
        DraftStatus.DRAFT_READY_FOR_REVIEW,
        DraftStatus.DRAFT_BLOCKED_FOR_PUBLISH,
    },
    DraftStatus.DRAFT_READY_FOR_REVIEW: {
        DraftStatus.PUBLISHED,
        DraftStatus.PUBLISHED_WITH_WARNING,
        DraftStatus.DRAFT_BLOCKED_FOR_PUBLISH,
    },
    DraftStatus.DRAFT_BLOCKED_FOR_PUBLISH: set(),
    DraftStatus.PUBLISHED: set(),
    DraftStatus.PUBLISHED_WITH_WARNING: set(),
}


class StateTransitionError(ValueError):
    """Raised when a status transition is not allowed."""


def can_transition_task(current: str, target: str) -> bool:
    return TaskStatus(target) in TASK_TRANSITIONS[TaskStatus(current)]


def can_transition_draft(current: str, target: str) -> bool:
    return DraftStatus(target) in DRAFT_TRANSITIONS[DraftStatus(current)]


def transition_task(task: AgentTask, target: TaskStatus | str) -> AgentTask:
    target_status = TaskStatus(target)
    current_status = TaskStatus(task.status)
    if target_status not in TASK_TRANSITIONS[current_status]:
        raise StateTransitionError(f"Invalid task transition: {current_status} -> {target_status}")

    task.status = target_status.value
    if target_status is TaskStatus.RUNNING:
        task.started_at = datetime.now(UTC)
    if target_status in {TaskStatus.SUCCEEDED, TaskStatus.FAILED, TaskStatus.BLOCKED}:
        task.finished_at = datetime.now(UTC)
    if target_status is TaskStatus.RETRYING:
        task.retry_count += 1
    return task


def transition_draft(report: DailyReport, target: DraftStatus | str) -> DailyReport:
    target_status = DraftStatus(target)
    current_status = DraftStatus(report.status)
    if target_status not in DRAFT_TRANSITIONS[current_status]:
        raise StateTransitionError(f"Invalid draft transition: {current_status} -> {target_status}")

    report.status = target_status.value
    if target_status in {DraftStatus.PUBLISHED, DraftStatus.PUBLISHED_WITH_WARNING}:
        report.published_at = datetime.now(UTC)
    return report
