"""Build a report from actual collection membership, never a rolling event pool."""

from collections import defaultdict
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import select

from app.models import Item
from app.models.briefing import AnalysisRun
from app.schemas import SourceSignal
from app.schemas.analysis import HotspotAnalysisOutput
from app.schemas.official_support import (
    OfficialReference,
    OfficialSupportDetail,
    OfficialSupportResult,
)
from app.schemas.platform_trend import PlatformTrendConfig
from app.services.briefing import as_utc, generate_briefing, save_briefing
from app.services.classification_assembly import assemble_hotspot_classification_from_db
from app.services.platform_trend_assembly import assemble_event_heat_analysis_from_db


def rebuild_window(db, run):
    """Rebuild only from saved observations and current accepted item associations."""
    scope = run.input_json["report_window"]
    start, end = as_utc(scope["start"]), as_utc(scope["end"])
    runs = [db.get(AnalysisRun, key) for key in scope["run_ids"]]
    items = list(db.scalars(select(Item)))
    by_hash = defaultdict(list)
    for item in items:
        for key in {item.content_hash, (item.normalized_json or {}).get("collector_content_hash")}:
            if key:
                by_hash[key].append(item)
    members, observations = {}, set()
    official_pool = {}
    events = {}
    for original in runs:
        if original is None:
            continue
        for source in (original.collection_json or {}).get("results", []):
            observed = source.get("observed_at")
            if not observed or not start < as_utc(observed) <= end:
                continue
            if source.get("observation_id"):
                observations.add(source["observation_id"])
            for raw in source.get("normalized_items", []):
                for item in by_hash.get(raw.get("content_hash"), []):
                    if item.source.source_type == "official_news":
                        official_pool[item.id] = item
                    data = (item.normalized_json or {}).get("source_signal")
                    if not item.event or not data:
                        continue
                    signal = SourceSignal.model_validate(data)
                    if signal.audit_only or not signal.contributes_to_classification:
                        continue
                    if item.source.source_type == "official_news":
                        if not item.published_at or not start < as_utc(item.published_at) <= end:
                            continue
                    signal = signal.model_copy(update={"fetched_at": as_utc(observed)})
                    key = (item.event.event_id, item.id)
                    if key not in members or as_utc(members[key].fetched_at) < as_utc(observed):
                        members[key] = signal
                    events[item.event.event_id] = item.event
    result = HotspotAnalysisOutput.model_validate(run.output_json)
    result.run_id, result.window_end = run.run_id, end
    result.event_ids, result.classifications, result.analyses = [], [], []
    configs = {
        p: PlatformTrendConfig.model_validate(c) for p, c in run.input_json["trend_configs"].items()
    }
    for eid, event in events.items():
        signals = [s for (event_id, _), s in members.items() if event_id == eid]
        # An auxiliary search result alone cannot discover an event in this window.
        if not any(s.signal_role != "search_enrichment_signal" for s in signals):
            continue
        result.event_ids.append(eid)
        official = [s for s in signals if s.source_type == "official_news"]
        support = OfficialSupportResult(
            event_id=eid,
            official_support_status="supported" if official else "not_checked",
            official_support_detail=OfficialSupportDetail(
                matched_item_ids=[str(s.item_id) for s in official],
                official_item_count=len(official),
                source_coverage_count=len({s.source_id for s in official}),
            ),
            official_references=[
                OfficialReference(
                    item_id=s.item_id,
                    title=s.title,
                    url=s.url,
                    source_name=s.source_id,
                    source_status=s.source_status,
                    published_at=str(s.published_at),
                    match_reason="accepted event association within selected window",
                )
                for s in official
            ],
        )
        if any(s.platform in {"zhihu", "weibo"} for s in signals):
            from app.schemas.official_support import OfficialSupportConfig
            from app.services.official_support import match_official_support

            # Revalidate only evidence captured by the selected original analyses.
            # Never search externally while rebuilding a historical report.
            evidence_ids = set(official_pool)
            for original in runs:
                if not original:
                    continue
                for assembly in (original.output_json or {}).get("classifications", []):
                    if assembly.get("event_id") == eid:
                        detail = assembly.get("classification_detail_json", {})
                        evidence_ids.update(
                            int(v)
                            for v in detail.get("official_support_detail", {}).get(
                                "matched_item_ids", []
                            )
                            if str(v).isdigit()
                        )
            support = match_official_support(
                event,
                [item for item in items if item.id in evidence_ids],
                OfficialSupportConfig(),
                window_start=start,
                window_end=end,
                allow_prior_reports=True,
            )
        assembly = assemble_hotspot_classification_from_db(
            db=db,
            run_id=run.run_id,
            event=event,
            source_signals=signals,
            window_start=start,
            window_end=end,
            allowed_observation_ids=observations,
            persist=False,
            official_support_result=support,
        )
        assembly.classification_detail_json["citation_observed_at"] = {
            str(s.item_id): as_utc(s.fetched_at).isoformat() for s in signals
        }
        result.classifications.append(assembly)
        result.analyses.append(
            assemble_event_heat_analysis_from_db(
                db,
                event=event,
                run_id=run.run_id,
                window_start=start,
                window_end=end,
                configs=configs,
                allowed_observation_ids=observations,
                persist=False,
            )
        )
    run.output_json = result.model_dump(mode="json")
    db.commit()
    report = save_briefing(
        db, generate_briefing(db, run.run_id, refresh=True), supersedes_id=run.report_id
    )
    run.report_id = report.id
    db.commit()
    return report.id


