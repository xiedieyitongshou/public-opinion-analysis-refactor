"""Exhaustive, resumable community Event pair review using the existing LLM contract."""

import hashlib
import json
import logging
from datetime import UTC, datetime, timedelta
from time import monotonic
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.orm import selectinload

from app.core.config import settings
from app.models import AgentTask, Event, EventPairJudgment, EventSnapshot, HumanReviewTask, Item
from app.schemas.llm import MATCH_PROMPT, LLMPairDecision
from app.services.briefing import as_utc
from app.services.llm_client import DeepSeekJSONClient, LLMBudgetExceeded, LLMFailure, document
from app.services.llm_event_workflow import judge_pairs, validate_pair
from app.services.llm_material import canonical_pair, remap_result
from app.services.official_agenda import is_official_agenda_event
from app.services.topic_content import topic_context

logger = logging.getLogger(__name__)
TASK_TYPE = "community_event_duplicate_scan"
REVIEW_TYPE = "event_duplicate_candidate"


def event_material(event):
    """Reuse accepted source context. Heat and observation clocks are outside the key."""
    items = [i for i in sorted(event.items, key=lambda i: i.id)
             if i.signal_role != "search_enrichment_signal"
             and not (i.normalized_json or {}).get("source_signal", {}).get("audit_only")]
    body = "\n".join(dict.fromkeys(topic_context(i) for i in items if topic_context(i)))
    body = body or event.summary or ""
    sources = [{"url": i.url, "published_at": i.published_at.isoformat()
                if i.published_at else None} for i in items]
    full = json.dumps([event.title, body, sources], ensure_ascii=False, sort_keys=True)
    return document(event.event_id, event.title, body,
                    material_hash=hashlib.sha256(full.encode()).hexdigest(), sources=sources)


def recent_community_materials(db, window_end):
    """Only positive, successful main-list observations in (end-24h, end]."""
    end, valid = as_utc(window_end), {}
    for snapshot in db.scalars(select(EventSnapshot).where(
        EventSnapshot.snapshot_at > end - timedelta(hours=24),
        EventSnapshot.snapshot_at <= end,
    )):
        for entries in (snapshot.metrics_json or {}).get("platform_observations", {}).values():
            for obs in entries:
                observed = as_utc(obs["observed_at"])
                if (obs.get("platform") in {"weibo", "zhihu"}
                    and obs.get("source_id") in {"zhihu_hot_list", "weibo_rsshub_hot_search"}
                    and obs.get("topn_present") is True
                    and obs.get("collection_status") == "succeeded"
                    and end - timedelta(hours=24) < observed <= end):
                    valid.setdefault(snapshot.event_id, set()).add(obs.get("run_id"))
    events = db.scalars(select(Event).where(
        Event.id.in_(valid), Event.lifecycle_status == "active", Event.event_id.is_not(None),
    ).options(selectinload(Event.items).selectinload(Item.source)).order_by(Event.event_id)).all()
    return [{"document": event_material(e), "run_ids": sorted(valid[e.id] - {None})}
            for e in events if not is_official_agenda_event(e)]


def create_scan(db, *, window_end=None, max_calls=300, token_budget=2000000):
    end = as_utc(window_end or datetime.now(UTC))
    documents = recent_community_materials(db, end)
    task = AgentTask(
        task_type=TASK_TYPE, status="queued", title="社区事件全量两两查重",
        max_retries=1, retry_count=0,
        input_json={"run_id": "event-dedup-" + uuid4().hex,
                    "window_end": end.isoformat(), "events": documents,
                    "policy_key": policy_key(),
                    "max_calls": max_calls, "token_budget": token_budget},
        output_json={"cursor": [0, 1], "event_count": len(documents),
                     "pair_count": len(documents) * (len(documents) - 1) // 2,
                     "completed_pairs": 0, "cache_hits": 0, "review_ids": [],
                     "errors": [], "unresolved_pairs": {}, "processing_seconds": 0},
    )
    db.add(task)
    db.commit()
    return task


