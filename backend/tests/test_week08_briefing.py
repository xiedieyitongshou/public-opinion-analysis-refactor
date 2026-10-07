"""Week 8 acceptance: real offline analysis through report/review/render/delivery."""

import json
from copy import deepcopy
from datetime import timedelta

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from test_hotspot_analysis import (
    OTHER_TITLE,
    START,
    TITLE,
    register_official_feed,
    register_weibo_feed,
    request,
)
from test_hotspot_analysis import (
    environment as environment,
)

from app.api.auth import require_admin
from app.api.briefing import retry_review_refresh, review_queue
from app.core.config import settings
from app.main import create_app
from app.models import DailyReport, Event, EventSnapshot, HumanReviewTask
from app.models.briefing import AnalysisRun, DailyDispatch, EmailDelivery, RequestLog
from app.schemas.display import DailyBriefing
from app.schemas.human_review import HumanReviewDecisionInput
from app.services.briefing import approve_report, as_utc, generate_briefing, review_briefing
from app.services.briefing_email import dispatch_daily, send_report
from app.services.briefing_jobs import (
    acquire_lease,
    execute_analysis,
    queue_analysis,
    release_lease,
)
from app.services.briefing_render import render_report, render_text
from app.services.human_review import decide_human_review_task
from app.services.official_search import OfficialSearchClient
from app.services.request_usage import (
    RequestBudgetExceeded,
    http_request,
    request_scope,
    usage_summary,
)


def run_report(environment, run_id="brief-1", **kwargs):
    db, client, _ = environment
    row = execute_analysis(db, request(run_id, client.time, **kwargs))
    assert row.status != "failed", row.error
    assert row.report_id, row.error
    report = db.get(DailyReport, row.report_id)
    assert report.status == "draft", report.quality_review_json
    return report


def cards(briefing):
    return {card.event_id: card for section in briefing.sections for card in section.event_cards}


def test_job_stays_active_until_report_is_saved(environment, monkeypatch):
    db, client, _ = environment
    called = []

    def generating(session, run_id):
        row = session.get(AnalysisRun, run_id)
        assert row.status == "running" and row.finished_at is None and row.report_id is None
        duplicate = queue_analysis(session, request("another-click", client.time))
        assert duplicate.run_id == run_id
        called.append(run_id)
        return generate_briefing(session, run_id)

    monkeypatch.setattr("app.services.briefing.generate_briefing", generating)
    report = run_report(environment)
    row = db.get(AnalysisRun, "brief-1")
    assert called == ["brief-1"] and row.report_id == report.id
    assert row.status in {"partial", "succeeded"} and row.finished_at is not None


def test_report_filters_deduplicates_and_preserves_publication(environment):
    db, client, _ = environment
    report = run_report(environment)
    briefing = DailyBriefing.model_validate(report.briefing_json)
    assert briefing.event_count == 2
    assert briefing.source_citation_count == 2
    assert len(briefing.sections) == 10
    assert (
        len(next(section for section in briefing.sections if section.key == "E").event_cards) == 2
    )
    assert not next(section for section in briefing.sections if section.key == "weibo").event_cards
    assert "趋势待确认" in render_report(briefing)
    assert not review_briefing(briefing)["blocks_publish"]
    approve_report(db, report)
    snapshot = deepcopy(report.briefing_json)
    client.time += timedelta(hours=2)
    client.titles = [TITLE, OTHER_TITLE]
    second = run_report(environment, "brief-2")
    assert second.id != report.id
    assert report.briefing_json == snapshot
    for card in cards(DailyBriefing.model_validate(second.briefing_json)).values():
        assert card.platform_heat_analysis.platforms["zhihu"].trend_status in {"rising", "cooling"}
    # Reusing a run ID cannot launch another external collection.
    execute_analysis(db, request("brief-2", client.time))
    assert client.calls == 2
    assert db.scalar(select(func.count(RequestLog.id))) == 0  # local fixture made no HTTP requests


