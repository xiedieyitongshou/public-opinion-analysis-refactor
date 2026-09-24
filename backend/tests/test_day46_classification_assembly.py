from __future__ import annotations

from datetime import UTC, datetime, timedelta

from app.models import Event, PlatformScore
from app.schemas import (
    EventResolution,
    MatchFeatures,
    OfficialSupportDetail,
    OfficialSupportResult,
    SearchEnrichmentRelation,
    SourceSignal,
)
from app.services.classification_assembly import assemble_hotspot_classification

NOW = datetime(2026, 9, 24, 10, 0, tzinfo=UTC)


def test_day46_assembles_a_cross_platform_with_official() -> None:
    event = _event()
    official = _official(status="supported")

    result = assemble_hotspot_classification(
        run_id="run-day46",
        event=event,
        platform_scores=[
            _score("weibo", {"weibo_topn": True}, rank=2),
            _score("zhihu", {"zhihu_topn": True}, rank=3),
        ],
        official_support_result=official,
        event_resolution=_resolution(matched_by=["title_containment"]),
        window_end=NOW,
    )

    assert result.classification.priority_category == "A_cross_platform_with_official"
    assert result.classification.cross_platform_match_type == "natural_topn_overlap"
    assert event.event_detail_json["priority_category"] == "A_cross_platform_with_official"
    assert event.event_detail_json["classification_detail"]["matched_by"] == ["title_containment"]
    assert {
        "run_id",
        "window_start",
        "window_end",
        "platform_presence_reason",
        "cross_platform_match_reason",
        "event_id_aggregation_basis",
        "platform_scores",
        "source_signals",
        "official_support_detail",
        "evidence_summary",
        "matched_by",
        "match_features_json",
        "missing_fields",
        "rejected_reasons",
        "source_statuses",
        "quality_flags",
        "guardrail_flags",
        "updated_at",
    } <= set(result.classification_detail_json)


def test_day46_assembles_b_single_topn_search_with_official() -> None:
    event = _event()

    result = assemble_hotspot_classification(
        run_id="run-day46",
        event=event,
        platform_scores=[_score("weibo", {"weibo_topn": True}, rank=8)],
        source_signals=[_source_signal(platform="zhihu", signal_role="search_enrichment_signal")],
        official_support_result=_official(status="weak_supported"),
        event_resolution=_resolution(matched_by=["keyword_overlap"]),
        window_end=NOW,
    )

    assert result.classification.priority_category == "B_single_platform_with_search_and_official"
    assert result.classification.platform_presence.zhihu_search is True
    assert result.classification.platform_presence.zhihu_topn is False
    assert result.classification.cross_platform_match_type == "search_supported"


def test_day46_assembles_c_cross_platform_without_official() -> None:
    event = _event()

    result = assemble_hotspot_classification(
        run_id="run-day46",
        event=event,
        platform_scores=[_score("weibo", {"weibo_topn": True}, rank=4)],
        source_signals=[_source_signal(platform="zhihu", signal_role="search_enrichment_signal")],
        official_support_result=_official(status="not_found"),
        event_resolution=_resolution(matched_by=["bm25_ngram"]),
        window_end=NOW,
    )

    assert result.classification.priority_category == "C_cross_platform_without_official"
    assert "official_support_not_found" in result.classification.limitations


def test_day46_assembles_d_single_topn_with_official() -> None:
    event = _event()

    result = assemble_hotspot_classification(
        run_id="run-day46",
        event=event,
        platform_scores=[_score("zhihu", {"zhihu_topn": True}, rank=1)],
        official_support_result=_official(status="supported"),
        window_end=NOW,
    )

    assert result.classification.priority_category == "D_single_platform_with_official"
    assert result.classification.cross_platform_match_type == "single_platform_only"


def test_day46_assembles_e_single_platform_only_and_cli_not_topn() -> None:
    event = _event()

    result = assemble_hotspot_classification(
        run_id="run-day46",
        event=event,
        platform_scores=[_score("weibo", {"weibo_topn": True}, rank=12)],
        official_support_result=_official(status="not_found"),
        window_end=NOW,
    )

    assert result.classification.priority_category == "E_single_platform_only"
    assert result.classification.platform_presence.weibo_topn is True
    assert result.classification.cross_platform_match_type == "single_platform_only"


def test_day46_cli_only_does_not_mark_weibo_topn() -> None:
    event = _event()

    result = assemble_hotspot_classification(
        run_id="run-day46",
        event=event,
        platform_scores=[_score("zhihu", {"zhihu_topn": True}, rank=12)],
        source_signals=[
            _source_signal(
                platform="weibo",
                source_origin="weibo_cli",
                signal_role="search_enrichment_signal",
                relation_confidence=0.55,
            )
        ],
        official_support_result=_official(status="not_found"),
        window_end=NOW,
    )

    assert result.classification.priority_category == "C_cross_platform_without_official"
    assert result.classification.platform_presence.weibo_cli is True
    assert result.classification.platform_presence.weibo_topn is False
    assert result.classification.cross_platform_match_type == "weak_search_supported"


def test_day46_assembles_f_official_only() -> None:
    event = _event()

    result = assemble_hotspot_classification(
        run_id="run-day46",
        event=event,
        platform_scores=[],
        official_support_result=_official(status="supported"),
        window_end=NOW,
    )

    assert result.classification.priority_category == "F_official_only"
    assert result.classification.cross_platform_match_type == "official_only"


