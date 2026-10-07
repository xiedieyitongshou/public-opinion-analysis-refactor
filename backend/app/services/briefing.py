"""Snapshot one analysis window into a reusable, immutable web/email briefing."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.core.config import settings
from app.guardrails import default_guardrail_registry
from app.models import DailyReport, Event, HumanReviewTask, Item
from app.models.briefing import AnalysisRun, ReportRevision
from app.schemas.analysis import HotspotAnalysisOutput
from app.schemas.display import BriefingSection, DailyBriefing, EventCard, SourceCitation
from app.schemas.guardrails import GuardrailCheckInput
from app.services.hotspot_freshness import official_publication_exclusion

CATEGORIES = {
    "A": "跨平台 · 有官媒依据",
    "B": "单平台及搜索支持 · 有官媒依据",
    "C": "跨平台 · 暂无官媒依据",
    "D": "单平台 · 有官媒依据",
    "E": "单平台关注",
    "F": "官媒报道",
    "unknown": "来源待确认",
}
PLATFORMS = {"zhihu": "知乎", "weibo": "微博", "official": "官媒"}


def as_utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def safe_url(url: str | None) -> bool:
    parsed = urlsplit(url or "")
    return parsed.scheme in {"http", "https"} and bool(parsed.netloc)


def source_health(collection: dict, window_end: datetime) -> list[dict]:
    window_end = as_utc(window_end)
    results = {item["source_id"]: item for item in collection.get("results", [])}
    health = []
    for source in collection.get("requested_source_ids", []):
        result = results.get(source, {})
        observed = result.get("observed_at")
        stale = bool(observed and window_end - as_utc(observed) > timedelta(hours=6))
        status = result.get("status", "unknown")
        harmless = {"missing_summary", "missing_author", "html_summary", "no_raw_metrics",
                    "rss_order_not_official_rank", "missing_hot_value"}
        degraded_flags = set(result.get("quality_flags", [])) - harmless
        if stale:
            status = "stale"
        elif status == "succeeded" and (
            degraded_flags
            or (
                source in {"zhihu_hot_list", "weibo_rsshub_hot_search"}
                and not result.get("list_complete")
            )
        ):
            status = "partial"
        health.append(
            {
                "source_id": source,
                "platform": result.get("platform", "unknown"),
                "status": status,
                "observed_at": observed,
                "list_complete": result.get("list_complete", False),
                "count": result.get("returned_count", 0),
                "error": result.get("error_message"),
                "quality_flags": result.get("quality_flags", []),
            }
        )
    return health


def _citations(db, event, assembly, start, end):
    # Only accepted classification members and accepted official matches may be cited.
    detail = assembly.classification_detail_json
    member_ids = {str(row.get("item_id")) for row in detail.get("source_signals", [])}
    official = detail.get("official_support_detail") or {}
    if assembly.classification.official_support_status in {"supported", "weak_supported"}:
        member_ids.update(str(value) for value in official.get("matched_item_ids", []))
    rows = db.scalars(
        select(Item).where(Item.id.in_([int(value) for value in member_ids if value.isdigit()]))
    ).all()
    citations = []
    for item in rows:
        signal = (item.normalized_json or {}).get("source_signal", {})
        if signal.get("audit_only") or signal.get("contributes_to_classification") is False:
            continue
        is_official = item.source.source_type == "official_news"
        if not item.fetched_at:
            continue
        if not is_official and not start < as_utc(item.fetched_at) <= end:
            continue
        if is_official and official_publication_exclusion(item.published_at, end):
            continue
        flags = list(item.quality_flags_json or [])
        if not item.url:
            flags.append("missing_url")
        citations.append(
            SourceCitation(
                item_id=str(item.id),
                source_signal_id=signal.get("source_signal_id"),
                title=item.title,
                url=item.url,
                source_name=item.source.name,
                source_type=item.source.source_type,
                source_status=item.source_status,
                source_origin=item.source_origin,
                platform=item.source.platform,
                fetched_at=as_utc(item.fetched_at),
                published_at=item.published_at,
                quality_flags=flags,
                raw_metrics_used=item.raw_metrics_json or {},
                citation_role="official_support" if is_official else "supporting",
            )
        )
    return citations


def generate_briefing(db: Session, run_id: str, *, refresh=False) -> DailyBriefing:
    run = db.get(AnalysisRun, run_id)
    if run is None or not run.output_json or run.status == "failed":
        raise ValueError("需要一轮已完成且可用的分析结果")
    if run.report_id and not refresh:
        return DailyBriefing.model_validate(db.get(DailyReport, run.report_id).briefing_json)
    result = HotspotAnalysisOutput.model_validate(run.output_json)
    if result.window_end is None:
        raise ValueError("分析缺少截止时间")
    end = as_utc(result.window_end)
    start = end - timedelta(hours=24)
    heat = {value.event_id: value for value in result.analyses}
    cards, excluded = [], []
    seen = set()
    for assembly in result.classifications:
        if assembly.event_id in seen:
            continue
        seen.add(assembly.event_id)
        if assembly.run_id != run_id or as_utc(assembly.window_end) != end:
            raise ValueError("分类和日报不属于同一分析窗口")
        event = db.scalar(select(Event).where(Event.event_id == assembly.event_id))
        if event is None:
            raise ValueError("分类所指事件不存在")
        classification = assembly.classification
        citations = _citations(db, event, assembly, start, end)
        if not citations:
            # Missing evidence is a publication blocker, not a silent omission.
            excluded.append({"event_id": event.event_id, "reason": "missing_current_citation"})
            continue
        # E-class single-platform evidence is often classified low-confidence. It
        # is still an accepted hotlist topic; unresolved merge candidates never
        # reach this list and must not be confused with classification confidence.
        presence = classification.platform_presence
        platforms = [
            name
            for key, name in PLATFORMS.items()
            if (presence.official_source if key == "official" else getattr(presence, key + "_topn"))
        ]
        summary = "过去 24 小时见于" + "、".join(platforms or ["待确认来源"]) + "。"
        if not presence.official_source:
            summary += "话题信息来自平台讨论，暂无已接纳的官媒依据。"
        cards.append(
            EventCard(
                event_id=event.event_id,
                title=event.title,
                title_is_source_quote=any(citation.title == event.title for citation in citations),
                summary=summary,
                priority_category=classification.priority_category,
                category_rank=classification.category_rank,
                platform_presence=presence,
                cross_platform_match_type=classification.cross_platform_match_type,
                official_support_status=classification.official_support_status,
                confidence_level=classification.confidence_level,
                evidence_summary=assembly.evidence_summary.model_copy(update={
                    "lead": summary,
                    "discussion_evidence": [
                        f"{PLATFORMS[platform]}平台讨论来源"
                        for platform in ("zhihu", "weibo")
                        if any(citation.platform == platform for citation in citations)
                    ],
                }),
                source_citations=citations,
                platform_heat_analysis=heat.get(event.event_id),
                classification_detail=assembly.classification_detail_json,
                quality_flags=classification.quality_flags,
                risk_notes=classification.limitations,
            )
        )
    sections = []
    for platform, label in PLATFORMS.items():
        selected = [
            card
            for card in cards
            if (
                card.platform_presence.official_source
                if platform == "official"
                else getattr(card.platform_presence, platform + "_topn")
            )
        ]
        selected.sort(key=lambda card: platform_sort_key(card, platform))
        sections.append(
            BriefingSection(
                key=platform,
                section_type="platform_hotspots",
                title=label,
                event_cards=selected,
                notes=(
                    ["展示报道与来源覆盖，不计算公众热度。"]
                    if platform == "official"
                    else ["按本平台观测排序；当前离榜仍可保留在 24 小时关注窗口内。"]
                ),
            )
        )
    for category, label in CATEGORIES.items():
        selected = [card for card in cards if card.priority_category.split("_")[0] == category]
        # Cross-platform scores are deliberately not used for a total ordering.
        selected.sort(key=lambda card: (card.title, card.event_id))
        sections.append(
            BriefingSection(
                key=category,
                section_type="top_events",
                title=f"{category} · {label}",
                event_cards=selected,
            )
        )
    pending = list(db.scalars(select(HumanReviewTask).where(HumanReviewTask.status == "pending")))
    report_date = end.astimezone(ZoneInfo(settings.briefing_timezone)).date()
    health = source_health(run.collection_json or {}, end)
    risks = []
    if any(value["status"] != "succeeded" for value in health):
        risks.append("部分来源数据不完整或不可用；已保留其他来源的有效结果。")
    if pending:
        risks.append(f"有 {len(pending)} 条候选关系待人工处理，未确认关系不参与分类。")
    if excluded:
        risks.append(f"有 {len(excluded)} 个事件未进入正文，详见质量检查。")
    return DailyBriefing(
        run_id=run_id,
        report_date=report_date,
        title=f"每日关注 · {report_date}",
        summary=(
            f"过去 24 小时，共 {len(cards)} 个已接纳事件。分类按事件去重，热度分别在平台内比较。"
        ),
        sections=sections,
        created_at=datetime.now(UTC),
        window_start=start,
        window_end=end,
        timezone=settings.briefing_timezone,
        source_health=health,
        risk_notes=risks,
        pending_review_count=len(pending),
        excluded_events=excluded,
    )


def platform_sort_key(card: EventCard, platform: str):
    if platform == "official":
        citations = [c for c in card.source_citations if c.source_type == "official_news"]
        latest = max(
            (as_utc(c.published_at).timestamp() for c in citations if c.published_at), default=0
        )
        return (-len({c.source_name for c in citations}), -latest, card.event_id)
    trend = card.platform_heat_analysis.platforms[platform] if card.platform_heat_analysis else None
    present = trend.current_topn_present if trend else None
    heat = trend.latest_platform_heat if trend else None
    rank = heat.primary_platform_rank if heat and platform == "zhihu" else None
    score = heat.platform_score if heat else None
    return (
        0 if present is True else 1 if present is None else 2,
        rank if rank is not None else 9999,
        -(score or 0),
        card.event_id,
    )


def review_briefing(briefing: DailyBriefing) -> dict:
    payload = briefing.model_dump(mode="json")
    checks = default_guardrail_registry.run(
        GuardrailCheckInput(payload=payload, subject_type="daily_briefing"),
        rule_types={"briefing_publish", "source_quality", "classification_quality", "summary"},
    ).model_dump(mode="json")
    errors = []
    start, end = briefing.window_start, briefing.window_end
    if not start or not end or end - start != timedelta(hours=24):
        errors.append("日报必须使用固定的 24 小时窗口")
    for section in briefing.sections:
        ids = [card.event_id for card in section.event_cards]
        if len(set(ids)) != len(ids):
            errors.append("同一栏目包含重复事件")
        for card in section.event_cards:
            detail = card.classification_detail
            if (
                detail.get("run_id") != briefing.run_id
                or as_utc(detail.get("window_start") or datetime.min) != start
                or as_utc(detail.get("window_end") or datetime.min) != end
            ):
                errors.append(f"{card.event_id}: 分类窗口不一致")
            analysis = card.platform_heat_analysis
            if analysis is None:
                errors.append(f"{card.event_id}: 缺少同窗口的平台分析")
            if card.platform_presence.official_source and not any(
                citation.source_type == "official_news" for citation in card.source_citations
            ):
                errors.append(f"{card.event_id}: 官媒分类缺少已接纳的官媒引用")
            for citation in card.source_citations:
                if not safe_url(citation.url):
                    errors.append(f"{card.event_id}: 来源链接无效")
                if citation.source_type != "official_news" and start and end and (
                    not start < as_utc(citation.fetched_at) <= end
                ):
                    errors.append(f"{card.event_id}: 引用采集时间超出窗口")
                if as_utc(citation.fetched_at) > as_utc(briefing.created_at):
                    errors.append(f"{card.event_id}: 引用尚未采集")
                if (
                    citation.source_type == "official_news"
                    and end
                    and (official_publication_exclusion(citation.published_at, end))
                ):
                    errors.append(f"{card.event_id}: 官媒发布时间不符合窗口")
    if any(value["reason"] == "missing_current_citation" for value in briefing.excluded_events):
        errors.append("存在缺少有效引用的分类事件")
    if not briefing.source_health or not any(
        value["status"] in {"succeeded", "partial"} for value in briefing.source_health
    ):
        errors.append("没有可用来源，无法发布本期简报")
    checks["errors"] = sorted(set(errors))
    checks["warnings"] = briefing.risk_notes
    checks["blocks_publish"] = checks["blocks_publish"] or bool(errors)
    checks["status"] = (
        "block"
        if checks["blocks_publish"]
        else ("warn" if briefing.risk_notes or checks["status"] == "warn" else "pass")
    )
    return checks


def save_briefing(db: Session, briefing: DailyBriefing, supersedes_id=None) -> DailyReport:
    quality = review_briefing(briefing)
    briefing = briefing.model_copy(deep=True)
    briefing.draft_status = (
        "draft_blocked" if quality["blocks_publish"] else "draft_ready_for_review"
    )
    content = briefing.model_dump(mode="json")
    stable = {
        key: value for key, value in content.items() if key not in {"created_at", "daily_report_id"}
    }
    fingerprint = hashlib.sha256(json.dumps(stable, sort_keys=True).encode()).hexdigest()
    existing = db.scalar(select(ReportRevision).where(ReportRevision.fingerprint == fingerprint))
    if existing:
        return db.get(DailyReport, existing.report_id)
    report = DailyReport(
        report_date=as_utc(briefing.window_end).astimezone(ZoneInfo(briefing.timezone)).date(),
        title=briefing.title,
        summary=briefing.summary,
        briefing_json=content,
        status="blocked" if quality["blocks_publish"] else "draft",
        quality_review_json=quality,
        guardrail_summary_json=quality,
        source_citations_json=list(
            {
                (c.item_id, c.url): c.model_dump(mode="json")
                for section in briefing.sections
                for card in section.event_cards
                for c in card.source_citations
            }.values()
        ),
    )
    db.add(report)
    db.flush()
    report.briefing_json = {**content, "daily_report_id": str(report.id)}
    db.add(
        ReportRevision(
            report_id=report.id,
            run_id=briefing.run_id,
            fingerprint=fingerprint,
            supersedes_id=supersedes_id,
        )
    )
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        revision = db.scalar(
            select(ReportRevision).where(ReportRevision.fingerprint == fingerprint)
        )
        if revision is None:
            raise
        return db.get(DailyReport, revision.report_id)
    return report


def approve_report(db: Session, report: DailyReport, *, actor="admin") -> DailyReport:
    if report.status in {"approved", "sent"}:
        return report
    checks = review_briefing(DailyBriefing.model_validate(report.briefing_json))
    if checks["blocks_publish"]:
        raise ValueError("质量检查未通过，不能确认发布：" + "；".join(checks["errors"]))
    revision = db.get(ReportRevision, report.id)
    if revision is None:
        raise ValueError("旧日报缺少固定版本，请重新生成")
    revision.approval_json = {"actor": actor, "approved_at": datetime.now(UTC).isoformat()}
    report.status = "approved"
    report.published_at = datetime.now(UTC)
    db.commit()
    return report
