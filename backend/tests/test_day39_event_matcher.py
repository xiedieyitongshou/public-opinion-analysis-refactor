from app.agents.event_matcher import build_event_fingerprint, build_event_id
from app.schemas import EventSignal, RetrievedEventMatchCandidate
from app.tools import default_tool_registry


def event_signal(
    *,
    event_signal_id: str = "event-sig-1",
    source_signal_ids: list[str] | None = None,
    title: str = "某品牌公司发布召回通知引发消费者关注",
    keywords: list[str] | None = None,
    entities: list[str] | None = None,
    action_terms: list[str] | None = None,
    event_time_hint: str | None = "2026-09-14T10:00:00+08:00",
    is_weak_signal: bool = False,
) -> EventSignal:
    return EventSignal(
        event_signal_id=event_signal_id,
        source_signal_ids=source_signal_ids or ["source-sig-1"],
        title=title,
        keywords=keywords if keywords is not None else ["某品牌", "召回", "消费者"],
        entities=entities if entities is not None else ["某品牌公司"],
        action_terms=action_terms if action_terms is not None else ["发布", "召回"],
        event_type="consumer_rights",
        event_time_hint=event_time_hint,
        event_text_for_match=f"{title} 某品牌 召回 消费者 发布 召回 consumer_rights",
        semantic_fingerprint={"event_text_for_embedding": f"{title} 某品牌 召回 消费者"},
        confidence=0.9,
        is_weak_signal=is_weak_signal,
        weak_signal_reason=(
            "short_or_generic_topic_without_enough_event_constraints"
            if is_weak_signal
            else None
        ),
        source_statuses=["use"],
        source_roles=["event_signal"],
        platforms=["zhihu"],
    )


def candidate(
    *,
    event_id: str = "evt_existing",
    title: str = "某品牌公司召回部分产品",
    keywords: list[str] | None = None,
    entities: list[str] | None = None,
    action_terms: list[str] | None = None,
    event_time_hint: str | None = "2026-09-14T12:00:00+08:00",
    event_signal_ids: list[str] | None = None,
    source_signal_ids: list[str] | None = None,
    source_urls: list[str] | None = None,
    platform_ids: list[str] | None = None,
) -> RetrievedEventMatchCandidate:
    return RetrievedEventMatchCandidate(
        event_id=event_id,
        title=title,
        keywords=keywords if keywords is not None else ["某品牌", "召回", "消费者"],
        entities=entities if entities is not None else ["某品牌公司"],
        action_terms=action_terms if action_terms is not None else ["召回"],
        event_type="consumer_rights",
        event_time_hint=event_time_hint,
        event_text_for_match=f"{title} 某品牌 召回 消费者",
        event_text_for_embedding=f"{title} 某品牌 召回 消费者",
        event_signal_ids=event_signal_ids or [],
        source_signal_ids=source_signal_ids or [],
        source_urls=source_urls or [],
        platform_ids=platform_ids or [],
    )


def call_match(signal: EventSignal, candidates: list[RetrievedEventMatchCandidate]):
    return default_tool_registry.call(
        "match_and_resolve_events",
        {
            "run_id": "run-39",
            "event_signals": [signal.model_dump(mode="json")],
            "existing_events": [item.model_dump(mode="json") for item in candidates],
        },
    )


def test_match_and_resolve_events_reuses_event_id_on_url_hard_match() -> None:
    signal = event_signal(source_signal_ids=["source-with-url"], entities=[], action_terms=[])
    result = default_tool_registry.call(
        "match_and_resolve_events",
        {
            "run_id": "run-39",
            "event_signals": [signal.model_dump(mode="json")],
            "existing_events": [
                candidate(
                    event_id="evt_url",
                    entities=[],
                    action_terms=[],
                    source_signal_ids=[],
                    source_urls=["https://example.test/news/1"],
                ).model_dump(mode="json")
            ],
            "source_refs_by_signal_id": {
                "source-with-url": {
                    "source_signal_id": "source-with-url",
                    "url": "https://example.test/news/1/",
                }
            },
        },
    )

    resolution = result.output["event_resolutions"][0]
    assert resolution["action"] == "merge"
    assert resolution["event_id"] == "evt_url"
    assert "url_match" in resolution["matched_by"]


def test_match_and_resolve_events_reuses_event_id_on_hard_source_signal_match() -> None:
    signal = event_signal(source_signal_ids=["hard-source"])
    result = call_match(
        signal,
        [candidate(event_id="evt_reused", source_signal_ids=["hard-source"])],
    )

    resolution = result.output["event_resolutions"][0]
    assert resolution["action"] == "merge"
    assert resolution["event_id"] == "evt_reused"
    assert "id_match" in resolution["matched_by"]


