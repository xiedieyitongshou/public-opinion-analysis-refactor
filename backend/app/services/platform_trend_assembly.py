"""Day 47 observation storage and per-event analysis orchestration."""

from __future__ import annotations

import hashlib
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.models import Event, EventSnapshot, Item, PlatformScore, Source
from app.schemas.collectors import FetchSourceItemsOutput
from app.schemas.platform_heat import PlatformHeatConfig
from app.schemas.platform_trend import (
    EventHeatAnalysis,
    PlatformHeatObservation,
    PlatformTrendConfig,
    PlatformTrendInput,
)
from app.services.platform_heat import item_to_platform_heat_signal, score_platform_signals
from app.services.platform_trend import analyze_platform_trend

MAIN_SOURCE_PLATFORMS = {
    "zhihu_hot_list": "zhihu",
    "weibo_rsshub_hot_search": "weibo",
}
AUX_SOURCE_PLATFORMS = {"zhihu_search": "zhihu"}
SOURCE_PLATFORMS = {**MAIN_SOURCE_PLATFORMS, **AUX_SOURCE_PLATFORMS}


def record_collection_round(
    db: Session,
    *,
    collection: FetchSourceItemsOutput,
    tracked_events: Iterable[Event],
    event_ids_by_content_hash: dict[str, str] | None = None,
    score_config: PlatformHeatConfig | None = None,
) -> int:
    """Associate one collected main-list round with already resolved events.

    Call after event fusion. `event_ids_by_content_hash` supplies fresh resolver
    associations; previously linked Item rows supply historical associations.
    All tracked events get true/false/unknown for every requested main source.
    """

    if collection.validate_only or collection.dry_run:
        return 0
    if not collection.run_id and any(
        not result.run_id for result in collection.results
        if result.source_id in SOURCE_PLATFORMS
    ):
        raise ValueError("a stable collection run_id is required")
    events = {event.event_id: event for event in tracked_events if event.event_id}
    if not events:
        return 0
    result_count = 0
    config = score_config or PlatformHeatConfig()
    config_version = "day44-v1:" + hashlib.sha256(
        config.model_dump_json().encode("utf-8")
    ).hexdigest()[:12]
    for result in collection.results:
        platform = SOURCE_PLATFORMS.get(result.source_id)
        if platform is None or result.validate_only or result.dry_run:
            continue
        if result.observed_at is None or result.observation_id is None:
            raise ValueError("collection must provide observed_at and observation_id")
        hashes = [item.get("content_hash") for item in result.normalized_items]
        linked = (
            db.scalars(select(Item).where(Item.content_hash.in_(hashes))).all()
            if hashes else []
        )
        by_hash = {
            item.content_hash: item.event.event_id
            for item in linked
            if item.event is not None and item.event.event_id
        }
        by_hash.update(event_ids_by_content_hash or {})
        matches: dict[str, list[dict]] = {event_id: [] for event_id in events}
        for item in result.normalized_items:
            event_id = by_hash.get(item.get("content_hash"))
            if event_id in matches:
                matches[event_id].append(item)
        for event_id, event in events.items():
            hit_items = matches[event_id]
            is_main = result.source_id in MAIN_SOURCE_PLATFORMS
            if not is_main and not hit_items:
                continue
            prior = (
                _last_main_observation(db, event, platform, result.observed_at)
                if is_main else None
            )
            scope_changed = bool(
                prior is not None and prior.topn_scope != result.topn_scope
            )
            if is_main and hit_items and result.status == "succeeded":
                present = True
            elif (
                is_main and result.status == "succeeded"
                and result.list_complete and not scope_changed
            ):
                present = False
            else:
                present = None
            rank = None
            if platform == "zhihu" and present is True:
                ranks = [item.get("rank") for item in hit_items]
                ranks = [
                    int(value) for value in ranks
                    if isinstance(value, int | float) and value > 0
                ]
                rank = min(ranks) if ranks else None
            heat = None
            component_signature = None
            if hit_items and result.status == "succeeded":
                signals = [item_to_platform_heat_signal(item, event_id=event_id)
                           for item in hit_items]
                heat = score_platform_signals(
                    signals, config, observed_at=result.observed_at
                )[0]
                if platform == "weibo":
                    heat = heat.model_copy(update={
                        "primary_platform_rank": None,
                        "quality_flags": list(dict.fromkeys(
                            [*heat.quality_flags, "rss_order_not_official_rank"]
                        )),
                    })
                component_signature = ",".join(sorted(
                    key for key, value in heat.sub_scores.items() if value is not None
                ))
            flags = list(result.quality_flags)
            if scope_changed:
                flags.append("incomparable_scope")
            if result.status in {"failed", "skipped"}:
                flags.append("source_unavailable")
            if is_main and result.status == "succeeded" and not result.list_complete:
                flags.append("incomplete_list")
            observation = PlatformHeatObservation(
                event_id=event_id,
                platform=platform,
                observation_id=result.observation_id,
                run_id=result.run_id or collection.run_id,
                observed_at=result.observed_at,
                source_id=result.source_id,
                collection_status=result.status,
                list_complete=result.list_complete,
                topn_scope=result.topn_scope,
                topn_present=present,
                rank=rank,
                platform_heat=heat,
                score_config_version=config_version if heat else None,
                sampling_signature=result.sampling_signature,
                score_component_signature=component_signature,
                source_refs=[str(item.get("url") or item.get("content_hash"))
                             for item in hit_items],
                quality_flags=flags,
            )
            result_count += record_platform_observations(
                db, event=event, observations=[observation]
            )
    return result_count