def build_window(
    db, *, start, end, run_ids=None, interval_minutes=60, run_id=None, label="自定义窗口"
):
    start, end = as_utc(start), as_utc(end)
    existing = db.get(AnalysisRun, run_id) if run_id else None
    if existing and existing.window_end:
        end = as_utc(existing.window_end)
    if end > datetime.now(UTC) or not start < end:
        raise ValueError("统计窗口须是已经发生的有效时间范围")
    originals = list(
        db.scalars(
            select(AnalysisRun)
            .where(
                AnalysisRun.status.in_(["partial", "succeeded"]),
                AnalysisRun.output_json.is_not(None),
            )
            .order_by(AnalysisRun.started_at)
        )
    )
    originals = [
        r
        for r in originals
        if not r.input_json.get("report_window")
        and not r.run_id.endswith("-summary")
        and (run_ids is None or r.run_id in run_ids)
        and any(
            s.get("observed_at") and start < as_utc(s["observed_at"]) <= end
            for s in (r.collection_json or {}).get("results", [])
        )
    ]
    if not originals:
        raise ValueError("所选范围没有可用的采集记录")
    rid = run_id or "window-" + uuid4().hex[:16]
    run = db.get(AnalysisRun, rid)
    if run is None:
        run = AnalysisRun(run_id=rid, started_at=datetime.now(UTC))
        db.add(run)
    run.input_json = {
        **originals[-1].input_json,
        "run_id": rid,
        "trend_configs": {
            p: PlatformTrendConfig(expected_interval_minutes=interval_minutes).model_dump(
                mode="json"
            )
            for p in ("zhihu", "weibo")
        },
        "report_window": {
            "start": start.isoformat(),
            "end": end.isoformat(),
            "run_ids": [r.run_id for r in originals],
            "label": label,
        },
    }
    run.status, run.window_end, run.finished_at = "partial", end, datetime.now(UTC)
    run.output_json = originals[-1].output_json
    # Preserve each source's latest result; expose failures across the whole window separately.
    sources = {}
    failures = []
    for original in originals:
        for source in original.collection_json.get("results", []):
            if source.get("observed_at") and start < as_utc(source["observed_at"]) <= end:
                sources[source["source_id"]] = source
                if source["status"] != "succeeded":
                    failures.append(
                        {
                            "run_id": original.run_id,
                            "source_id": source["source_id"],
                            "status": source["status"],
                        }
                    )
    run.collection_json = {
        **originals[-1].collection_json,
        "run_id": rid,
        "results": list(sources.values()),
        "requested_source_ids": list(sources),
        "window_failures": failures,
    }
    db.commit()
    return rebuild_window(db, run)