def test_offlist_24h_history_expiry_and_source_outage(environment):
    db, client, _ = environment
    run_report(environment)
    client.time += timedelta(hours=2)
    client.titles = [OTHER_TITLE]
    second = run_report(environment, "offlist")
    briefing = DailyBriefing.model_validate(second.briefing_json)
    target = next(card for card in cards(briefing).values() if card.title == TITLE)
    assert target.platform_heat_analysis.platforms["zhihu"].current_topn_present is False
    assert "当前离榜" in render_text(briefing)
    client.time = START + timedelta(hours=25)
    third = run_report(environment, "expired")
    assert third.briefing_json["event_count"] == 1
    assert TITLE not in render_text(DailyBriefing.model_validate(third.briefing_json))
    client.time += timedelta(hours=2)
    client.failed = True
    row = execute_analysis(db, request("outage", client.time))
    report = db.get(DailyReport, row.report_id)
    assert report.status == "blocked"  # no source succeeded in this round
    assert report.briefing_json["event_count"] == 1  # history remains visible as unknown


def test_natural_cross_platform_and_official_views(environment, monkeypatch):
    db, client, _ = environment
    client.titles = [TITLE]
    register_weibo_feed(environment, monkeypatch, {"titles": [TITLE]})
    report = run_report(environment, sources=["zhihu_hot_list", "weibo_rsshub_hot_search"])
    briefing = DailyBriefing.model_validate(report.briefing_json)
    assert briefing.event_count == 1
    assert next(section for section in briefing.sections if section.key == "C").event_cards
    assert next(section for section in briefing.sections if section.key == "weibo").event_cards
    assert next(section for section in briefing.sections if section.key == "zhihu").event_cards
    assert "趋势待确认" in render_report(briefing)


def test_recent_official_and_stale_official_report_admission(environment, monkeypatch):
    db, client, _ = environment
    register_official_feed(environment, monkeypatch, published_at=START - timedelta(hours=1))
    report = run_report(environment, sources=["zhihu_hot_list", "chinanews_scroll_rss"])
    briefing = DailyBriefing.model_validate(report.briefing_json)
    assert next(section for section in briefing.sections if section.key == "official").event_cards
    client.time += timedelta(hours=25)
    later = run_report(
        environment, "old-official", sources=["zhihu_hot_list", "chinanews_scroll_rss"]
    )
    assert not next(
        section
        for section in DailyBriefing.model_validate(later.briefing_json).sections
        if section.key == "official"
    ).event_cards


def test_review_recomputes_original_observation_and_new_draft(environment):
    db, client, _ = environment
    client.titles = ["热议"]
    report = run_report(environment)
    assert report.briefing_json["event_count"] == 0
    approve_report(db, report)
    original = deepcopy(report.briefing_json)
    task = db.scalar(select(HumanReviewTask).where(HumanReviewTask.status == "pending"))
    decision = decide_human_review_task(
        db,
        task,
        HumanReviewDecisionInput(
            decision="create_new_event",
            reviewer="tester",
            event_title="热议话题",
        ),
    )
    assert decision.status == "succeeded"
    assert decision.refresh_status == "refreshed", decision.message
    assert decision.report_id != report.id
    event = db.scalar(select(Event))
    assert as_utc(event.last_seen_at) == START
    assert all(as_utc(row.snapshot_at) == START for row in db.scalars(select(EventSnapshot)))
    new_report = db.get(DailyReport, decision.report_id)
    assert new_report.briefing_json["event_count"] == 1
    assert new_report.briefing_json["pending_review_count"] == 0
    assert report.status == "approved"
    assert report.briefing_json == original


def test_quality_blocks_missing_citations_window_and_html_injection(environment):
    report = run_report(environment)
    briefing = DailyBriefing.model_validate(report.briefing_json)
    card = briefing.sections[0].event_cards[0]
    card.source_citations[0].url = "javascript:alert(1)"
    assert review_briefing(briefing)["blocks_publish"]
    card.title = '<script>alert("bad")</script>'
    html = render_report(briefing)
    assert "<script>" not in html and "javascript:" not in html
    assert "&lt;script&gt;" in html
    card.classification_detail["run_id"] = "another-run"
    assert any("窗口不一致" in message for message in review_briefing(briefing)["errors"])