def test_match_and_resolve_events_merges_on_title_containment_and_shared_features() -> None:
    signal = event_signal(title="某品牌公司发布召回通知引发消费者关注")
    result = call_match(
        signal,
        [candidate(event_id="evt_title", title="某品牌公司发布召回通知")],
    )

    resolution = result.output["event_resolutions"][0]
    assert resolution["action"] == "merge"
    assert resolution["event_id"] == "evt_title"
    assert "title_containment" in resolution["matched_by"]


def test_match_and_resolve_events_uses_keyword_and_ngram_matching() -> None:
    signal = event_signal(title="某品牌召回产品消费者投诉")
    result = call_match(
        signal,
        [candidate(event_id="evt_ngram", title="某品牌产品召回引发投诉")],
    )

    resolution = result.output["event_resolutions"][0]
    features = resolution["match_features_json"]
    assert resolution["action"] in {"merge", "candidate_review"}
    assert features["keyword_overlap"] >= 0.55
    assert "bm25_ngram" in resolution["matched_by"]


def test_match_and_resolve_events_rejects_entity_conflict() -> None:
    signal = event_signal(entities=["甲公司"])
    result = call_match(signal, [candidate(event_id="evt_conflict", entities=["乙公司"])])

    resolution = result.output["event_resolutions"][0]
    assert resolution["action"] == "reject"
    assert "entity_conflict" in resolution["match_features_json"]["guardrail_flags"]


def test_match_and_resolve_events_rejects_time_conflict() -> None:
    signal = event_signal(event_time_hint="2026-09-14T10:00:00+08:00")
    result = call_match(
        signal,
        [candidate(event_id="evt_old", event_time_hint="2026-01-01T10:00:00+08:00")],
    )

    resolution = result.output["event_resolutions"][0]
    assert resolution["action"] == "reject"
    assert "time_conflict" in resolution["match_features_json"]["guardrail_flags"]


def test_match_and_resolve_events_embedding_disabled_falls_back_to_rule_ngram() -> None:
    result = default_tool_registry.call(
        "match_and_resolve_events",
        {
            "run_id": "run-39",
            "event_signals": [event_signal().model_dump(mode="json")],
            "existing_events": [candidate(event_id="evt_rule").model_dump(mode="json")],
            "match_config": {"use_embedding": False},
        },
    )

    assert result.status == "succeeded"
    assert "embedding_disabled_rule_bm25_ngram_fallback" in result.output["quality_flags"]
    assert result.output["event_resolutions"][0]["event_id"] == "evt_rule"


def test_match_and_resolve_events_creates_new_stable_event_id_without_candidates() -> None:
    signal = event_signal()
    result = call_match(signal, [])

    resolution = result.output["event_resolutions"][0]
    fingerprint = build_event_fingerprint(signal)
    assert resolution["action"] == "create"
    assert resolution["event_id"] == build_event_id(signal, fingerprint)
    assert resolution["event_fingerprint"] == fingerprint


def test_match_and_resolve_events_candidate_review_for_medium_confidence() -> None:
    signal = event_signal(
        title="某品牌召回",
        keywords=["某品牌", "召回", "关注"],
        entities=[],
        action_terms=["召回"],
        event_time_hint=None,
        is_weak_signal=True,
    )
    result = call_match(
        signal,
        [
            candidate(
                event_id="evt_review",
                title="某品牌召回",
                entities=[],
                action_terms=["召回"],
                event_time_hint=None,
            )
        ],
    )

    resolution = result.output["event_resolutions"][0]
    assert resolution["action"] == "candidate_review"
    assert resolution["review_required"] is True


def test_embedding_only_high_similarity_without_hard_constraint_requires_review() -> None:
    signal = event_signal(
        title="相似话题A",
        keywords=["相似", "话题", "讨论"],
        entities=[],
        action_terms=[],
        event_time_hint=None,
        is_weak_signal=True,
    )
    result = call_match(
        signal,
        [
            candidate(
                event_id="evt_embedding_only",
                title="相似话题A",
                keywords=["相似", "话题", "讨论"],
                entities=[],
                action_terms=[],
                event_time_hint=None,
            )
        ],
    )

    resolution = result.output["event_resolutions"][0]
    assert resolution["action"] == "candidate_review"
    assert "embedding_only_without_hard_constraint" in resolution["match_features_json"][
        "guardrail_flags"
    ]
    assert resolution["match_features_json"]["hard_constraints_passed"] is False
