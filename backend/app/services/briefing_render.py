"""One renderer for an email-safe HTML report, browser preview and text fallback."""
# Inline email CSS is kept together so it can be inspected against mail-client output.
# ruff: noqa: E501

from html import escape

from app.schemas.display import DailyBriefing
from app.services.briefing import as_utc, safe_url

INK = "#172c38"
MUTED = "#627581"
TEAL = "#087f78"


def display_title(card):
    return f"来源标题：「{card.title}」" if card.title_is_source_quote else card.title


def stamp(value, timezone):
    from zoneinfo import ZoneInfo

    return as_utc(value).astimezone(ZoneInfo(timezone)).strftime("%m-%d %H:%M")


def unknown_trend_reason(trend):
    if trend is None:
        return "本期未保存趋势观测数据"
    flags = set(trend.quality_flags)
    reasons = [
        ("source_unavailable", "最近一次来源采集失败，无法比较"),
        ("stale_observation", "最近观测已过期，缺少近期可比数据"),
        ("incomplete_list", "榜单数据不完整，无法连续比较"),
        ("missing_sampling_config", "未配置采样间隔，无法确认观测是否连续"),
        ("no_window_observations", "本期没有可用的榜单采集记录"),
        ("observation_gap", "相邻采集间隔过长，无法连续比较"),
        ("incomparable_scope", "前后采集的榜单范围不一致"),
        ("sampling_changed", "前后采样口径不一致"),
        ("score_config_changed", "前后热度计算口径不一致"),
        ("incomparable_scores", "前后两轮热度数据不满足可比条件"),
    ]
    for flag, reason in reasons:
        if flag in flags:
            return reason
    if trend.snapshot_presence_count == 1:
        return ("仅有1次在榜观测，最近一轮已离榜，缺少连续可比热度"
                if trend.current_topn_present is False
                else "仅有1次在榜观测，需要两次连续可比数据")
    if trend.current_topn_present is False:
        return "最近一轮已离榜，缺少连续两轮在榜热度"
    if "insufficient_trend_metrics" in flags:
        return ("缺少两轮可比的CLI互动指标" if trend.platform == "weibo"
                else "缺少两轮可比的榜位或热度指标")
    if "insufficient_history" in flags:
        return "有效历史不足，需要两次连续可比数据"
    return "缺少连续可比的榜单观测数据"


def trend_label(card, platform, timezone="Asia/Shanghai"):
    if platform == "shared":
        return "知乎：" + trend_label(card, "zhihu", timezone) + "；微博：" + trend_label(card, "weibo", timezone)
    if platform == "official":
        count = len(
            {c.source_name for c in card.source_citations if c.source_type == "official_news"}
        )
        return f"{count} 个官媒来源 · 报道覆盖"
    analysis = card.platform_heat_analysis
    trend = analysis.platforms[platform] if analysis else None
    presence = {True: "当前在榜", False: "当前离榜", None: "当前榜单状态未知"}[
        trend.current_topn_present if trend else None
    ]
    labels = {"rising": "升温 ↑", "stable": "平稳 →", "cooling": "降温 ↓", "unknown": "趋势待确认"}
    movement = labels.get(trend.trend_status, "趋势待确认") if trend else "趋势待确认"
    if not trend or trend.trend_status == "unknown":
        movement += f"（{unknown_trend_reason(trend)}）"
    heat = trend.latest_platform_heat if trend else None
    historical = bool(trend and heat is None and trend.last_observed_heat is not None)
    if historical:
        heat = trend.last_observed_heat
    if platform == "zhihu" and heat and heat.primary_platform_rank is not None:
        presence += f" · {'最近在榜' if historical else ''}第 {heat.primary_platform_rank} 名"
    if platform == "zhihu":
        if heat and heat.platform_score is not None:
            native_text = heat.raw_metrics_used.get("heat_text")
            if native_text:
                presence += f" · 知乎热榜 {native_text}"
            else:
                presence += f" · 知乎站内热度 {heat.platform_score:.3f}（系统计算，仅限知乎内比较）"
        else:
            presence += " · 知乎站内热度暂不可用"
    elif platform == "weibo" and heat and heat.platform_score is not None:
        presence += f" · {'最近采集热度' if historical else '本平台热度'} {heat.platform_score:.3f}"
    if historical and trend.last_observed_at:
        presence += f"（热度采于 {stamp(trend.last_observed_at, timezone)}，非当前值）"
    checked = stamp(trend.last_collection_at, timezone) if trend and trend.last_collection_at else "未记录"
    check_label = "最后采集尝试" if trend and "source_unavailable" in trend.quality_flags else "最后采集"
    presence += f" · {check_label}：{checked}"
    if trend and trend.trend_status != "unknown":
        if trend.trend_basis == "native_heat" and trend.native_heat_change_ratio is not None:
            movement += f" · 热度变化 {trend.native_heat_change_ratio:+.1%}"
        elif trend.trend_basis == "rank" and trend.rank_delta is not None:
            movement += f" · 榜位变化 {trend.rank_delta:+d}"
        elif trend.trend_basis == "platform_score" and trend.score_delta is not None:
            movement += f" · 本平台分数变化 {trend.score_delta:+.3f}"
    return f"{presence} · {movement}"


