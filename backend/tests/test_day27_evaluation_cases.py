import json
from pathlib import Path

from app.schemas import HotspotClassificationInput, PlatformPresence
from app.services.event_classification import classify_hotspot

CASES_PATH = (
    Path(__file__).resolve().parents[1]
    / "app"
    / "evaluation"
    / "cases"
    / "day27_evaluation_cases_v0_1.json"
)


def load_cases() -> dict:
    return json.loads(CASES_PATH.read_text(encoding="utf-8"))


def expected_input(case: dict) -> HotspotClassificationInput:
    expected = case["expected"]
    return HotspotClassificationInput(
        platform_presence=PlatformPresence(**expected["platform_presence"]),
        cross_platform_match_type=expected["cross_platform_match_type"],
        official_support_status=expected["official_support_status"],
        primary_platform_rank=expected["primary_platform_rank"],
        search_hit_quality=expected["search_hit_quality"],
        source_health=expected["source_health"],
        quality_flags=case.get("quality_flags", []),
        snapshot_presence_count=case.get("snapshot_presence_count", 1),
    )


def test_day27_case_set_has_required_sections() -> None:
    cases = load_cases()

    assert cases["case_set_id"] == "day27_evaluation_cases_v0_1"
    assert cases["version"] == "0.1"
    assert len(cases["event_cases"]) >= 8
    assert len(cases["matching_cases"]) >= 6
    assert len(cases["error_cases"]) >= 4
    assert cases["feature_policy"]["may_contribute_to_classification_or_sorting"]
    assert cases["feature_policy"]["must_not_contribute_to_classification_or_sorting"]


def test_day27_event_cases_cover_required_sampling_scenarios() -> None:
    sample_types = {
        sample_type
        for case in load_cases()["event_cases"]
        for sample_type in case["sample_type"]
    }

    assert "community_high_official_not_found" in sample_types
    assert "official_reported_community_low_heat" in sample_types
    assert "weibo_rsshub_missing_degradation" in sample_types
    assert "weibo_rsshub_topic_no_zhihu_discussion" in sample_types


def test_day27_event_cases_replay_expected_classification() -> None:
    for case in load_cases()["event_cases"]:
        result = classify_hotspot(expected_input(case))
        expected = case["expected"]

        assert result.priority_category == expected["priority_category"], case["case_id"]
        assert result.confidence_level == expected["confidence_level"], case["case_id"]
        assert result.official_support_status == expected["official_support_status"]
        assert result.cross_platform_match_type == expected["cross_platform_match_type"]


def test_day27_matching_cases_mark_audit_only_as_non_contributing() -> None:
    for case in load_cases()["matching_cases"]:
        expected = case["expected"]
        if expected["relation_decision"] == "audit_only":
            assert expected["same_event"] is False
            assert expected["should_contribute_to_classification"] is False
            assert expected["should_contribute_to_sorting"] is False


def test_day27_records_current_coverage_gaps_without_fabricating_examples() -> None:
    cases = load_cases()
    expected_categories = {case["expected"]["priority_category"] for case in cases["event_cases"]}

    assert "A_cross_platform_with_official" not in expected_categories
    assert "B_single_platform_with_search_and_official" not in expected_categories
    assert "D_single_platform_with_official" not in expected_categories
    assert any("No real A_cross_platform_with_official" in gap for gap in cases["coverage_gaps"])
