"""Recompute accepted membership using saved rounds, never the review's wall clock."""

from datetime import timedelta

from sqlalchemy import select

from app.core.config import settings
from app.models import AgentTask, Event
from app.models.briefing import AnalysisRun
from app.schemas.analysis import ClassifyEventsInput, HotspotAnalysisInput, HotspotAnalysisOutput
from app.schemas.collectors import FetchSourceItemsOutput
from app.services.analysis_interfaces import _has_current_attention, classify_events
from app.services.briefing import as_utc, generate_briefing, save_briefing
from app.services.platform_trend_assembly import (
    assemble_event_heat_analysis_from_db,
    record_collection_round,
)


def refresh_after_review(db, task, event_id):
    run = db.scalar(
        select(AnalysisRun)
        .where(
            AnalysisRun.output_json.is_not(None),
            AnalysisRun.status.in_(["succeeded", "partial", "skipped"]),
        )
        .order_by(AnalysisRun.window_end.desc())
    )
    if run is None or not run.window_end:
        return None
    end = as_utc(run.window_end)
    result = HotspotAnalysisOutput.model_validate(run.output_json)
    if event_id:
        event = db.scalar(select(Event).where(Event.event_id == event_id))
        if event is None:
            raise ValueError("reviewed_event_missing")
        by_hash = {}
        for item in event.items:
            by_hash[item.content_hash] = event_id
            original = (item.normalized_json or {}).get("collector_content_hash")
            if original:
                by_hash[original] = event_id
        # Include the original round even when it is now older than 24h; it remains history.
        original_run = (task.payload_json or {}).get("run_id")
        tasks = db.scalars(
            select(AgentTask)
            .where(
                AgentTask.task_type == "fetch_source_items",
                AgentTask.output_json.is_not(None),
            )
            .order_by(AgentTask.id)
        ).all()
        seen = set()
        for recorded in tasks:
            collection = FetchSourceItemsOutput.model_validate(recorded.output_json)
            if collection.run_id in seen:
                continue
            seen.add(collection.run_id)
            recent = any(
                value.observed_at and end - timedelta(hours=24) < value.observed_at <= end
                for value in collection.results
            )
            if recent or collection.run_id == original_run:
                record_collection_round(
                    db,
                    collection=collection,
                    tracked_events=[event],
                    event_ids_by_content_hash=by_hash,
                )
        if _has_current_attention(event, end) and event_id not in result.event_ids:
            result.event_ids.append(event_id)
    collection = FetchSourceItemsOutput.model_validate(run.collection_json)
    original_classify = db.scalar(select(AgentTask).where(
        AgentTask.plan_id == result.plan_id, AgentTask.task_type == "classify_events",
    ))
    profile = ((original_classify.input_json or {}).get("matching_profile")
               if original_classify else None) or settings.matching_profile
    classified = classify_events(
        db,
        ClassifyEventsInput(
            run_id=run.run_id,
            window_end=end,
            event_ids=result.event_ids,
            collection=collection,
            matching_profile=profile,
            official_search_enabled=bool(
                original_classify and original_classify.input_json.get("official_search_enabled")
            ),
            official_search_budget=0,
        ),
    )
    result.classifications = classified.classifications
    configs = HotspotAnalysisInput.model_validate(run.input_json).trend_configs
    result.analyses = [
        assemble_event_heat_analysis_from_db(
            db=db,
            event=event,
            run_id=run.run_id,
            window_end=end,
            configs=configs,
        )
        for event in db.scalars(select(Event).where(Event.event_id.in_(result.event_ids)))
    ]
    run.output_json = result.model_dump(mode="json")
    db.commit()
    report = save_briefing(
        db, generate_briefing(db, run.run_id, refresh=True), supersedes_id=run.report_id,
    )
    run.report_id = report.id
    db.commit()
    return report.id