def test_source_title_is_quoted_but_generated_fact_claims_still_block(environment):
    _, client, _ = environment
    client.titles = ["某品牌公司确认召回某型号产品引发消费者讨论"]
    report = run_report(environment)
    briefing = DailyBriefing.model_validate(report.briefing_json)
    card = briefing.sections[0].event_cards[0]
    assert card.title_is_source_quote
    assert "来源标题：「" in render_text(briefing)
    assert "来源标题：「" in render_report(briefing)
    assert not review_briefing(briefing)["blocks_publish"]
    card.summary = "事实证明该产品存在安全问题。"
    assert review_briefing(briefing)["blocks_publish"]
    card.summary = "平台正在讨论这个话题。"
    card.title = "已经证实出现了全新的事故"
    assert review_briefing(briefing)["blocks_publish"]  # not an exact cited title


def test_review_keeps_original_cached_query_evidence_without_new_requests(environment, monkeypatch):
    db, client, _ = environment
    client.titles = [TITLE, "热议"]
    calls = []

    def transport(request):
        calls.append(request)
        if request.method == "POST":
            return httpx.Response(200, json={"code": 0, "data": {"records": []}})
        return httpx.Response(200, text="var docArr = " + json.dumps([{
            "title": TITLE, "content_without_tag": TITLE,
            "url": "https://www.chinanews.com.cn/cj/current.shtml",
            "pubtime": (START - timedelta(hours=1)).isoformat(),
        }]) + ";")

    search = OfficialSearchClient(httpx.Client(transport=httpx.MockTransport(transport)))
    monkeypatch.setattr("app.services.official_search.OfficialSearchClient", lambda: search)
    data = request("cached-evidence", client.time)
    data.official_search_enabled = True
    run = execute_analysis(db, data)
    report = db.get(DailyReport, run.report_id)
    before = cards(DailyBriefing.model_validate(report.briefing_json))
    target = next(iter(before.values()))
    assert target.platform_presence.official_source
    assert any(c.source_origin == "official_search" for c in target.source_citations), (
        target.classification_detail.get("official_support_detail"),
        [c.model_dump() for c in target.source_citations],
    )
    assert not review_briefing(DailyBriefing.model_validate(report.briefing_json))["blocks_publish"]
    task = db.scalar(select(HumanReviewTask).where(HumanReviewTask.status == "pending"))
    result = decide_human_review_task(
        db, task, HumanReviewDecisionInput(decision="ignore", reviewer="tester"),
    )
    assert result.refresh_status == "refreshed", result.message
    updated = db.get(DailyReport, result.report_id)
    after = cards(DailyBriefing.model_validate(updated.briefing_json))[target.event_id]
    assert after.platform_presence.official_source
    assert after.priority_category == target.priority_category
    assert len(calls) == 2


def test_failed_review_refresh_remains_actionable_and_retry_clears_queue(environment, monkeypatch):
    db, client, _ = environment
    client.titles = ["热议"]
    run_report(environment)
    task = db.scalar(select(HumanReviewTask))
    with monkeypatch.context() as patch:
        def fail(*args):
            raise RuntimeError("simulated refresh failure")
        patch.setattr("app.services.review_refresh.refresh_after_review", fail)
        result = decide_human_review_task(
            db, task, HumanReviewDecisionInput(decision="create_new_event", reviewer="tester"),
        )
    assert result.status == "succeeded" and result.refresh_status == "failed"
    assert task.status == "resolved"
    queued = review_queue(db, limit=50)
    assert queued["total"] == 1 and queued["items"][0].id == task.id
    updated = retry_review_refresh(task.id, db)
    assert db.get(DailyReport, updated["report_id"]).briefing_json["event_count"] == 1
    assert review_queue(db, limit=50)["total"] == 0


def configure_email(monkeypatch):
    for name, value in {
        "smtp_host": "localhost",
        "smtp_port": 1025,
        "smtp_security": "plain",
        "email_from": "briefing@example.com",
        "email_recipients": ["test@example.com"],
    }.items():
        monkeypatch.setattr(settings, name, value)


def test_email_approval_idempotency_html_text_and_unknown_outcome(environment, monkeypatch):
    db, _, _ = environment
    configure_email(monkeypatch)
    report = run_report(environment)
    with pytest.raises(ValueError, match="已确认"):
        send_report(db, report)
    approve_report(db, report)
    messages = []

    def sender(message, recipient):
        messages.append(message)
        return "unknown", "connection_lost_after_DATA"

    rows = send_report(db, report, sender=sender)
    assert rows[0].status == "unknown"
    send_report(db, report, sender=sender)
    assert len(messages) == 1
    message = messages[0]
    briefing = DailyBriefing.model_validate(report.briefing_json)
    assert message.get_body(preferencelist=("plain",)).get_content().strip().replace(
        "\r\n", "\n"
    ) == render_text(briefing)
    assert (
        message.get_body(preferencelist=("html",)).get_content().strip().replace("\r\n", "\n")
        == render_report(briefing, email=True, status="approved").strip()
    )