def render_report(briefing: DailyBriefing, *, email=False, status="draft") -> str:
    e = escape
    window = (
        f"{stamp(briefing.window_start, briefing.timezone)} — "
        f"{stamp(briefing.window_end, briefing.timezone)} · {briefing.timezone}"
    )
    status_label = {
        "draft": "待确认草稿",
        "blocked": "检查未通过",
        "approved": "已确认版本",
        "sent": "已投递版本",
    }.get(status, status)
    nav = (
        ""
        if email
        else (
            '<div style="margin-bottom:24px"><a href="/" style="color:#087f78">← 简报与管理后台</a>'
            '<span style="float:right">网页与邮件共用数据</span></div>'
        )
    )
    header = f"""{nav}<p style="letter-spacing:3px;font-size:12px;color:{TEAL};margin:0">DAILY BRIEFING</p>
        <h1 style="font-size:30px;line-height:1.35;margin:12px 0">{e(briefing.title)}</h1>
        <p style="color:{MUTED};font-size:13px">{e(window)}</p>
        <p style="font-size:16px;line-height:1.8">{e(briefing.summary)}</p>
        <table role="presentation" style="width:100%;border-top:2px solid {INK};border-bottom:1px solid #dbe4e5;margin:24px 0"><tr>
        <td style="padding:18px 0"><b style="font-size:28px">{briefing.event_count}</b><br>个独立事件</td>
        <td><b style="font-size:28px">{briefing.source_citation_count}</b><br>条来源引用</td>
        <td style="font-size:13px">{e(status_label)}<br>版本 #{e(briefing.daily_report_id or "预览")}</td>
        </tr></table>"""
    warnings = "".join(f'<p style="margin:6px 0">{e(note)}</p>' for note in briefing.risk_notes)
    if warnings:
        header += f'<div style="background:#fff5dd;padding:14px 18px;font-size:13px;line-height:1.6">{warnings}</div>'
    health_labels = {
        "succeeded": "可用",
        "partial": "部分可用",
        "failed": "不可用",
        "skipped": "未采集",
        "unknown": "未知",
        "stale": "已过期",
    }
    names = {
        "zhihu_hot_list": "知乎热榜",
        "zhihu_search": "知乎搜索",
        "weibo_rsshub_hot_search": "微博热榜",
        "chinanews_scroll_rss": "中新网",
        "people_politics_rss": "人民网",
        "xinhua_politics_rss": "新华网",
    }
    health = " · ".join(
        f"{names.get(s['source_id'], s['source_id'])}：{health_labels.get(s['status'], '未知')}"
        f"（{stamp(s['observed_at'], briefing.timezone) if s.get('observed_at') else '更新时间未知'}）"
        for s in briefing.source_health
    )
    header += f'<p style="font-size:12px;color:{MUTED};line-height:1.8">{e(health)}</p>'
    body, anchors = [], {}
    body.append('<h2 style="font-size:23px;margin:38px 0 8px">01 / 各平台在关注什么</h2>')
    for section in briefing.sections:
        if section.section_type != "platform_hotspots":
            continue
        key = section.key
        body.append(
            f'<h3 style="font-size:20px;border-bottom:2px solid {TEAL};padding-bottom:10px;margin-top:30px">'
            f'{e(section.title)} <span style="font-size:13px;color:{MUTED}">{len(section.event_cards)} 个事件</span></h3>'
        )
        if not section.event_cards:
            body.append(f'<p style="color:{MUTED}">本期没有已接纳的事件；请结合来源状态阅读。</p>')
        for card in section.event_cards:
            anchor = f"{key}-{card.event_id}"
            anchors.setdefault(card.event_id, anchor)
            category = card.priority_category.split("_")[0]
            cites = "".join(
                f'<a href="{e(c.url, quote=True)}" rel="noopener noreferrer" style="color:{TEAL};text-decoration:underline">'
                f"{e(c.source_name)}</a>"
                + (f"（此前报道 · {e(stamp(c.published_at, briefing.timezone))}）"
                   if c.is_prior_report else "") + " · "
                for c in card.source_citations
                if safe_url(c.url)
            ).removesuffix(" · ")
            excerpts = " · ".join(
                f'<a href="{e(row["url"], quote=True)}" rel="noopener noreferrer">摘要原文</a>'
                for row in card.classification_detail.get("summary_excerpts", [])
                if safe_url(row.get("url"))
            )
            label = ("知乎：" + trend_label(card, "zhihu", briefing.timezone) + "；微博：" + trend_label(card, "weibo", briefing.timezone)) if key == "shared" else trend_label(card, key, briefing.timezone)
            body.append(f'''<article id="{e(anchor)}" style="padding:16px 0 20px;border-bottom:1px solid #e1e8e9">
                <p style="color:{TEAL};font-size:12px;margin:0 0 9px">{e(category)} 类 · {e(label)}</p>
                <h4 style="font-size:18px;line-height:1.6;margin:0 0 9px">{e(display_title(card))}</h4>
                <p style="font-size:14px;line-height:1.8;margin:0 0 8px">{e(card.source_summary)}</p>
                <p style="font-size:14px;line-height:1.8;margin:0;color:{MUTED}">{e(card.summary)}</p>
                <p style="font-size:12px;line-height:1.8;margin:12px 0 0">依据 / {cites} {excerpts}</p></article>''')
    body.append(
        '<h2 style="font-size:23px;margin:38px 0 8px">02 / 事件的来源分类</h2>'
        f'<p style="color:{MUTED};font-size:13px">按已接纳的来源组合分类，同一平台的热榜与搜索仍算一个平台。</p>'
    )
    for section in briefing.sections:
        if section.section_type != "top_events":
            continue
        links = []
        for card in section.event_cards:
            if card.event_id in anchors:
                links.append(
                    f'<a href="#{e(anchors[card.event_id])}" style="color:{INK}">{e(display_title(card))}</a>'
                )
            else:
                links.append(e(display_title(card)))
        contents = "<br>".join(links) or '<span style="color:#627581">本期暂无</span>'
        body.append(
            f'<div style="padding:14px 0;border-bottom:1px solid #e1e8e9;font-size:14px;line-height:1.9">'
            f"<b>{e(section.title)} · {len(section.event_cards)}</b><br>{contents}</div>"
        )
    footer = f"""<p style="margin-top:30px;font-size:12px;color:{MUTED};line-height:1.9">
        在榜状态截至所标注的最后采集时间，不是打开网页时的实时榜单。离榜后可保留最近热度，历史值不代表当前值。
        趋势依据本平台最新两轮可比观测；待确认时列出原因。
        官媒仅表示报道覆盖。此简报保留来源依据与不确定性。<br>生成于 {e(stamp(briefing.created_at, briefing.timezone))}</p>"""
    return f"""<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1"><title>{e(briefing.title)}</title></head>
        <body style="margin:0;background:#f3f5f3;color:{INK};font-family:-apple-system,BlinkMacSystemFont,'Segoe UI','Microsoft YaHei',sans-serif">
        <table role="presentation" style="width:100%;border-collapse:collapse"><tr><td style="padding:20px 12px">
        <div style="max-width:720px;margin:0 auto;background:#fff;padding:24px;box-sizing:border-box">{header}{"".join(body)}{footer}</div>
        </td></tr></table></body></html>"""


def render_text(briefing: DailyBriefing) -> str:
    lines = [
        briefing.title,
        f"{stamp(briefing.window_start, briefing.timezone)} — "
        f"{stamp(briefing.window_end, briefing.timezone)} ({briefing.timezone})",
        briefing.summary,
        *briefing.risk_notes,
    ]
    for section in briefing.sections:
        lines.extend(["", section.title])
        for card in section.event_cards:
            lines.append(f"[{card.priority_category.split('_')[0]}] {display_title(card)}")
            if section.section_type == "platform_hotspots":
                lines.extend([trend_label(card, section.key, briefing.timezone), card.source_summary, card.summary])
                lines.extend(
                    "摘要原文: " + row["url"]
                    for row in card.classification_detail.get("summary_excerpts", [])
                    if safe_url(row.get("url"))
                )
                lines.extend(
                    f"{c.source_name}"
                    + (f"（此前报道 · {stamp(c.published_at, briefing.timezone)}）"
                       if c.is_prior_report else "")
                    + f": {c.url}" for c in card.source_citations if safe_url(c.url)
                )
        if not section.event_cards:
            lines.append("本期暂无")
    return "\n".join(lines)
