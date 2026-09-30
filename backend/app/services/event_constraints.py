"""Conservative title-level facts, independent of retrieval relevance scores.

These rules detect explicit contradictions, not unrestricted Chinese NER or entailment.
Missing facts are unknown. Keep summaries out: roundups often mention several events.
"""

import re
from datetime import date


def calendar_dates(text: str) -> set[str]:
    dates = set()
    for y, m, d in re.findall(r"(20\d{2})[-年/](\d{1,2})[-月/](\d{1,2})", text):
        try:
            dates.add(date(int(y), int(m), int(d)).isoformat())
        except ValueError:
            continue
    return dates


def title_facts(text: str) -> dict[str, set[str]]:
    facts = {
        "explicit_date": calendar_dates(text),
        "model": {
            s.lower().replace("-", "")
            for s in re.findall(r"(?<![A-Za-z0-9])[A-Za-z]+-?\d+[A-Za-z]*(?![A-Za-z0-9])", text)
        },
        "model_year": set(re.findall(r"(20\d{2})款", text)),
    }
    # A short named city/province at a clause start is much safer than any occurrence of 市.
    places = re.findall(r"(?:^|[，,：:。；;]|位于|来自)([\u4e00-\u9fff]{2,5}?)(?:全市|市|省)", text)
    facts["location"] = {
        p
        for p in places
        if not any(word in p for word in ("上市", "进入", "提醒", "宣布", "发布", "获批", "全国"))
    }
    # Explicitly named bridges after an optional city prefix.
    bridges = re.findall(r"(?:^|市)([\u4e00-\u9fff]{2,8}?大桥)", text)
    facts["named_object"] = {bridge.rsplit("市", 1)[-1] for bridge in bridges}
    actions = {
        "recall": ("召回",),
        "release": ("发布", "亮相"),
        "cancel": ("取消", "不再安排"),
        "add": ("新开设", "新增", "增设"),
    }
    found = {key for key, words in actions.items() if any(w in text for w in words)}
    # 发布召回通知 is a recall; 发布 is not a second independent action.
    if found - {"release"}:
        found.discard("release")
    facts["action"] = found
    facts["hazard"] = {
        key
        for key, words in {
            "rain": ("暴雨", "强降雨"),
            "heat": ("高温", "酷暑"),
            "earthquake": ("地震",),
            "typhoon": ("台风",),
            "snow": ("暴雪",),
        }.items()
        if any(w in text for w in words)
    }
    return facts


def _fact_pair(left: str, right: str):
    a, b = title_facts(left), title_facts(right)
    for place in a["location"] | b["location"]:
        for facts in (a, b):
            facts["named_object"] = {v.removeprefix(place) for v in facts["named_object"]}
    return a, b


def title_conflicts(left: str, right: str) -> list[str]:
    a, b = _fact_pair(left, right)
    return [f"{key}_conflict" for key in a if a[key] and b[key] and not a[key] & b[key]]


def specific_title_anchors(left: str, right: str) -> list[str]:
    a, b = _fact_pair(left, right)
    # Time, location or a generic action alone is not event-specific evidence.
    return [
        f"{key}:{value}" for key in ("model", "named_object") for value in sorted(a[key] & b[key])
    ]