def test_email_retries_are_bounded_and_scheduled_reports_require_approval(environment, monkeypatch):
    db, _, _ = environment
    configure_email(monkeypatch)
    report = run_report(environment)
    now = START + timedelta(hours=1)
    assert dispatch_daily(db, now=now) == []
    approve_report(db, report)
    attempts = []

    def rejected(message, recipient):
        attempts.append(1)
        return "failed", "RCPT rejected"

    for _ in range(6):
        send_report(db, report, sender=rejected)
    assert len(attempts) == settings.email_max_attempts
    assert db.scalar(select(EmailDelivery)).status == "failed"


def test_daily_dispatch_freezes_version_and_deduplicates_recipients(environment, monkeypatch):
    db, client, _ = environment
    configure_email(monkeypatch)
    monkeypatch.setattr(settings, "email_recipients", ["first@example.com", "second@example.com"])
    report = run_report(environment)
    approve_report(db, report)
    sent = []

    def sender(message, recipient):
        sent.append((message["Message-ID"], recipient))
        return "sent", None

    dispatch_daily(db, now=START + timedelta(hours=10), sender=sender)
    client.time += timedelta(hours=2)
    revised = run_report(environment, "newer-same-day")
    approve_report(db, revised)
    dispatch_daily(db, now=START + timedelta(hours=11), sender=sender)
    assert len(sent) == 2
    assert db.get(DailyDispatch, report.report_date.isoformat()).report_id == report.id
    assert not list(db.scalars(select(EmailDelivery).where(EmailDelivery.report_id == revised.id)))
    client.time += timedelta(days=1)
    tomorrow = run_report(environment, "tomorrow")
    approve_report(db, tomorrow)
    dispatch_daily(db, now=START + timedelta(days=1, hours=10), sender=sender)
    assert len(sent) == 4 and len({message_id for message_id, _ in sent}) == 4


def test_http_requests_redirects_budgets_and_failures_are_separate_from_tools(environment):
    db, _, _ = environment

    def transport(request):
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"location": "/ok"})
        return httpx.Response(200, json={})

    client = httpx.Client(transport=httpx.MockTransport(transport))
    with request_scope("usage", {"test": 2}, 30) as scope:
        response = http_request(
            client, "test", "get", "https://example.com/redirect", follow_redirects=True
        )
        assert response.status_code == 200
        with pytest.raises(RequestBudgetExceeded):
            http_request(client, "test", "get", "https://example.com/ok")
        scope.flush(db)
    summary = usage_summary(db)
    assert summary["requests"][0]["count"] == 2
    assert summary["requests"][0]["cost"] is None
    assert summary["tool_call_count"] == 0


def test_auth_fail_closed_cookie_csrf_and_lease_exclusion(environment, monkeypatch):
    db, _, _ = environment
    app = create_app()
    client = TestClient(app)
    assert client.get("/ops/events").status_code == 401
    assert client.post("/auth/session", json={"token": "test-admin"}).status_code == 403
    assert (
        client.post(
            "/auth/session", json={"token": "wrong"}, headers={"Origin": "http://testserver"}
        ).status_code
        == 401
    )
    response = client.post(
        "/auth/session", json={"token": "test-admin"}, headers={"Origin": "http://testserver"}
    )
    assert response.status_code == 200
    assert "HttpOnly" in response.headers["set-cookie"]
    assert client.post("/api/jobs", json={}).status_code == 403
    assert require_admin not in app.dependency_overrides
    monkeypatch.setattr(settings, "admin_token", None)
    assert client.get("/ops/events").status_code == 503
    assert acquire_lease(db, "analysis", "worker-1")
    assert not acquire_lease(db, "analysis", "worker-2")
    release_lease(db, "analysis", "worker-1")
    assert acquire_lease(db, "analysis", "worker-2")
