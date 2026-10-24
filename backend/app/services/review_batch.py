"""Exclusive evidence ownership and batched human merge decisions."""

from uuid import uuid4

from sqlalchemy import select

from app.models import Event, HumanReviewTask, Item, Source
from app.schemas.human_review import HumanReviewDecisionInput
from app.services.human_review import _within_topic_window, identity_payload
from app.services.review_identity import topic_identity


def stable_topics(payload):
    return {
        key
        for key in topic_identity(payload)
        if not key.startswith("signal:") and key != "official:"
    }


def same_review_subject(db, left, right):
    """Evidence overlap is absolute; weak topic identity also needs its time scope."""
    if set(map(str, left.get("item_ids", []))) & set(map(str, right.get("item_ids", []))):
        return True
    a = {*left.get("event_signal_ids", []), left.get("event_signal_id")} - {None}
    b = {*right.get("event_signal_ids", []), right.get("event_signal_id")} - {None}
    if a & b:
        return True
    if set(left.get("source_signal_ids", [])) & set(right.get("source_signal_ids", [])):
        return True
    return bool(stable_topics(left) & stable_topics(right)) and _within_topic_window(
        db, left, right
    )


class MergeSelection:
    """Read-only view shared by the queue, single decisions, and batch preflight."""

    def __init__(self, db, tasks):
        self.db = db
        self.tasks = {t.id: t for t in tasks}
        self.payloads = {t.id: identity_payload(db, t.payload_json or {}) for t in tasks}
        self.events = {
            e.event_id: e
            for e in db.execute(
                select(Event.id, Event.event_id, Event.title, Event.lifecycle_status)
            )
        }
        self.items = {
            i.id: i
            for i in db.execute(
                select(
                    Item.id,
                    Item.event_id,
                    Item.url,
                    Item.title,
                    Item.fetched_at,
                    Item.signal_role,
                    Source.source_type,
                    Item.normalized_json["source_signal"]["audit_only"]
                    .as_boolean()
                    .label("audit_only"),
                ).join(Source)
            )
        }
        self.claims = []
        for item in self.items.values():
            if not item.event_id or item.signal_role == "search_enrichment_signal":
                continue
            if item.audit_only:
                continue
            p = {
                "identity_sources": [
                    {"url": item.url, "title": item.title, "source_type": item.source_type}
                ],
                "observed_at": item.fetched_at,
            }
            # Only stable source identities, never title similarity, establish ownership.
            if stable_topics(p):
                self.claims.append((p, item.event_id))

    def exclusive(self, a, b):
        left, right = self.payloads[a], self.payloads[b]
        return left.get("candidate_event_id") != right.get(
            "candidate_event_id"
        ) and same_review_subject(self.db, left, right)

    def issues(self, task_id, expected_target=None):
        task = self.tasks[task_id]
        p = self.payloads[task_id]
        target = self.events.get(p.get("candidate_event_id"))
        issues = []
        if task.status != "pending" or task.review_type != "event_merge_candidate":
            issues.append("该项已处理或不属于事件归并审核，请刷新列表")
        if not target or target.lifecycle_status != "active":
            issues.append("候选目标不存在或已失效")
        if expected_target is not None and p.get("candidate_event_id") != expected_target:
            issues.append("候选目标已变更，请刷新后重新确认")
        if not target:
            return issues
        for value in p.get("item_ids", []):
            item = self.items.get(int(value))
            if item is None:
                issues.append("采集记录已不存在")
            elif item.event_id not in {None, target.id}:
                issues.append("采集记录已归属于另一个事件，不能重复归并")
        for claim, event_id in self.claims:
            if event_id != target.id and same_review_subject(self.db, p, claim):
                issues.append("该话题在适用时间范围内已归属于另一个事件，不能跨轮分配到不同目标")
                break
        return list(dict.fromkeys(issues))

    def metadata(self):
        output = {}
        for task_id, p in self.payloads.items():
            event = self.events.get(p.get("candidate_event_id"))
            issues = self.issues(task_id)
            conflicts = []
            for other_id, other in self.tasks.items():
                if other_id == task_id or other.status != "pending":
                    continue
                if self.exclusive(task_id, other_id):
                    target = self.events.get(self.payloads[other_id].get("candidate_event_id"))
                    if target:
                        conflicts.append({"task_id": other_id, "target_title": target.title})
            output[task_id] = {
                "eligible": not issues,
                "unavailable_reason": "；".join(issues),
                "target_event_id": p.get("candidate_event_id"),
                "target_title": event.title if event else None,
                "conflicts": conflicts,
            }
        return output


