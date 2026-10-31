from datetime import UTC, datetime

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db.init_db import init_db
from app.models import Event
from app.schemas import CrawlValidationResult, FetchSourceItemsOutput
from app.schemas.platform_trend import PlatformTrendConfig
from app.services.platform_trend_assembly import (
    assemble_event_heat_analysis_from_db,
    record_collection_round,
    zhihu_answer_metrics,
)

NOW = datetime(2026, 10, 20, tzinfo=UTC)
SEED = {
    "url": "https://www.zhihu.com/question/123", "platform": "zhihu",
    "rank": 5, "content_hash": "seed", "signal_role": "topic_discovery_signal",
    "source_origin": "official_api",
}


def answer(number, *, question=123, accepted=True, votes=10):
    return {
        "url": f"https://www.zhihu.com/question/{question}/answer/{number}",
        "content_hash": str(number), "platform": "zhihu",
        "raw_metrics": {"vote_count": votes, "comment_count": 2},
        "normalized": {"relation": {"decision": "accepted" if accepted else "rejected"}},
    }


def collection(items, run="run"):
    def result(source, values):
        return CrawlValidationResult(
            source_id=source, run_id=run, status="succeeded", validate_only=False,
            dry_run=False, observation_id=f"{run}:{source}", observed_at=NOW,
            normalized_items=values, list_complete=True, topn_scope="top20",
            sampling_signature="same",
        )
    return FetchSourceItemsOutput(
        status="succeeded", run_id=run, validate_only=False, dry_run=False,
        requested_source_ids=["zhihu_hot_list", "zhihu_search"],
        results=[result("zhihu_hot_list", [SEED]), result("zhihu_search", items)],
    )


def test_question_identity_acceptance_and_answer_deduplication():
    items = [answer(1), answer(1), answer(2), answer(3, question=999),
             answer(4, accepted=False)]
    metrics, refs, _ = zhihu_answer_metrics(SEED, collection(items))
    assert metrics == {"vote_count": 20, "comment_count": 4}
    assert len(refs) == 2
    old = collection(items)
    old.results[1].run_id = "older-run"
    assert zhihu_answer_metrics(SEED, old)[0] == {}


def test_final_main_heat_uses_same_round_answers_without_accumulation(monkeypatch):
    from app.core.config import settings
    monkeypatch.setattr(settings, "zhihu_search_enrichment_enabled", True)
    engine = create_engine("sqlite://")
    init_db(engine)
    with Session(engine) as db:
        event = Event(event_id="evt", title="question")
        db.add(event)
        db.commit()
        for run in ("one", "two"):
            record_collection_round(
                db, collection=collection([answer(1, votes=100)], run),
                tracked_events=[event], event_ids_by_content_hash={"seed": "evt"},
            )
        analysis = assemble_event_heat_analysis_from_db(
            db, event=event, run_id="two", window_end=NOW,
            configs={"zhihu": PlatformTrendConfig(expected_interval_minutes=60)},
            persist=False,
        )
        heat = analysis.platforms["zhihu"].latest_platform_heat
        assert heat.raw_metrics_used["vote_count"] == 100
        assert heat.raw_metrics_used["comment_count"] == 2
        assert heat.sub_scores["engagement_score"] is not None
        assert heat.primary_platform_rank == 5
    engine.dispose()