def _last_main_observation(
    db: Session, event: Event, platform: str, before: datetime | None
) -> PlatformHeatObservation | None:
    if before is None:
        return None
    rows = db.scalars(
        select(EventSnapshot).where(
            EventSnapshot.event_id == event.id,
            EventSnapshot.snapshot_at < before,
        ).order_by(EventSnapshot.snapshot_at.desc()).limit(50)
    ).all()
    for row in rows:
        entries = (row.metrics_json or {}).get("platform_observations", {}).get(platform) or []
        for entry in reversed(entries):
            if entry.get("source_id") in MAIN_SOURCE_PLATFORMS:
                return PlatformHeatObservation.model_validate(entry)
    return None


def record_platform_observations(
    db: Session,
    *,
    event: Event,
    observations: Iterable[PlatformHeatObservation],
    dry_run: bool = False,
) -> int:
    """Upsert genuine collection observations after event association.

    The caller supplies stable event membership from the resolver. Content-deduped
    Item rows and re-scored PlatformScore rows are never treated as observations.
    """

    if dry_run:
        return 0
    if event.id is None or not event.event_id:
        raise ValueError("a persisted Event with a stable event_id is required")
    count = 0
    for observation in observations:
        if observation.event_id != event.event_id:
            raise ValueError("observation belongs to a different event")
        existing_snapshots = db.scalars(
            select(EventSnapshot).where(EventSnapshot.event_id == event.id)
        ).all()
        for existing in existing_snapshots:
            for entries in (existing.metrics_json or {}).get("platform_observations", {}).values():
                if any(
                    entry.get("observation_id") == observation.observation_id
                    for entry in entries
                ):
                    existing_time = existing.snapshot_at
                    if existing_time.tzinfo is None:
                        existing_time = existing_time.replace(tzinfo=UTC)
                    if existing_time != observation.observed_at:
                        raise ValueError(
                            "observation_id cannot refer to a different collection time"
                        )
        if db.get_bind().dialect.name == "sqlite":
            db.execute(
                sqlite_insert(EventSnapshot)
                .values(event_id=event.id, snapshot_at=observation.observed_at)
                .on_conflict_do_nothing(index_elements=["event_id", "snapshot_at"])
            )
        snapshot = db.scalar(
            select(EventSnapshot).where(
                EventSnapshot.event_id == event.id,
                EventSnapshot.snapshot_at == observation.observed_at,
            )
        )
        if snapshot is None:
            snapshot = EventSnapshot(event_id=event.id, snapshot_at=observation.observed_at)
            db.add(snapshot)
            db.flush()
        metrics = dict(snapshot.metrics_json or {})
        by_platform = dict(metrics.get("platform_observations") or {})
        stored = list(by_platform.get(observation.platform) or [])
        payload = observation.model_dump(mode="json")
        if observation.platform_heat is not None:
            score = _upsert_score(db, event, observation)
            payload["platform_score_id"] = score.id
        else:
            payload["platform_score_id"] = None
            old_score = db.scalar(select(PlatformScore).where(
                PlatformScore.event_id == event.id,
                PlatformScore.platform == observation.platform,
                PlatformScore.observation_id == observation.observation_id,
            ))
            if old_score is not None:
                db.delete(old_score)
        stored = [
            entry for entry in stored
            if entry.get("observation_id") != observation.observation_id
        ]
        stored.append(payload)
        stored.sort(key=lambda entry: (entry["observed_at"], entry["observation_id"]))
        by_platform[observation.platform] = stored
        metrics["platform_observations"] = by_platform
        snapshot.metrics_json = metrics
        count += 1
    db.commit()
    return count