def test_day46_search_only_does_not_mark_topn_and_official_does_not_mutate_score() -> None:
    event = _event()
    score = _score("zhihu", {"zhihu_search": True}, rank=None)
    original_detail = dict(score.score_detail_json)

    result = assemble_hotspot_classification(
        run_id="run-day46",
        event=event,
        platform_scores=[score],
        official_support_result=_official(status="supported", coverage="multi_source_independent"),
        window_end=NOW,
    )

    assert result.classification.platform_presence.zhihu_search is True
    assert result.classification.platform_presence.zhihu_topn is False
    assert result.classification.priority_category == "F_official_only"
    assert score.score_detail_json == original_detail
    assert (
        result.classification_detail_json["official_support_detail"]["official_coverage_level"]
        == "multi_source_independent"
    )


def test_day46_repeated_run_overwrites_classification_detail_only() -> None:
    event = _event()
    event.event_detail_json = {
        "keep": "value",
        "classification_detail": {"run_id": "old-run", "stale": True},
    }

    assemble_hotspot_classification(
        run_id="run-1",
        event=event,
        platform_scores=[_score("weibo", {"weibo_topn": True}, rank=3)],
        official_support_result=_official(status="not_found"),
        window_end=NOW,
    )
    assemble_hotspot_classification(
        run_id="run-2",
        event=event,
        platform_scores=[
            _score("weibo", {"weibo_topn": True}, rank=2),
            _score("zhihu", {"zhihu_topn": True}, rank=4),
        ],
        official_support_result=_official(status="supported"),
        window_end=NOW,
    )

    detail = event.event_detail_json
    assert detail["keep"] == "value"
    assert detail["classification_detail"]["run_id"] == "run-2"
    assert "stale" not in detail["classification_detail"]
    assert isinstance(detail["classification_detail"], dict)


def test_day46_ignores_platform_scores_outside_rolling_24h_window() -> None:
    event = _event()

    result = assemble_hotspot_classification(
        run_id="run-day46",
        event=event,
        platform_scores=[
            _score("weibo", {"weibo_topn": True}, rank=1, calculated_at=NOW - timedelta(days=2)),
            _score("zhihu", {"zhihu_topn": True}, rank=5),
        ],
        official_support_result=_official(status="not_found"),
        window_end=NOW,
    )

    assert result.classification.priority_category == "E_single_platform_only"
    assert result.classification.platform_presence.weibo_topn is False
    assert result.classification.platform_presence.zhihu_topn is True


def _event() -> Event:
    return Event(
        id=1,
        event_id="evt-day46",
        title="Day46 event",
        event_detail_json={},
    )


def _score(
    platform: str,
    presence: dict[str, bool],
    *,
    rank: int | None,
    calculated_at: datetime = NOW,
) -> PlatformScore:
    return PlatformScore(
        event_id=1,
        platform=platform,
        normalized_score=0.8,
        calculated_at=calculated_at,
        score_detail_json={
            "business_event_id": "evt-day46",
            "platform_bucket": "top10" if rank else "unknown",
            "platform_strength": "strong",
            "score_status": "ok",
            "primary_platform_rank": rank,
            "snapshot_presence_count": 1,
            "platform_presence": presence,
            "quality_flags": [],
        },
    )


def _official(
    *,
    status: str,
    coverage: str = "single_source",
) -> OfficialSupportResult:
    return OfficialSupportResult(
        event_id="evt-day46",
        official_support_status=status,
        official_support_detail=OfficialSupportDetail(
            official_source_count=1 if status in {"supported", "weak_supported"} else 0,
            official_item_count=1 if status in {"supported", "weak_supported"} else 0,
            official_coverage_level=coverage,
            match_quality="strong" if status == "supported" else "weak",
        ),
        quality_flags=["official_support_not_found"] if status == "not_found" else [],
    )


def _source_signal(
    *,
    platform: str,
    signal_role: str,
    source_origin: str = "official_api",
    relation_confidence: float = 0.85,
) -> SourceSignal:
    relation_source = "zhihu_search" if platform == "zhihu" else "weibo_cli"
    return SourceSignal(
        source_signal_id=f"signal-{platform}-{signal_role}",
        item_id=f"item-{platform}",
        source_id=f"source-{platform}",
        source_type="community_search",
        source_status="use",
        source_origin=source_origin,
        platform=platform,
        signal_role=signal_role,
        title=f"{platform} enrichment",
        url=f"https://example.test/{platform}",
        fetched_at=NOW.isoformat(),
        parent_candidate_id="candidate-1",
        relation_to_parent=SearchEnrichmentRelation(
            source=relation_source,
            parent_candidate_id="candidate-1",
            parent_title="Day46 event",
            enrichment_title=f"{platform} enrichment",
            decision="accepted",
            confidence=relation_confidence,
            matched_by=["keyword_overlap"],
        ),
    )


def _resolution(*, matched_by: list[str]) -> EventResolution:
    return EventResolution(
        event_signal_id="signal-1",
        event_id="evt-day46",
        event_fingerprint="fp-day46",
        action="merge",
        confidence=0.9,
        reason="matched",
        matched_by=matched_by,
        match_features_json=MatchFeatures(
            title_containment="title_containment" in matched_by,
            keyword_overlap=0.7 if "keyword_overlap" in matched_by else 0.0,
            bm25_score=0.8 if "bm25_ngram" in matched_by else 0.0,
            hard_constraints_passed=True,
            matched_by=matched_by,
        ),
    )
