"""Persistent, traceable adapters between the three analysis stages."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from app.agents.event_matcher import EventMatcher, parse_datetime
from app.agents.event_resolver import EventResolverAgent
from app.core.config import settings
from app.models import Event, Item, Source
from app.schemas.analysis import (
    ClassifyEventsInput,
    ClassifyEventsOutput,
    PrepareSourceSignalsInput,
    PrepareSourceSignalsOutput,
)
from app.schemas.matching import (
    EventMergeCandidateReview,
    EventResolution,
    MatchAndResolveEventsInput,
    MatchAndResolveEventsOutput,
    RetrievedEventMatchCandidate,
    SourceSignalMatchRef,
)
from app.schemas.platform_heat import CalculateEventScoresInput, CalculateEventScoresOutput
from app.schemas.signals import (
    EventSignalTraceabilityCheck,
    SearchEnrichmentRelation,
    SourceSignal,
)
from app.services.classification_assembly import assemble_hotspot_classification_from_db
from app.services.hotspot_freshness import official_publication_exclusion
from app.services.match_documents import canonical_ids, event_document, event_features
from app.services.matching_profiles import official_config
from app.services.official_paths import enrich_support_detail, official_agenda_rank_from_items
from app.services.official_search import enrich_event_support
from app.services.official_support import match_official_support
from app.services.platform_trend_assembly import load_platform_observations, record_collection_round


def prepare_source_signals(
    db: Session,
    data: PrepareSourceSignalsInput,
) -> PrepareSourceSignalsOutput:
    if data.collection.run_id != data.run_id:
        raise ValueError("collection and analysis must share run_id")
    for result in data.collection.results:
        if result.run_id != data.run_id:
            raise ValueError("all source observations must belong to the analysis run")
        if not result.observation_id or result.observed_at is None:
            raise ValueError("source observations require identity and observed_at")
        if result.observed_at.tzinfo is None:
            raise ValueError("observed_at must include a timezone")
    times = [result.observed_at for result in data.collection.results if result.observed_at]
    window_end = data.window_end or max(times, default=datetime.now(UTC))
    window_end = window_end.astimezone(UTC)
    if any(time > window_end for time in times):
        raise ValueError("window_end cannot precede the source observations")
    output = PrepareSourceSignalsOutput(status="succeeded", window_end=window_end)
    for item in data.items:
        if not item.title or not item.url or not item.content_hash:
            output.errors.append(f"{item.source_id}: title, URL and identity are required")
            continue
        exclusion = (
            official_publication_exclusion(item.published_at, window_end)
            if item.source_type == "official_news" else None
        )
        if item.source_type == "official_news":
            item = item.model_copy(update={
                "normalized": {
                    **item.normalized,
                    "hotspot_freshness": {
                        "eligible": exclusion is None,
                        "basis": "published_at",
                        "window_start": (window_end - timedelta(hours=24)).isoformat(),
                        "window_end": window_end.isoformat(),
                        "exclusion_reason": exclusion,
                    },
                },
                "quality_flags": list(dict.fromkeys([
                    *item.quality_flags, *([exclusion, "audit_only"] if exclusion else [])
                ])),
            })
        source = db.scalar(select(Source).where(Source.name == item.source_name))
        if source is None:
            source = Source(
                name=item.source_name,
                source_type=item.source_type,
                source_status=item.source_status,
                source_origin=item.source_origin,
                platform=item.platform,
                fetch_config_json={"source_id": item.source_id},
            )
            db.add(source)
            db.flush()
        original_hash = item.normalized.get("collector_content_hash")
        row = db.scalar(select(Item).where(Item.content_hash == item.content_hash))
        if row is None and original_hash:
            row = db.scalar(select(Item).where(Item.content_hash == original_hash))
        if row is None:
            row = Item(source_id=source.id, title=item.title, content_hash=item.content_hash)
            db.add(row)
        row.content_hash = item.content_hash
        for field in (
            "external_id",
            "title",
            "url",
            "source_status",
            "source_origin",
            "signal_role",
            "author",
            "summary",
            "language",
        ):
            setattr(row, field, getattr(item, field))
        row.content = item.content_text or item.content
        row.published_at = parse_datetime(item.published_at)
        row.fetched_at = parse_datetime(item.fetched_at)
        row.raw_payload_json = item.raw_payload
        row.raw_metrics_json = item.raw_metrics
        row.quality_flags_json = item.quality_flags
        row.signal_contribution_roles_json = item.signal_contribution_role
        db.flush()
        citation = {**item.source_citation, "item_id": str(row.id), "url": item.url}
        item = item.model_copy(update={"source_citation": citation})
        signal = item_to_source_signal(item, str(row.id))
        row.source_citation_json = citation
        row.normalized_json = {
            **item.normalized,
            "platform": item.platform,
            "event_text_for_match": item.event_text_for_match,
            "event_text_for_embedding": item.event_text_for_embedding,
            "source_signal": signal.model_dump(mode="json"),
        }
        output.saved_count += 1
        if exclusion:
            output.excluded_count += 1
            output.quality_flags = sorted(set([*output.quality_flags, exclusion]))
            continue
        output.items.append(item)
        output.source_signals.append(signal)
        output.source_refs_by_signal_id[signal.source_signal_id] = SourceSignalMatchRef(
            source_signal_id=signal.source_signal_id,
            url=signal.url,
            platform_id=f"{item.source_id}:{item.external_id or row.id}",
            canonical_ids=canonical_ids([signal.url] if signal.url else []),
        )
    db.commit()
    if output.errors:
        output.status = "partial" if output.items else "failed"
    elif not output.items:
        output.status = "skipped"
    return output


def item_to_source_signal(item, item_id: str) -> SourceSignal:
    normalized = item.normalized
    relation_data = normalized.get("relation")
    relation = SearchEnrichmentRelation.model_validate(relation_data) if relation_data else None
    audit_only = bool(
        (relation and relation.audit_only)
        or "audit_only" in item.quality_flags
        or "low_relevance" in item.quality_flags
    )
    signal_id = "source:" + hashlib.sha256(f"{item.source_id}:{item_id}".encode()).hexdigest()[:24]
    attachment = {}
    for field in ("parent_candidate_id", "parent_signal_id", "user_query_id"):
        attachment[field] = getattr(relation, field, None) or normalized.get(field)
    return SourceSignal(
        source_signal_id=signal_id,
        item_id=item_id,
        source_id=item.source_id,
        source_type=item.source_type,
        source_status=item.source_status,
        source_origin=item.source_origin,
        platform=item.platform,
        signal_role=item.signal_role,
        title=item.title,
        url=item.url,
        published_at=item.published_at,
        fetched_at=item.fetched_at,
        raw_metrics=item.raw_metrics,
        platform_features={**normalized, "source_name": item.source_name},
        classification_features={"source_citation": item.source_citation},
        relation_to_parent=relation,
        audit_only=audit_only,
        contributes_to_classification=not audit_only,
        quality_flags=item.quality_flags,
        **attachment,
    )


def _has_current_attention(event: Event, window_end: datetime) -> bool:
    """Keep only accepted discovery evidence in the rolling attention window."""
    start = window_end - timedelta(hours=24)
    for item in event.items:
        signal = (item.normalized_json or {}).get("source_signal", {})
        if signal.get("audit_only") or signal.get("contributes_to_classification") is False:
            continue
        fetched = parse_datetime(item.fetched_at)
        if fetched is None or not start < fetched <= window_end:
            continue
        if item.source.source_type == "official_news":
            if official_publication_exclusion(item.published_at, window_end) is None:
                return True
        elif item.source.source_type in {"community_hotlist", "community_question_hotlist"}:
            return True
    return False


def _candidate(event: Event) -> RetrievedEventMatchCandidate:
    detail = event.event_detail_json or {}
    features = event_features(event)
    document = event_document(event)
    return RetrievedEventMatchCandidate(
        event_id=event.event_id,
        title=event.title,
        event_fingerprint=event.event_fingerprint,
        keywords=event.keywords_json or [],
        entities=document.entities,
        action_terms=document.action_terms,
        event_type=event.event_type,
        event_time_hint=features.get("event_time_hint"),
        first_seen_at=event.first_seen_at,
        last_seen_at=event.last_seen_at,
        event_text_for_match=features.get("event_text_for_match") or event.title,
        event_signal_ids=detail.get("event_signal_ids", []),
        source_signal_ids=detail.get("source_signal_ids", []),
        source_urls=[item.url for item in event.items if item.url],
        platform_ids=detail.get("platform_ids", []),
        canonical_ids=detail.get("canonical_ids", []),
        representative_documents=detail.get("representative_documents", []),
    )


def resolve_and_persist_events(
    db: Session,
    data: MatchAndResolveEventsInput,
    *,
    task_id=None,
    tool_call_id=None,
) -> MatchAndResolveEventsOutput:
    """Resolve sequentially so later signals can reuse events created in this round."""
    window_end = data.window_end or datetime.now(UTC)
    sources = {signal.source_signal_id: signal for signal in data.source_signals}
    items = {str(item.source_citation.get("item_id")): item for item in data.normalized_items}
    result = MatchAndResolveEventsOutput(run_id=data.run_id, status="succeeded")
    matcher = EventMatcher()
    resolver = EventResolverAgent(matcher)
    touched: set[str] = set()
    accepted_discovery_sources: set[str] = set()
    # Resolve discovery seeds before their search results, regardless of source order.
    ordered_signals = sorted(
        data.event_signals,
        key=lambda signal: all(
            sources[key].signal_role == "search_enrichment_signal"
            for key in signal.source_signal_ids
        ),
    )
    for signal in ordered_signals:
        EventSignalTraceabilityCheck(
            event_signal=signal,
            source_signals=data.source_signals,
            normalized_items=data.normalized_items,
        )
        source_signals = [sources[key] for key in signal.source_signal_ids]
        automatic_enrichment = data.discovery_mode == "automatic" and all(
            source.signal_role == "search_enrichment_signal" for source in source_signals
        )
        rows = [db.get(Item, int(source.item_id)) for source in source_signals]
        if any(row is None for row in rows):
            raise ValueError("Event source item is missing from the database")
        candidate_rows = db.scalars(
            select(Event).where(
                Event.event_id.is_not(None),
                Event.lifecycle_status == "active",
                Event.first_seen_at <= window_end,
                or_(
                    Event.last_seen_at
                    >= window_end - timedelta(hours=data.match_config.time_window_same_event_hours),
                    Event.id.in_([row.event_id for row in rows if row.event_id]),
                ),
            )
        ).all()
        candidates = [_candidate(event) for event in candidate_rows]
        parent_event_id = None
        if automatic_enrichment:
            parent_event_id = _resolved_discovery_parent(
                db, source_signals, sources, accepted_discovery_sources
            )
            candidates = [
                candidate for candidate in candidates if candidate.event_id == parent_event_id
            ]
        refs = [data.source_refs_by_signal_id[key] for key in signal.source_signal_ids]
        resolved = resolver.resolve(
            data.model_copy(
                update={
                    "event_signals": [signal],
                    "existing_events": candidates,
                }
            ),
            db=db,
            agent_task_id=task_id,
            tool_call_id=tool_call_id,
        )
        result.quality_flags.extend(resolved.quality_flags)
        if resolved.event_resolutions:
            rejected = resolved.event_resolutions[0]
            if not automatic_enrichment and rejected.action == "reject" and not (
                rejected.match_features_json.id_match or rejected.match_features_json.url_match
            ):
                # A conflicting existing event rejects that association, not the new observation.
                # Keep the guardrail audit produced above, then create a separate event.
                resolved = resolver.resolve(
                    data.model_copy(update={"event_signals": [signal], "existing_events": []}),
                    db=db,
                    agent_task_id=task_id,
                    tool_call_id=tool_call_id,
                )
        result.errors.extend(resolved.errors)
        if not resolved.event_resolutions:
            continue
        resolution = resolved.event_resolutions[0]
        if automatic_enrichment and (
            parent_event_id is None
            or resolution.action != "merge"
            or resolution.review_required
            or resolution.guardrail_status == "block"
            or resolution.event_id != parent_event_id
        ):
            reason = (
                "search enrichment has no resolved discovery parent"
                if parent_event_id is None
                else "search enrichment needs review before joining its parent event"
            )
            reason = f"{reason}: {resolution.reason}"
            resolution = _review_unattached_enrichment(
                resolution, signal, parent_event_id=parent_event_id, reason=reason
            )
            result.quality_flags.append("search_enrichment_not_auto_created")
        result.event_resolutions.append(resolution)
        if (
            resolution.action not in {"create", "merge"}
            or resolution.review_required
            or resolution.blocks_auto_analysis
            or resolution.guardrail_status == "block"
        ):
            result.review_required_count += int(resolution.review_required)
            result.rejected_event_count += int(resolution.action == "reject")
            continue
        event = db.scalar(select(Event).where(Event.event_id == resolution.event_id))
        if event is None:
            event = Event(
                event_id=resolution.event_id,
                event_fingerprint=resolution.event_fingerprint,
                title=signal.title,
                event_type=signal.event_type,
                primary_entity=next(iter(signal.entities), None),
                keywords_json=signal.keywords,
                confidence_score=resolution.confidence,
                first_seen_at=min(parse_datetime(source.fetched_at) for source in source_signals),
            )
            db.add(event)
            db.flush()
            result.created_event_count += 1
        else:
            result.merged_event_count += 1
        observed_at = max(parse_datetime(source.fetched_at) for source in source_signals)
        event.last_seen_at = max(parse_datetime(event.last_seen_at) or observed_at, observed_at)
        detail = dict(event.event_detail_json or {})
        incoming_features = {
            "entities": signal.entities,
            "action_terms": signal.action_terms,
            "event_time_hint": signal.model_dump(mode="json")["event_time_hint"],
            "event_text_for_match": signal.event_text_for_match,
        }
        # Preserve the event anchor; a new member must not redefine the whole cluster.
        previous = detail.get("match_features") or {}
        detail["match_features"] = {
            key: previous.get(key) or val for key, val in incoming_features.items()
        }
        representative = event_document(signal).model_dump(mode="json")
        representatives = detail.get("representative_documents", [])
        if representative not in representatives:
            representatives.append(representative)
        detail["representative_documents"] = (
            [representatives[0], *representatives[-2:]]
            if len(representatives) > 3
            else representatives
        )
        for key, values in (
            ("event_signal_ids", [signal.event_signal_id]),
            ("source_signal_ids", signal.source_signal_ids),
            ("platform_ids", [ref.platform_id for ref in refs if ref.platform_id]),
            (
                "canonical_ids",
                [
                    key
                    for ref in refs
                    for key in (ref.canonical_ids + canonical_ids([ref.url] if ref.url else []))
                ],
            ),
        ):
            detail[key] = list(dict.fromkeys([*detail.get(key, []), *values]))
        detail["latest_resolution"] = resolution.model_dump(mode="json")
        event.event_detail_json = detail
        citations = {
            str(citation.get("item_id")): citation for citation in event.source_citations_json or []
        }
        for row in rows:
            if row.event_id not in {None, event.id}:
                raise ValueError("An item already belongs to another event; review is required")
            row.event_id = event.id
            item = items[str(row.id)]
            citations[str(row.id)] = item.source_citation
            for content_hash in (item.content_hash, item.normalized.get("collector_content_hash")):
                if content_hash:
                    result.event_ids_by_content_hash[content_hash] = event.event_id
        event.source_citations_json = list(citations.values())
        touched.add(event.event_id)
        db.commit()
        accepted_discovery_sources.update(
            source.source_signal_id
            for source in source_signals
            if source.source_id in {"zhihu_hot_list", "weibo_rsshub_hot_search"}
        )
    # Retain recent events to record explicit absence or failure in subsequent rounds.
    tracked = db.scalars(
        select(Event).where(
            Event.event_id.is_not(None),
            Event.lifecycle_status == "active",
            Event.first_seen_at <= window_end,
            or_(Event.last_seen_at > window_end - timedelta(hours=24), Event.event_id.in_(touched)),
        )
    ).all()
    result.event_ids = sorted(
        event.event_id for event in tracked
        if data.discovery_mode != "automatic" or _has_current_attention(event, window_end)
    )
    result.review_payload = {
        "run_id": data.run_id,
        "event_resolutions": [value.model_dump(mode="json") for value in result.event_resolutions],
        "source_signals_by_id": {
            key: value.model_dump(mode="json") for key, value in sources.items()
        },
        "event_signals_by_id": {
            value.event_signal_id: value.model_dump(mode="json") for value in data.event_signals
        },
    }
    result.quality_flags = list(dict.fromkeys(result.quality_flags))
    degraded = any(not flag.startswith("embedding_disabled_") for flag in result.quality_flags)
    if result.errors or result.review_required_count or result.rejected_event_count or degraded:
        result.status = "partial"
    elif not data.event_signals:
        result.status = "skipped"
    return result


def _resolved_discovery_parent(
    db: Session,
    enrichment_sources: list[SourceSignal],
    sources: dict[str, SourceSignal],
    accepted_discovery_sources: set[str],
) -> str | None:
    event_ids: set[str] = set()
    for enrichment in enrichment_sources:
        relation = enrichment.relation_to_parent
        if relation is None or relation.decision != "accepted":
            return None
        expected_source_id = {
            "zhihu_search": "zhihu_hot_list",
            "weibo_cli": "weibo_rsshub_hot_search",
        }.get(relation.source)
        if expected_source_id is None:
            return None
        parent_key = enrichment.parent_candidate_id
        parent_signal_id = enrichment.parent_signal_id
        possible_parents = [
            source for source in sources.values()
            if source.source_id == expected_source_id
            and source.platform == enrichment.platform
            and source.source_signal_id in accepted_discovery_sources
            and (
                source.source_signal_id == parent_signal_id
                or (parent_key is not None and parent_key in {source.url, source.title})
                or (
                    parent_key is not None
                    and source.platform == "zhihu"
                    and bool(set(canonical_ids([parent_key])) & set(canonical_ids([source.url])))
                )
            )
        ]
        parent_events = set()
        for parent in possible_parents:
            row = db.get(Item, int(parent.item_id)) if parent.item_id else None
            if row is not None and row.event is not None and row.event.event_id:
                parent_events.add(row.event.event_id)
        if len(parent_events) != 1:
            return None
        event_ids.update(parent_events)
    return next(iter(event_ids)) if len(event_ids) == 1 else None


def _review_unattached_enrichment(
    resolution: EventResolution,
    signal,
    *,
    parent_event_id: str | None,
    reason: str,
) -> EventResolution:
    review = EventMergeCandidateReview(
        event_signal_id=signal.event_signal_id,
        candidate_event_id=parent_event_id,
        recommended_action=resolution.action,
        confidence=resolution.confidence,
        reason=reason,
        matched_by=resolution.matched_by,
        match_features_json=resolution.match_features_json,
        guardrail_flags=resolution.match_features_json.guardrail_flags,
        source_signal_ids=signal.source_signal_ids,
    )
    return resolution.model_copy(update={
        "action": "candidate_review",
        "event_id": parent_event_id or resolution.event_id,
        "matched_candidate_event_id": parent_event_id,
        "reason": reason,
        "review_required": True,
        "candidate_review": review,
    })


def classify_events(db: Session, data: ClassifyEventsInput) -> ClassifyEventsOutput:
    output = ClassifyEventsOutput(status="succeeded" if data.event_ids else "skipped")
    start = data.window_end - timedelta(hours=24)
    official_items = list(
        db.scalars(
            select(Item)
            .join(Source)
            .where(
                Source.source_type == "official_news",
                Item.fetched_at > start,
                Item.fetched_at <= data.window_end,
                Item.status == "active",
            )
        ).all()
    )
    checked = any(
        result.source_type == "official_news" and result.status == "succeeded"
        for result in data.collection.results
    )
    resolutions = {
        value.event_id: value
        for value in data.event_resolutions
        if value.action in {"create", "merge"} and not value.review_required
    }
    budget = [settings.official_search_max_events]
    for event in db.scalars(
        select(Event).where(Event.event_id.in_(data.event_ids)).order_by(Event.id)
    ).all():
        pool = official_items if checked or official_items else None
        config = official_config(data.matching_profile)
        community = any(item.source.platform in {"zhihu", "weibo"} for item in event.items)
        if data.official_search_enabled and community:
            support = enrich_event_support(
                db, event, pool, run_id=data.run_id, config=config, budget=budget,
                window_end=data.window_end,
            )
        else:
            support = match_official_support(event, pool, config, window_end=data.window_end)
        degraded = [
            flag
            for flag in support.quality_flags
            if flag.startswith(("embedding_", "reranker_", "official_query_"))
        ]
        if degraded:
            output.status = "partial"
            output.quality_flags = sorted(set(output.quality_flags + degraded))
        if support.official_support_detail.official_query is None:
            support.official_support_detail = enrich_support_detail(support)
        signals = [
            SourceSignal.model_validate(item.normalized_json["source_signal"])
            for item in event.items
            if (item.normalized_json or {}).get("source_signal")
        ]
        output.classifications.append(
            assemble_hotspot_classification_from_db(
                db=db,
                run_id=data.run_id,
                event=event,
                window_end=data.window_end,
                source_signals=signals,
                official_support_result=support,
                event_resolution=resolutions.get(event.event_id),
            )
        )
        if event.items and all(item.source.source_type == "official_news" for item in event.items):
            current_items = [
                item for item in event.items
                if official_publication_exclusion(item.published_at, data.window_end) is None
            ]
            event.event_detail_json = {
                **event.event_detail_json,
                "official_agenda_rank": official_agenda_rank_from_items(
                    current_items, window_end=data.window_end
                ).model_dump(mode="json"),
            }
            db.commit()
    return output


def score_collection(db: Session, data: CalculateEventScoresInput) -> CalculateEventScoresOutput:
    """Use one observation-backed scoring writer for classification and trends."""
    if data.dry_run:
        raise ValueError("Observation scoring requires a writable analysis run")
    events = list(db.scalars(select(Event).where(Event.event_id.in_(data.event_ids))).all())
    if not events:
        return CalculateEventScoresOutput(status="skipped")
    count = record_collection_round(
        db,
        collection=data.collection,
        tracked_events=events,
        event_ids_by_content_hash=data.event_ids_by_content_hash,
    )
    observations = [result for result in data.collection.results if result.observed_at]
    scores = []
    if observations:
        times = [result.observed_at for result in observations]
        ids = {result.observation_id for result in observations}
        for event in events:
            for observation in load_platform_observations(
                db,
                event=event,
                window_start=min(times) - timedelta(microseconds=1),
                window_end=max(times),
            ):
                if (
                    observation.observation_id in ids
                    and observation.platform_heat is not None
                    and observation.platform in data.platforms
                ):
                    scores.append(observation.platform_heat)
    return CalculateEventScoresOutput(
        status="succeeded",
        platform_scores=scores,
        saved_count=len(scores),
        saved_observation_count=count,
    )
