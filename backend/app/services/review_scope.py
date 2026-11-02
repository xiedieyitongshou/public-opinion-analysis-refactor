"""Bind review relations to report editions through recorded collection provenance."""

from dataclasses import asdict, dataclass

from sqlalchemy import and_, case, exists, false, func, or_, select

from app.models import AgentTask, DailyReport, HumanReviewTask
from app.models.briefing import AnalysisRun, ReportRevision


def run_scope_ids(db, run_id):
    """A window owns its constituent runs; revisions share the same edition identity."""
    pending, seen = [run_id] if run_id else [], set()
    while pending:
        key = pending.pop()
        if not isinstance(key, str) or not key or key in seen:
            continue
        seen.add(key)
        data = db.scalar(select(AnalysisRun.input_json).where(AnalysisRun.run_id == key)) or {}
        children = (data.get("report_window") or {}).get("run_ids") or []
        if not children and key.startswith("overnight-") and key.endswith("-summary"):
            # Older overnight summaries predate report_window provenance.
            children = list(db.scalars(select(AnalysisRun.run_id).where(
                AnalysisRun.run_id.startswith(key.removesuffix("summary"), autoescape=True),
                AnalysisRun.run_id != key,
            )))
        pending.extend(children)
    return seen


@dataclass(frozen=True)
class ReportReviewScope:
    report_id: int
    title: str
    run_id: str | None
    run_ids: list[str]
    latest_report_id: int
    window_start: str | None
    window_end: str | None

    def as_dict(self):
        return {**asdict(self), "kind": "report",
                "collection_run_count": len(set(self.run_ids) - {self.run_id})
                or int(bool(self.run_id))}


def report_review_scope(db, report_id):
    report = db.get(DailyReport, report_id)
    if report is None:
        raise ValueError("简报不存在")
    revision = db.get(ReportRevision, report_id)
    run_id = revision.run_id if revision else report.briefing_json.get("run_id")
    latest = db.scalar(select(func.max(ReportRevision.report_id)).where(
        ReportRevision.run_id == run_id
    )) if run_id else None
    return ReportReviewScope(
        report_id=report.id, title=report.title, run_id=run_id,
        run_ids=sorted(run_scope_ids(db, run_id)), latest_report_id=latest or report.id,
        window_start=report.briefing_json.get("window_start"),
        window_end=report.briefing_json.get("window_end"),
    )


def review_run_filter(run_ids):
    """Filter before pagination; a deduplicated relation may belong to several runs."""
    if not run_ids:
        return false()
    keys = sorted(run_ids)
    payload = HumanReviewTask.payload_json
    observations = func.json_each(payload["run_ids"]).table_valued("value")
    explicit = or_(
        payload["run_id"].as_string().in_(keys),
        exists(select(1).select_from(observations).where(observations.c.value.in_(keys))),
    )
    legacy = and_(
        or_(payload["run_id"].as_string().is_(None), payload["run_id"].as_string() == ""),
        func.coalesce(func.json_array_length(payload["run_ids"]), 0) == 0,
        HumanReviewTask.agent_task_id.in_(select(AgentTask.id).where(
            AgentTask.input_json["run_id"].as_string().in_(keys)
        )),
    )
    return func.coalesce(or_(explicit, legacy), false())


def active_review_filter():
    return or_(HumanReviewTask.status == "pending",
               HumanReviewTask.payload_json["refresh"]["status"].as_string() == "failed")


def review_counts(db, predicate=None):
    query = select(
        func.count(HumanReviewTask.id),
        func.sum(case((HumanReviewTask.status == "pending", 1), else_=0)),
    ).where(active_review_filter())
    if predicate is not None:
        query = query.where(predicate)
    total, pending = db.execute(query).one()
    return {"total": total, "pending_count": pending or 0,
            "refresh_failed_count": total - (pending or 0)}


def reported_run_ids(db):
    keys = set(db.scalars(select(ReportRevision.run_id)))
    keys.update(db.scalars(select(DailyReport.briefing_json["run_id"].as_string())))
    return set().union(*(run_scope_ids(db, key) for key in keys if key)) if keys else set()


def review_run_ids(db, task):
    payload = task.payload_json or {}
    values = payload.get("run_ids") or []
    ids = {v for v in values if isinstance(v, str) and v} if isinstance(values, list) else set()
    if payload.get("run_id"):
        ids.add(payload["run_id"])
    if not ids and task.agent_task_id:
        key = db.scalar(select(AgentTask.input_json["run_id"].as_string()).where(
            AgentTask.id == task.agent_task_id
        ))
        if key:
            ids.add(key)
    return ids


def validate_review_scope(db, tasks, report_id):
    scope = report_review_scope(db, report_id)
    ids = {task.id for task in tasks}
    matched = set(db.scalars(select(HumanReviewTask.id).where(
        HumanReviewTask.id.in_(ids), review_run_filter(scope.run_ids)
    )))
    if ids != matched:
        raise ValueError("审核任务不属于所选简报，请刷新后重新选择")
    return scope


def review_refresh_run(db, tasks, report_id=None):
    if report_id is not None:
        scope = validate_review_scope(db, tasks, report_id)
        return db.get(AnalysisRun, scope.run_id) if scope.run_id else None
    keys = set().union(*(review_run_ids(db, task) for task in tasks))
    if not keys:
        return None
    # Prefer the latest related summary, never an unrelated newer edition.
    reports = db.execute(select(
        DailyReport.id, ReportRevision.run_id,
        DailyReport.briefing_json["run_id"].as_string().label("legacy_run_id"),
    ).outerjoin(ReportRevision, ReportRevision.report_id == DailyReport.id)
      .order_by(DailyReport.id.desc()))
    seen = set()
    for row in reports:
        key = row.run_id or row.legacy_run_id
        if not key or key in seen:
            continue
        seen.add(key)
        if keys & run_scope_ids(db, key):
            run = db.get(AnalysisRun, key)
            if run and run.output_json and run.window_end:
                return run
    return db.scalar(select(AnalysisRun).where(
        AnalysisRun.run_id.in_(keys), AnalysisRun.output_json.is_not(None),
        AnalysisRun.status.in_(["succeeded", "partial", "skipped"]),
    ).order_by(AnalysisRun.window_end.desc()))
