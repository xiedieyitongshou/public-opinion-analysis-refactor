"""Explicit human decisions for two existing community Events."""

from sqlalchemy import select

from app.models import Event, HumanReviewTask
from app.services.briefing import as_utc
from app.services.event_duplicate_scan import REVIEW_TYPE, event_material
from app.services.official_agenda import is_official_agenda_event


def review_pair(db, payload):
    ids = [payload.get("source_event_id"), payload.get("candidate_event_id")]
    events = {e.event_id: e for e in db.scalars(select(Event).where(Event.event_id.in_(ids)))}
    if len(set(ids)) != 2 or len(events) != 2:
        raise ValueError("查重事件不存在，请刷新审核列表")
    source, target = [events[eid] for eid in ids]
    if any(e.lifecycle_status != "active" or is_official_agenda_event(e) for e in events.values()):
        raise ValueError("事件已归并或不属于社区事件，请重新查重")
    expected = payload.get("event_pair_documents")
    if expected != [event_material(source), event_material(target)]:
        raise ValueError("事件材料已更新，请基于最新查重结果审核")
    return source, target


def merge_reviewed_pair(db, payload, *, now):
    """Called only from the authenticated human decision path under the analysis lease."""
    source, target = review_pair(db, payload)
    for item in list(source.items):
        item.event = target
    detail = dict(target.event_detail_json or {})
    old = source.event_detail_json or {}
    for key in ("source_signal_ids", "event_signal_ids", "canonical_ids", "platform_ids"):
        detail[key] = list(dict.fromkeys([*detail.get(key, []), *old.get(key, [])]))
    representatives = list(detail.get("representative_documents", []))
    for representative in old.get("representative_documents", []):
        if representative not in representatives:
            representatives.append(representative)
    detail["representative_documents"] = (
        [representatives[0], *representatives[-2:]] if len(representatives) > 3 else representatives
    )
    # Preserve the same anchor convention as incremental resolve_and_persist_events.
    detail["match_features"] = {
        **(old.get("match_features") or {}), **(detail.get("match_features") or {}),
    }
    detail["merged_event_ids"] = list(dict.fromkeys([
        *detail.get("merged_event_ids", []), source.event_id, *old.get("merged_event_ids", []),
    ]))
    target.event_detail_json = detail
    target.keywords_json = list(dict.fromkeys([
        *(target.keywords_json or []), *(source.keywords_json or []),
    ]))
    citations = {str(c.get("item_id") or c.get("url") or c): c
                 for c in [*(target.source_citations_json or []),
                           *(source.source_citations_json or [])]}
    target.source_citations_json = list(citations.values())
    first = [as_utc(e.first_seen_at) for e in [source, target] if e.first_seen_at]
    last = [as_utc(e.last_seen_at) for e in [source, target] if e.last_seen_at]
    target.first_seen_at = min(first) if first else None
    target.last_seen_at = max(last) if last else None
    # Old snapshots/reports remain auditable. review_refresh rebuilds the accepted target.
    source.lifecycle_status = "merged"
    source.event_detail_json = {**old, "merged_into_event_id": target.event_id,
                               "human_merged_at": now.isoformat()}
    for review in db.scalars(select(HumanReviewTask).where(
        HumanReviewTask.review_type == REVIEW_TYPE, HumanReviewTask.status == "pending",
    )):
        p = review.payload_json or {}
        if p.get("material_key") != payload.get("material_key") and {
            source.event_id, target.event_id,
        }.intersection({p.get("source_event_id"), p.get("candidate_event_id")}):
            review.status, review.resolved_at = "cancelled", now
            review.decision_reason = "事件已由人工归并，旧配对材料失效"
    db.flush()
    return target.event_id