def _upsert_score(
    db: Session, event: Event, observation: PlatformHeatObservation
) -> PlatformScore:
    score = db.scalar(
        select(PlatformScore).where(
            PlatformScore.event_id == event.id,
            PlatformScore.platform == observation.platform,
            PlatformScore.observation_id == observation.observation_id,
        )
    )
    if score is None:
        score = PlatformScore(
            event_id=event.id,
            platform=observation.platform,
            observation_id=observation.observation_id,
        )
        db.add(score)
    heat = observation.platform_heat
    score.normalized_score = heat.platform_score
    score.score_detail_json = {
        "business_event_id": event.event_id,
        "observation_id": observation.observation_id,
        "observed_at": observation.observed_at.isoformat(),
        "run_id": observation.run_id,
        "source_id": observation.source_id,
        "score_config_version": observation.score_config_version,
        "sampling_signature": observation.sampling_signature,
        "score_component_signature": observation.score_component_signature,
        "platform_bucket": heat.platform_bucket,
        "platform_strength": heat.platform_strength,
        "score_status": heat.score_status,
        "primary_platform_rank": observation.rank,
        "sub_scores": heat.sub_scores,
        "raw_metrics_used": heat.raw_metrics_used,
        "platform_presence": heat.platform_presence.model_dump(mode="json"),
        "quality_flags": heat.quality_flags,
    }
    db.flush()
    return score


def load_platform_observations(
    db: Session, *, event: Event, window_start: datetime, window_end: datetime
) -> list[PlatformHeatObservation]:
    rows = db.scalars(
        select(EventSnapshot).where(
            EventSnapshot.event_id == event.id,
            EventSnapshot.snapshot_at > window_start,
            EventSnapshot.snapshot_at <= window_end,
        )
    ).all()
    observations = []
    for row in rows:
        for entries in (row.metrics_json or {}).get("platform_observations", {}).values():
            observations.extend(PlatformHeatObservation.model_validate(entry) for entry in entries)
    return observations


