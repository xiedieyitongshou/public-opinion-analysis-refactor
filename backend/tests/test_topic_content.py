"""No paid services: verify the actual text boundary and parent membership changes."""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from test_day41_human_review_queue import make_session
from test_hotlist_topic_policy import seed

from app.agents.event_extractor import EventSignalAssembler, RuleEventExtractor
from app.models import Event, HumanReviewTask, Item, Source
from app.schemas import NormalizedItem
from app.services.analysis_interfaces import _candidate, item_to_source_signal
from app.services.human_review import _reattach_pending_children
from app.services.match_documents import event_document
from app.services.topic_content import summary_from_items, topic_context, topic_excerpts
from app.services.topic_discovery_policy import can_create_hotlist_topic


def sample(text="警方公布调查进展，事故车辆已被拖离现场。", decision="accepted"):
    return {
        "text": text,
        "url": "https://weibo.com/123/456",
        "relation": {"decision": decision, "audit_only": decision != "accepted"},
    }


def test_accepted_bodies_only_deduplicate_and_bound():
    item = {
        "title": "事故进展",
        "raw_payload": {
            "cli_enrichment": {
                "samples": [
                    sample(),
                    sample(),
                    sample("蹭热点的广告与这件事无关", "rejected"),
                    sample("#事故进展#"),
                    sample("另一个已接纳的证据句子，介绍车辆后续检测结果。"),
                ]
            }
        },
    }
    excerpts = topic_excerpts(item)
    assert len(excerpts) == 2
    assert "广告" not in topic_context(item)
    assert "警方" in topic_context(item)
    assert all(row["url"] for row in excerpts)


def test_context_reaches_both_match_documents():
    item = NormalizedItem(
        source_id="weibo_rsshub_hot_search",
        source_name="微博",
        source_type="community_hotlist",
        source_status="use",
        source_origin="rsshub",
        platform="weibo",
        signal_role="topic_discovery_signal",
        title="事故进展",
        url="https://weibo.com/topic",
        fetched_at=datetime.now(UTC),
        raw_payload={"cli_enrichment": {"samples": [sample()]}},
    )
    source = item_to_source_signal(item, "1")
    signal = EventSignalAssembler().assemble(
        run_id="offline", result=RuleEventExtractor().extract(source)
    )
    assert "警方公布" in event_document(signal).text
    event = Event(
        event_id="example",
        title="事故进展",
        items=[
            Item(
                id=1,
                title=item.title,
                content_hash="one",
                raw_payload_json=item.raw_payload,
                signal_role=item.signal_role,
            )
        ],
    )
    assert "警方公布" in event_document(_candidate(event)).text


def test_summary_does_not_call_an_answer_question_description():
    answer = SimpleNamespace(
        source=SimpleNamespace(platform="zhihu", source_type="community_search"),
        title="问题",
        summary="这是回答正文",
        raw_payload_json={},
    )
    assert "这是回答正文" not in summary_from_items([answer])[0]
    question = SimpleNamespace(
        source=SimpleNamespace(platform="zhihu", source_type="community_question_hotlist"),
        title="问题",
        summary="问题的补充说明",
        url="https://zhihu.com/question/1",
    )
    assert summary_from_items([question, answer])[0] == "知乎问题描述：问题的补充说明"


def test_short_topic_is_independent_but_search_is_not():
    assert can_create_hotlist_topic(seed("荣耀").model_dump())
    assert not can_create_hotlist_topic(seed("荣耀", "search_enrichment_signal").model_dump())
    assert not can_create_hotlist_topic(seed("热议").model_dump())


@pytest.mark.parametrize(
    "decision, question, owned, expected",
    [
        ("accepted", "123", False, True),
        ("rejected", "123", False, False),
        ("accepted", "456", False, False),
        ("accepted", "123", True, False),
    ],
)
def test_parent_resolution_cascade_is_identity_bounded(decision, question, owned, expected):
    with make_session() as db:
        source = Source(name="hot", platform="zhihu", source_type="community_question_hotlist")
        search = Source(name="search", platform="zhihu", source_type="community_search")
        target, other = (
            Event(event_id="target", title="事件"),
            Event(event_id="other", title="其他"),
        )
        db.add_all([source, search, target, other])
        db.flush()
        parent = Item(
            source=source,
            event=target,
            title="问题",
            content_hash="parent",
            url="https://www.zhihu.com/question/123",
        )
        child = Item(
            source=search,
            title="回答",
            content_hash="child",
            event=other if owned else None,
            url=f"https://www.zhihu.com/question/{question}/answer/1",
            normalized_json={"relation": {"source": "zhihu_search", "decision": decision}},
        )
        db.add_all([parent, child])
        db.flush()
        task = HumanReviewTask(
            review_type="event_merge_candidate",
            status="pending",
            payload_json={
                "item_ids": [str(child.id)],
                "reason": "search enrichment has no resolved discovery parent",
            },
        )
        db.add(task)
        db.flush()
        _reattach_pending_children(db, "target", 999, datetime.now(UTC))
        db.flush()
        assert (child.event_id == target.id) is expected
        assert (task.status == "resolved") is expected
        if expected:
            assert task.payload_json["decision_audit"]["parent_review_id"] == 999
