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


def trend_label(card, platform):
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
    heat = trend.latest_platform_heat if trend else None
    if platform == "zhihu" and heat and heat.primary_platform_rank is not None:
        presence += f" · 第 {heat.primary_platform_rank} 名"
    elif platform == "weibo" and heat and heat.platform_score is not None:
        presence += f" · 本平台热度 {heat.platform_score:.3f}"
    if trend and trend.trend_status != "unknown":
        if trend.trend_basis == "rank" and trend.rank_delta is not None:
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
                f"{e(c.source_name)}</a> · "
                for c in card.source_citations
                if safe_url(c.url)
            ).removesuffix(" · ")
            body.append(f'''<article id="{e(anchor)}" style="padding:16px 0 20px;border-bottom:1px solid #e1e8e9">
                <p style="color:{TEAL};font-size:12px;margin:0 0 9px">{e(category)} 类 · {e(trend_label(card, key))}</p>
                <h4 style="font-size:18px;line-height:1.6;margin:0 0 9px">{e(display_title(card))}</h4>
                <p style="font-size:14px;line-height:1.8;margin:0;color:{MUTED}">{e(card.summary)}</p>
                <p style="font-size:12px;line-height:1.8;margin:12px 0 0">依据 / {cites}</p></article>''')
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
        统计窗口内曾上榜与当前仍在榜分别展示。趋势依据本平台最新两轮可比观测；样本不足或不可比时显示待确认。
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
                lines.extend([trend_label(card, section.key), card.summary])
                lines.extend(
                    f"{c.source_name}: {c.url}" for c in card.source_citations if safe_url(c.url)
                )
        if not section.event_cards:
            lines.append("本期暂无")
    return "\n".join(lines)