def assemble_event_heat_analysis_from_db(
    db: Session,
    *,
    event: Event,
    run_id: str,
    window_end: datetime | None = None,
    configs: dict[str, PlatformTrendConfig] | None = None,
    persist: bool = True,
) -> EventHeatAnalysis:
    """Read real snapshots and overwrite only Day 47's analysis JSON."""

    detail = dict(event.event_detail_json or {})
    classification = detail.get("classification_detail") or {}
    if classification.get("run_id") == run_id and classification.get("window_end"):
        classified_end = datetime.fromisoformat(
            classification["window_end"].replace("Z", "+00:00")
        )
        if window_end is not None and not _same_time(window_end, classified_end):
            raise ValueError("Day 47 must reuse the Day 46 window for this run")
        window_end = classified_end
    window_end = (window_end or datetime.now(UTC)).astimezone(UTC)
    window_start = window_end - timedelta(hours=24)
    previous = detail.get("platform_heat_analysis") or {}
    if previous.get("run_id") == run_id and not _same_time(
        previous.get("window_end"), window_end
    ):
        raise ValueError("the same run_id must reuse its original window")
    observations = load_platform_observations(
        db, event=event, window_start=window_start, window_end=window_end
    )
    platform_results = {}
    for platform in ("zhihu", "weibo"):
        config = (configs or {}).get(platform) or _source_trend_config(db, platform)
        platform_results[platform] = analyze_platform_trend(
            PlatformTrendInput(
                event_id=event.event_id,
                platform=platform,
                run_id=run_id,
                window_start=window_start,
                window_end=window_end,
                observations=[item for item in observations if item.platform == platform],
                config=config,
            )
        )
        if not any(item.platform == platform for item in observations):
            old_scores = db.scalars(select(PlatformScore).where(
                PlatformScore.event_id == event.id,
                PlatformScore.platform == platform,
            )).all()
            if any(
                not (score.score_detail_json or {}).get("observation_id")
                or not (score.score_detail_json or {}).get("observed_at")
                for score in old_scores
            ):
                platform_results[platform].quality_flags.append("missing_observation_metadata")
                platform_results[platform].limitations.append(
                    "Old platform scores have no traceable source observation."
                )
    usable = [
        item for item in platform_results.values()
        if item.latest_platform_heat is not None or item.duration_status != "unknown"
    ]
    complete = all(
        item.latest_platform_heat is not None
        and item.duration_status == "ok"
        and item.trend_status != "unknown"
        for item in platform_results.values()
    )
    flags = list(dict.fromkeys(
        flag for item in platform_results.values() for flag in item.quality_flags
    ))
    if classification and (
        classification.get("run_id") != run_id
        or not _same_time(classification.get("window_start"), window_start)
        or not _same_time(classification.get("window_end"), window_end)
    ):
        flags.append("classification_window_mismatch")
    elif not classification:
        flags.append("missing_classification")
    analysis = EventHeatAnalysis(
        event_id=event.event_id,
        run_id=run_id,
        window_start=window_start,
        window_end=window_end,
        platforms=platform_results,
        status="ok" if complete and not flags else "partial" if usable else "unknown",
        quality_flags=flags,
    )
    if persist:
        detail["platform_heat_analysis"] = analysis.model_dump(mode="json")
        event.event_detail_json = detail
        db.add(event)
        db.commit()
    return analysis


def _source_trend_config(db: Session, platform: str) -> PlatformTrendConfig:
    source_name = "知乎热榜" if platform == "zhihu" else "微博热搜 RSSHub"
    source = db.scalar(select(Source).where(Source.name == source_name))
    interval = source.fetch_interval_minutes if source else None
    return PlatformTrendConfig(
        expected_interval_minutes=interval if interval and interval > 0 else None
    )


def current_event_heat_analysis(event: Event) -> EventHeatAnalysis | None:
    """Return only analysis matching the stored Day 46 run and window."""

    detail = event.event_detail_json or {}
    classification = detail.get("classification_detail") or {}
    analysis = detail.get("platform_heat_analysis")
    if not analysis or analysis.get("run_id") != classification.get("run_id"):
        return None
    parsed = EventHeatAnalysis.model_validate(analysis)
    if not _same_time(classification.get("window_start"), parsed.window_start) or not _same_time(
        classification.get("window_end"), parsed.window_end
    ):
        return None
    return parsed


def _same_time(left: str | datetime | None, right: datetime) -> bool:
    if left is None:
        return False
    try:
        parsed = (
            datetime.fromisoformat(left.replace("Z", "+00:00"))
            if isinstance(left, str) else left
        )
        return parsed.astimezone(UTC) == right.astimezone(UTC)
    except (ValueError, TypeError):
        return False


def rank_events_within_platform(
    events: Iterable[Event], *, platform: str, category: str
) -> list[Event]:
    """Rank only events in one Day 46 category and one platform."""

    if platform not in {"zhihu", "weibo"}:
        raise ValueError("platform must be zhihu or weibo")
    candidates = [
        event for event in events
        if (event.event_detail_json or {}).get("priority_category") == category
    ]

    def key(event: Event) -> tuple:
        analysis = current_event_heat_analysis(event)
        result = analysis.platforms[platform] if analysis else None
        heat = result.latest_platform_heat if result else None
        rank = heat.primary_platform_rank if platform == "zhihu" and heat else None
        score = heat.platform_score if heat else None
        duration = result.continuous_topn_minutes if result else None
        return (
            0 if result and result.current_topn_present is True else 1,
            0 if rank is not None else 1,
            rank if rank is not None else float("inf"),
            0 if score is not None else 1,
            -score if score is not None else 0,
            -(duration or 0),
            event.event_id or "",
        )

    return sorted(candidates, key=key)