def selection_metadata(db, rows):
    # Include alternatives outside the visible page so exclusivity is not pagination-dependent.
    pending = list(db.scalars(select(HumanReviewTask).where(HumanReviewTask.status == "pending")))
    tasks = {t.id: t for t in [*pending, *rows]}
    metadata = MergeSelection(db, tasks.values()).metadata()
    return {row.id: metadata[row.id] for row in rows}


def batch_merge_reviews(db, data):
    from app.services.briefing_jobs import acquire_lease, release_lease
    from app.services.human_review import _decide_human_review_task
    from app.services.review_refresh import refresh_after_reviews

    owner = str(uuid4())
    if not acquire_lease(db, "analysis", owner, seconds=900):
        return {"status": "failed", "message": "采集分析正在运行，请稍后重试", "items": []}
    try:
        ids = [entry.task_id for entry in data.items]
        if len(ids) != len(set(ids)):
            return {"status": "failed", "message": "同一审核项不能重复提交", "items": []}
        rows = list(db.scalars(select(HumanReviewTask).where(HumanReviewTask.id.in_(ids))))
        context = MergeSelection(db, rows)
        problems = []
        for entry in data.items:
            issues = (
                context.issues(entry.task_id, entry.candidate_event_id)
                if entry.task_id in context.tasks
                else ["审核项不存在"]
            )
            if issues:
                problems.append({"task_ids": [entry.task_id], "reason": "；".join(issues)})
        for n, left in enumerate(rows):
            for right in rows[n + 1 :]:
                if context.exclusive(left.id, right.id):
                    problems.append(
                        {
                            "task_ids": [left.id, right.id],
                            "reason": "同一线索不能同时归入两个不同事件",
                        }
                    )
        if problems:
            return {
                "status": "failed",
                "message": "批量校验未通过，未执行任何归并",
                "conflicts": problems,
                "items": [],
            }
        results, successful = [], []
        for entry in data.items:
            task = context.tasks[entry.task_id]
            # A parent decision can legitimately resolve a selected search child first.
            if task.status != "pending":
                results.append(
                    {"task_id": task.id, "status": "skipped", "message": "已随父话题处理"}
                )
                continue
            result = _decide_human_review_task(
                db,
                task,
                HumanReviewDecisionInput(
                    decision="merge_to_existing",
                    reviewer=data.reviewer,
                    decision_reason=entry.decision_reason,
                ),
                consolidate=False,
                validate_ownership=False,
            )
            results.append(
                {
                    "task_id": task.id,
                    "status": result.status,
                    "message": result.message,
                    "event_id": result.event_id,
                }
            )
            if result.status == "succeeded":
                successful.append((task, result.event_id))
        report_id, refresh_status = None, "not_needed"
        if successful:
            batch_ids = [task.id for task, _ in successful]
            # Persist retry membership before the expensive rebuild, including interruption cases.
            for task, _ in successful:
                task.payload_json = {
                    **task.payload_json,
                    "refresh": {
                        "status": "failed",
                        "batch_review_ids": batch_ids,
                        "error": "批量归并已保存，等待统一重算",
                        "batch_id": owner,
                    },
                }
            db.commit()
            error = None
            try:
                report_id = refresh_after_reviews(db, successful)
                refresh_status = "refreshed" if report_id else "no_analysis_run"
            except Exception as exc:
                db.rollback()
                refresh_status, error = "failed", type(exc).__name__
            for task, _ in successful:
                task.payload_json = {
                    **task.payload_json,
                    "refresh": {
                        "status": refresh_status,
                        "report_id": report_id,
                        "error": error,
                        "batch_review_ids": batch_ids,
                        "batch_id": owner,
                    },
                }
            db.commit()
        return {
            "status": "partial" if any(r["status"] == "failed" for r in results) else "succeeded",
            "items": results,
            "report_id": report_id,
            "refresh_status": refresh_status,
            "message": "归并已保存，统一重算失败，可重试"
            if refresh_status == "failed"
            else "批量归并完成",
        }
    finally:
        release_lease(db, "analysis", owner)