def pair_key(client, incoming, candidate):
    # Callers always supply lexicographically ordered stable Event IDs.
    material = client.cache_identity("event_pair", MATCH_PROMPT,
                                     canonical_pair(incoming, candidate), LLMPairDecision)[0]
    return hashlib.sha256(json.dumps([incoming["source_id"], candidate["source_id"], material])
                          .encode()).hexdigest()


def policy_key():
    return DeepSeekJSONClient().cache_identity("event_pair", MATCH_PROMPT, {}, LLMPairDecision)[0]


def cached_pair(db, client, incoming, candidate):
    cached = db.get(EventPairJudgment, pair_key(client, incoming, candidate))
    if cached is None:
        return None
    row = LLMPairDecision.model_validate(cached.decision_json)
    validate_pair(row, incoming, candidate)
    return row, {**cached.audit_json, "cache_hit": True, "durable_pair_cache": True}


def save_pair(db, client, incoming, candidate, row, audit):
    key = pair_key(client, incoming, candidate)
    db.merge(EventPairJudgment(material_key=key, event_a=incoming["source_id"],
                               event_b=candidate["source_id"],
                               decision_json=row.model_dump(mode="json"), audit_json=audit))


def queue_duplicate_review(db, task, client, incoming, candidate, row, audit):
    suspected = row.decision == "same_event" or (
        row.decision == "insufficient_evidence" and row.uncertainty_kind == "possible_same_event"
    )
    if not suspected:
        return None
    pair_ids = [incoming["source_id"], candidate["source_id"]]
    current = list(db.scalars(select(Event).where(Event.event_id.in_(pair_ids))))
    by_id = {e.event_id: e for e in current}
    if any(eid not in by_id or by_id[eid].lifecycle_status != "active"
           or is_official_agenda_event(by_id[eid]) for eid in pair_ids):
        return None
    # A snapshot is immutable, but membership/material can change while it waits its turn.
    if any(event_material(by_id[d["source_id"]]) != d for d in [incoming, candidate]):
        return None
    identity = "|".join(pair_ids)
    previous = db.scalar(select(HumanReviewTask).where(
        HumanReviewTask.review_type == REVIEW_TYPE,
        HumanReviewTask.payload_json["pair_identity"].as_string() == identity,
        HumanReviewTask.payload_json["material_key"].as_string() == pair_key(
            client, incoming, candidate),
    ))
    run_ids = sorted({r for e in task.input_json["events"]
                      if e["document"]["source_id"] in pair_ids for r in e["run_ids"]})
    if previous:
        if previous.status != "pending":
            return None
        payload = dict(previous.payload_json or {})
        payload["run_ids"] = sorted(set([*payload.get("run_ids", []), *run_ids]))
        previous.payload_json = payload
        return previous.id
    incoming_event = by_id[incoming["source_id"]]
    review = HumanReviewTask(
        agent_task_id=task.id, review_type=REVIEW_TYPE, status="pending", priority=50,
        payload_json={"pair_identity": identity,
                      "material_key": pair_key(client, incoming, candidate),
                      "run_ids": run_ids, "scan_run_id": task.input_json["run_id"],
                      "title": incoming["title"], "source_event_id": incoming["source_id"],
                      "candidate_event_id": candidate["source_id"],
                      "source_citations": incoming_event.source_citations_json or [],
                      "item_ids": [], "reason": row.reason,
                      "event_pair_documents": [incoming, candidate],
                      "llm_decision": row.model_dump(mode="json"), "llm_audit": audit},
    )
    db.add(review)
    db.flush()
    return review.id


