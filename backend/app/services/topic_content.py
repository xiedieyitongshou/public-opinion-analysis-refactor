"""Bounded, extractive context shared by matching and briefing display."""

import re
from html import unescape

from app.services.match_documents import value
from app.services.weibo_relevance import clean_body

# Presentation-only cleanup; unknown bracketed text remains part of the source.
WEIBO_EMOTICON_NAMES = """
笑cry doge 二哈 喵喵 允悲 哈哈 嘻嘻 呵呵 可爱 爱你 心 伤心 泪 悲伤 抓狂
怒 怒骂 吃惊 晕 汗 黑线 困 睡 懒得理你 挖鼻 挖鼻屎 鄙视 右哼哼 左哼哼
哼 委屈 可怜 失望 思考 疑问 问号 吐 衰 生病 亲亲 抱抱 抱一抱 太开心 鼓掌
赞 good 威武 加油 加油加油 嘿嘿 酷 馋嘴 色 偷笑 笑而不语 笑哈哈 微笑 皱眉
摊手 捂脸 跪了 费解 苦涩 裂开 破涕为笑 兔子 熊猫 奥特曼 笑脸 哭 哭泣
鲜花 礼物 蛋糕 蜡烛 太阳 月亮 浮云 雨 沙尘暴 雪花 彩虹 中国赞 中国心
给力 围观 话筒 音乐 电影 干杯 祈祷 握手 耶 OK ok 拳头 弱 赞啊 啊 666
""".split()
WEIBO_EMOTICON_RE = re.compile(
    r"\[(?:" + "|".join(re.escape(name) for name in WEIBO_EMOTICON_NAMES) + r")\]",
    re.IGNORECASE,
)
# Include modifiers, flags, keycaps and joined emoji as whole sequences, so no
# variation selectors, skin tones or joiners remain after removing the emoji.
_EMOJI_BASE = (
    r"(?:[\U0001F300-\U0001FAFF\u2600-\u27BF]"
    r"|[\u231A\u231B\u23E9-\u23F3\u23F8-\u23FA\u25AA\u25AB"
    r"\u25B6\u25C0\u25FB-\u25FE\u2B05-\u2B07\u2B1B\u2B1C\u2B50\u2B55]"
    r"|[©®™]\uFE0F|\U0001F004|\U0001F0CF)"
)
_EMOJI_PART = _EMOJI_BASE + r"[\uFE0E\uFE0F]?[\U0001F3FB-\U0001F3FF]?[\U000E0020-\U000E007F]*"
UNICODE_EMOJI_RE = re.compile(
    r"[0-9#*]\uFE0F?\u20E3|[\U0001F1E6-\U0001F1FF]{1,2}|"
    + _EMOJI_PART + r"(?:\u200D" + _EMOJI_PART + r")*"
)


def display_body(text):
    text = WEIBO_EMOTICON_RE.sub("", text)
    text = UNICODE_EMOJI_RE.sub("", text)
    return re.sub(r"\s+", " ", text).strip()


def plain(raw):
    return re.sub(r"\s+", " ", unescape(re.sub(r"<[^>]+>", " ", raw or ""))).strip()


def cli_source_url(sample):
    """Resolve citation provenance, including saved CLI samples without a URL."""
    url = str(sample.get("url") or "").strip()
    if re.match(r"^https?://", url, re.IGNORECASE):
        return url
    for key in ("external_id", "mid"):
        status_id = str(sample.get(key) or "").strip()
        if re.fullmatch(r"[1-9][0-9]{0,24}", status_id):
            return f"https://m.weibo.cn/detail/{status_id}"
    return None


def topic_excerpts(item, *, for_display=False):
    """Only accepted CLI bodies; preserve provenance and never promote rejected samples."""
    raw = value(item, "raw_payload_json", "raw_payload", default={}) or {}
    title = value(item, "title", default="") or ""
    enrichment = raw.get("cli_enrichment") or {}
    excerpts, seen = [], set()
    for sample in enrichment.get("samples", []):
        relation = sample.get("relation") or {}
        if relation.get("decision") != "accepted" or relation.get("audit_only"):
            continue
        body = plain(clean_body(sample.get("text") or "", title))
        body = display_body(body) if for_display else body[:360]
        identity = re.sub(r"\W", "", body).lower()
        url = cli_source_url(sample)
        if len(identity) < 12 or identity in seen or not url:
            continue
        seen.add(identity)
        excerpts.append(
            {"text": body, "url": url, "published_at": sample.get("created_at")}
        )
        if len(excerpts) == 3:
            break
    return excerpts


def topic_context(item):
    excerpts = topic_excerpts(item)
    if excerpts:
        return "\n".join(row["text"] for row in excerpts)
    # This is source description, never an invented event narrative.
    title = plain(value(item, "title", default=""))
    description = plain(value(item, "summary", default=""))
    return description[:900] if description != title else ""


def summary_from_items(items):
    for item in items:
        if item.source.platform == "zhihu" and item.source.source_type in {
            "community_question_hotlist",
            "community_hotlist",
        }:
            description = topic_context(item)
            text = (
                ("知乎问题描述：" + description[:500])
                if description
                else ("知乎问题：" + item.title)
            )
            return text, [{"url": item.url, "text": description or item.title}]
    for item in items:
        excerpts = topic_excerpts(item, for_display=True)
        if excerpts:
            first = excerpts[0]
            body = first["text"]
            excerpt = body[:250] + "……" if len(body) > 250 else body
            selected = [{**first, "text": excerpt}]
            return "微博讨论摘录：" + excerpt, selected
    for item in items:
        if item.source.source_type == "official_news" and topic_context(item):
            text = topic_context(item)[:500]
            return "报道摘录：" + text, [{"url": item.url, "text": text}]
    return "当前仅有话题标题，尚无足够正文可供摘要。", []