def run_slice(db, task, *, client=None, max_batches=4, seconds=90, batch_size=4,
              should_yield=None):
    """Commit each batch and yield. No collection, retrieval or automatic merge occurs."""
    config = task.input_json
    if config["policy_key"] != policy_key():
        task.status, task.error_message = "stale", "model_or_prompt_changed"
        task.finished_at = datetime.now(UTC)
        db.commit()
        return task
    client = client or DeepSeekJSONClient(run_id=config["run_id"],
                                         max_calls=config["max_calls"],
                                         token_budget=config["token_budget"])
    state = dict(task.output_json)
    if task.status == "paused" and task.retry_count < task.max_retries:
        # One recovery pass reuses all successful durable pairs and the original budget.
        task.retry_count += 1
        state.update(cursor=[0, 1], completed_pairs=0, cache_hits=0)
    if task.retry_count:
        batch_size = min(batch_size, 4)
    documents = [e["document"] for e in config["events"]]
    started = monotonic()
    task.status, task.error_message = "running", None
    task.started_at = task.started_at or datetime.now(UTC)
    db.commit()
    batches = 0
    while state["cursor"][0] < len(documents) - 1:
        if (batches >= max_batches or monotonic() - started >= seconds
            or (should_yield is not None and should_yield())):
            break
        a, b = state["cursor"]
        incoming, candidates = documents[a], documents[b:b + batch_size]
        # Split oversized batches without excluding a single event pair.
        while len(candidates) > 1 and len(json.dumps(
            {"incoming": incoming, "candidates": candidates}, ensure_ascii=False,
        )) > settings.llm_input_max_chars:
            candidates = candidates[:-1]
        found, missing = {}, []
        for candidate in candidates:
            cached = cached_pair(db, client, incoming, candidate)
            if cached:
                found[candidate["source_id"]] = cached
                state["cache_hits"] += 1
            else:
                missing.append(candidate)
        error = None
        if missing:
            batches += 1
            try:
                for row, audit in judge_pairs(incoming, missing, client=client,
                                              batch_size=batch_size):
                    found[row.candidate_id] = (row, audit)
            except LLMFailure as exc:
                error = exc
                # judge_pairs caches independently validated successes before raising.
                for candidate in missing:
                    payload = canonical_pair(incoming, candidate)
                    cached = client.read_cached(
                        "event_pair", MATCH_PROMPT, payload, LLMPairDecision,
                        lambda row, p=payload: validate_pair(row, p["incoming"], p["candidate"]),
                    )
                    if cached:
                        row, audit = cached
                        row = remap_result(row, {"incoming": incoming["source_id"],
                                                 "candidate": candidate["source_id"]})
                        found[row.candidate_id] = (row, audit)
        for candidate in candidates:
            result = found.get(candidate["source_id"])
            if result:
                row, audit = result
                save_pair(db, client, incoming, candidate, row, audit)
                review_id = queue_duplicate_review(
                    db, task, client, incoming, candidate, row, audit,
                )
                if review_id:
                    state["review_ids"] = sorted(set([*state["review_ids"], review_id]))
        if error:
            task.error_message = str(error)
            state["errors"] = [*state["errors"],
                               {"cursor": [a, b], "error": str(error),
                                "at": datetime.now(UTC).isoformat()}]
            if isinstance(error, LLMBudgetExceeded):
                task.status = "budget_exhausted"
                break
            if str(error).startswith(("llm_http_", "llm_network", "llm_timeout",
                                      "llm_api_key", "llm_base_url", "llm_input_budget")):
                task.status = "paused" if task.retry_count < task.max_retries else "partial"
                break
            for candidate in candidates:
                if candidate["source_id"] not in found:
                    state["unresolved_pairs"][pair_key(client, incoming, candidate)] = str(error)
        for candidate in candidates:
            if candidate["source_id"] in found:
                state["unresolved_pairs"].pop(pair_key(client, incoming, candidate), None)
        state["completed_pairs"] += len(found)
        b += len(candidates)
        state["cursor"] = [a + 1, a + 2] if b >= len(documents) else [a, b]
        task.output_json = dict(state)
        db.commit()
    if task.status == "running":
        if state["cursor"][0] >= len(documents) - 1:
            task.status = "completed" if not state["unresolved_pairs"] else (
                "paused" if task.retry_count < task.max_retries else "partial"
            )
        else:
            task.status = "queued"
    if task.status in {"completed", "partial", "budget_exhausted"}:
        task.finished_at = datetime.now(UTC)
    state["processing_seconds"] += round(monotonic() - started, 3)
    state["usage"] = client.budget.summary()
    task.output_json = state
    db.commit()
    logger.info("event_dedup run=%s status=%s completed=%s/%s requests=%s tokens=%s",
                config["run_id"], task.status, state["completed_pairs"], state["pair_count"],
                state["usage"]["requests"], state["usage"]["charged_tokens"])
    return task
