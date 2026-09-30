"""Build the frozen public case set from local Sept 28 captures (not needed to run it).

Only public titles, canonical URLs, times, source metadata and metrics are retained.
All semantic annotations below are provisional; never derive labels from system output.
Run from the repository root using the project Python environment.
"""

import hashlib
import json
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

ROOT = Path(__file__).resolve().parents[2]
RAW = ROOT / "notes/source-probe-raw/live-analysis-20260928-112421"
DEST = ROOT / "backend/app/evaluation/cases/system_cases_v0_2.json"
END = "2026-09-28T12:00:00+00:00"
HOLDOUT = {1, 4, 7, 8, 17, 18}
CORE = list(range(10)) + list(range(12, 22))
corpus = {}
cases = []


def sanitize(value):
    if isinstance(value, dict):
        return {k: sanitize(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize(v) for v in value]
    if isinstance(value, str) and value.startswith("https://"):
        url = urlsplit(value)
        query = [(k, v) for k, v in parse_qsl(url.query) if not k.startswith("utm_")]
        return urlunsplit((url.scheme, url.netloc, url.path, urlencode(query), ""))
    return value


def ref(index, **overrides):
    return {"ref": f"live-{index:02}", **({"overrides": overrides} if overrides else {})}


def minimal(title, identity="sample", **overrides):
    return {
        "item": {
            "source_id": "zhihu_hot_list",
            "source_name": "synthetic Zhihu",
            "source_type": "community_question_hotlist",
            "source_status": "use",
            "source_origin": "official_api",
            "platform": "zhihu",
            "signal_role": "attention_signal",
            "title": title,
            "url": f"https://fixture.invalid/{identity}",
            "fetched_at": END,
            "raw_metrics": {"rank": 1},
            **overrides,
        }
    }


def add(identity, kind, data, expected, rationale, origin="synthetic", groups=None):
    found = []

    def visit(value):
        if isinstance(value, dict):
            if "ref" in value:
                found.append(corpus[value["ref"]]["group"])
            for child in value.values():
                visit(child)
        elif isinstance(value, list):
            for child in value:
                visit(child)

    visit(data)
    groups = sorted(set(found + (groups or [])))
    splits = {corpus[k]["split"] for k in corpus if corpus[k]["group"] in groups}
    if len(splits) > 1:
        raise ValueError(f"mixed splits in {identity}")
    cases.append(
        {
            "case_id": identity,
            "kind": kind,
            "origin": origin,
            "split": next(iter(splits), "regression"),
            "groups": groups,
            "rationale": rationale,
            "label_status": "assistant_annotated_pending_human_review",
            "input": data,
            "expected": expected,
        }
    )


def build_corpus():
    collection = json.loads((RAW / "collection.json").read_text(encoding="utf-8"))
    search = json.loads((RAW / "zhihu-search.json").read_text(encoding="utf-8"))
    allowed = {
        "source_id",
        "source_name",
        "source_type",
        "source_status",
        "source_origin",
        "platform",
        "signal_role",
        "title",
        "url",
        "fetched_at",
        "published_at",
        "content_hash",
        "raw_metrics",
        "quality_flags",
        "signal_contribution_role",
    }
    for index, raw in enumerate(collection["items"]):
        value = sanitize({k: v for k, v in raw.items() if k in allowed})
        # The collector hash identifies the original capture, despite excerpting its content.
        value["normalized"] = {
            k: v for k, v in raw.get("normalized", {}).items() if k in {"rank", "metric_semantics"}
        }
        corpus[f"live-{index:02}"] = {
            "group": f"event-{index:02}",
            "split": "holdout" if index in HOLDOUT else "dev",
            "core_seed": index in CORE,
            "provenance": f"collection.json/items/{index}",
            "item": value,
        }
    for index, raw in enumerate(search["normalized_items"][:2]):
        value = sanitize({k: v for k, v in raw.items() if k in allowed})
        value["normalized"] = sanitize({"relation": raw["normalized"]["relation"]})
        corpus[f"answer-{index}"] = {
            "group": "event-00",
            "split": "dev",
            "core_seed": False,
            "provenance": f"zhihu-search.json/normalized_items/{index}",
            "item": value,
        }


def build_normalization_and_matching():
    for index in CORE:
        raw = corpus[f"live-{index:02}"]["item"]
        add(
            f"normalize-{index:02}",
            "normalization",
            {"item": ref(index)},
            {
                "published_at": raw.get("published_at"),
                "hash_preserved": True,
                "idempotent_hash": True,
                "traceable": True,
                "source_id": raw["source_id"],
            },
            "保留已知时间；缺失发布时间不能以采集时间填充；内容身份和引用可追溯。",
            "real_excerpt",
        )
        add(
            f"match-repeat-{index:02}",
            "matching",
            {
                "left": ref(index),
                "right": ref(index, title=raw["title"] + " "),
            },
            {"same_event": True},
            "相同 URL 和原始采集身份的空白变体属于同一事件；属于衍生去重用例。",
            "derived",
        )
    negatives = [
        (0, 2),
        (0, 3),
        (0, 5),
        (0, 6),
        (0, 9),
        (0, 12),
        (0, 13),
        (0, 14),
        (0, 15),
        (2, 6),
        (14, 15),
        (15, 16),
        (19, 20),
        (20, 21),
        (1, 4),
        (1, 7),
        (1, 8),
        (4, 7),
        (7, 17),
        (17, 18),
    ]
    for a, b in negatives:
        add(
            f"match-distinct-{a:02}-{b:02}",
            "matching",
            {"left": ref(a), "right": ref(b)},
            {"same_event": False},
            "报道主体、行动或比赛场次不同；同属体育、社会或疫情议题不等于同一事件。",
            "real_excerpt",
        )
    for index in range(2):
        add(
            f"match-answer-{index}",
            "matching",
            {"left": ref(0), "right": {"ref": f"answer-{index}"}},
            {"same_event": True},
            "答案 URL 的 question ID 与热榜问题一致，标题也指向同一养老金事件。",
            "real_excerpt",
        )
    legacy = json.loads(
        (ROOT / "backend/app/evaluation/cases/day27_evaluation_cases_v0_1.json").read_text(
            encoding="utf-8"
        )
    )
    for index, row in enumerate(legacy["matching_cases"]):
        add(
            f"match-legacy-{index + 1}",
            "matching",
            {
                "left": minimal(row["parent_title"], f"legacy-{index}-a"),
                "right": minimal(row["enrichment_title"], f"legacy-{index}-b"),
            },
            {"same_event": row["expected"]["same_event"]},
            "复用 Day27 的同事件标签，仅提供历史标题对和占位 URL；不声称重放原始搜索响应。",
            "legacy_title_pair",
            groups=[f"legacy-{index}"],
        )


def build_official_and_classification():
    for a, b in [(0, 15), (2, 15), (3, 15), (6, 15), (0, 14), (2, 16), (1, 17), (4, 18)]:
        add(
            f"official-unrelated-{a:02}-{b:02}",
            "official_support",
            {
                "event": ref(a),
                "official_items": [ref(b)],
            },
            {"supported": False},
            "官媒条目没有报道目标事件，时间接近和泛化行动词不构成官媒支持。",
            "real_excerpt",
        )
    for index in [14, 15, 16, 17]:
        add(
            f"official-self-{index}",
            "official_support",
            {
                "event": ref(index),
                "official_items": [ref(index)],
            },
            {"supported": True},
            "同一当日官媒报道作为目标事件及证据；是身份对照用例。",
            "derived",
        )
    add(
        "official-unavailable",
        "official_support",
        {
            "event": minimal("测试汽车公司发布召回通知"),
            "official_items": [],
        },
        {"supported": False, "status": "not_checked"},
        "无可用证据池时应标为未检查。",
    )
    configs = [
        (
            "A",
            {"weibo_topn": True, "zhihu_topn": True},
            "supported",
            "natural_topn_overlap",
            "A_cross_platform_with_official",
        ),
        (
            "B",
            {"weibo_topn": True, "zhihu_search": True},
            "supported",
            "search_supported",
            "B_single_platform_with_search_and_official",
        ),
        (
            "C",
            {"weibo_topn": True, "zhihu_topn": True},
            "not_found",
            "natural_topn_overlap",
            "C_cross_platform_without_official",
        ),
        ("D", {"zhihu_topn": True}, "supported", "unknown", "D_single_platform_with_official"),
        ("E", {"zhihu_topn": True}, "not_found", "unknown", "E_single_platform_only"),
        ("F", {"official_source": True}, "supported", "unknown", "F_official_only"),
        ("unknown", {}, "not_checked", "unknown", "unknown"),
    ]
    for name, presence, support, match, category in configs:
        add(
            f"classification-{name}",
            "classification",
            {
                "platform_presence": presence,
                "official_support_status": support,
                "cross_platform_match_type": match,
                "source_health": "use",
                "freshness_bucket": "24h",
            },
            {"priority_category": category},
            "独立列出的证据组合与类别定义；不把 expected 转换成分类输入。",
        )
    add(
        "classification-rss-fallback",
        "classification",
        {
            "platform_presence": {"weibo_topn": True},
            "source_health": "fallback",
            "quality_flags": ["weibo_cli_command_failed"],
        },
        {"priority_category": "E_single_platform_only", "confidence_level": "low"},
        "CLI 失败时保留 RSS 存在信号，但未知的搜索质量不能作为增强置信度的依据。",
    )


def build_heat():
    def signal(platform="zhihu", origin="official_api", **kw):
        return {
            "event_id": "event",
            "platform": platform,
            "source_origin": origin,
            "signal_role": "attention_signal",
            **kw,
        }

    add(
        "heat-zhihu-order",
        "heat",
        {"check": "ordering", "groups": [[signal(rank=r)] for r in [8, 1, 20]]},
        {"order": [1, 0, 2]},
        "同平台其余证据相同时，官方热榜排名 1、8、20 应按此先后。",
    )
    add(
        "heat-weibo-rss-rank",
        "heat",
        {
            "check": "boundaries",
            "groups": [
                [
                    signal(
                        "weibo",
                        "rsshub",
                        topic_present=True,
                        list_position=1,
                        signal_role="topic_discovery_signal",
                    )
                ]
            ],
        },
        {"has_primary_rank": True, "has_numeric_score": True, "rss_order_labeled": True},
        "现有 Day44 契约将 RSS 顺序存入 primary_platform_rank；"
        "指标必须注明 list_position，不能冒充官方热值。",
    )
    add(
        "heat-irrelevant-cli",
        "heat",
        {
            "check": "boundaries",
            "groups": [
                [
                    signal(
                        "weibo",
                        "weibo_cli",
                        cli_relevance_ok=False,
                        top_status_like_count=999999,
                        signal_role="search_enrichment_signal",
                    )
                ]
            ],
        },
        {"has_numeric_score": False},
        "被相关性过滤的 CLI 搜索结果即使互动量很大也不能参与热度。",
    )
    add(
        "heat-official-no-public-score",
        "heat",
        {
            "check": "boundaries",
            "groups": [[signal("chinanews", "official_rss", signal_role="evidence_signal")]],
        },
        {"score_count": 0},
        "官媒的存在和权威性不是公众讨论热度。",
    )
    add(
        "heat-search-no-hotlist-rank",
        "heat",
        {
            "check": "boundaries",
            "groups": [
                [
                    signal(
                        rank=1,
                        search_enrichment_only=True,
                        vote_count=200,
                        signal_role="search_enrichment_signal",
                    )
                ]
            ],
        },
        {"has_primary_rank": False},
        "搜索结果的序号不能冒充知乎热榜排名。",
    )
    add(
        "heat-platform-separation",
        "heat",
        {
            "check": "boundaries",
            "groups": [
                [
                    signal(rank=2),
                    signal(
                        "weibo", "rsshub", topic_present=True, signal_role="topic_discovery_signal"
                    ),
                ]
            ],
        },
        {"platforms": ["weibo", "zhihu"], "score_count": 2},
        "两个平台保留独立分数，不生成跨平台总分。",
    )
    for platform in ("zhihu", "weibo"):
        add(
            f"heat-category-order-{platform}",
            "heat",
            {
                "check": "within_category",
                "platform": platform,
                "category": "E_single_platform_only",
                "events": [
                    {
                        "id": "a",
                        "category": "E_single_platform_only",
                        "zhihu": {"present": True, "rank": 5},
                        "weibo": {"present": True, "score": 0.8},
                    },
                    {
                        "id": "b",
                        "category": "E_single_platform_only",
                        "zhihu": {"present": True, "rank": 1},
                        "weibo": {"present": True, "score": 0.4},
                    },
                    {
                        "id": "c",
                        "category": "D_single_platform_with_official",
                        "zhihu": {"present": True, "rank": 1},
                        "weibo": {"present": True, "score": 0.99},
                    },
                ],
            },
            {"order": ["b", "a"] if platform == "zhihu" else ["a", "b"]},
            "调用正式展示排序入口：先限定同一类别，再按所选平台排序，另一平台分数及其他类别不参与。",
        )


def build_trends():
    end = datetime.fromisoformat(END)

    def obs(hours, rank=5, present=True, platform="zhihu", **kw):
        ident = f"{platform}-{hours}"
        return {
            "event_id": "event",
            "platform": platform,
            "observation_id": ident,
            "run_id": ident,
            "observed_at": (end + timedelta(hours=hours)).isoformat(),
            "source_id": "zhihu_hot_list" if platform == "zhihu" else "weibo_rsshub_hot_search",
            "collection_status": "succeeded",
            "list_complete": True,
            "topn_scope": "top20",
            "topn_present": present,
            "rank": rank if platform == "zhihu" and present else None,
            **kw,
        }

    def case(name, observations, expected, platform="zhihu", interval=120):
        add(
            f"trend-{name}",
            "trend",
            {
                "event_id": "event",
                "platform": platform,
                "run_id": name,
                "window_start": (end - timedelta(hours=24)).isoformat(),
                "window_end": END,
                "config": {"expected_interval_minutes": interval},
                "observations": observations,
            },
            expected,
            "固定 24h 窗口的人工构造时间线；检验趋势、断档和 unknown 规则，"
            "不代表真实世界预测准确率。",
        )

    for name, ranks, direction in [
        ("rising", [10, 5], "rising"),
        ("cooling", [3, 8], "cooling"),
        ("stable", [5, 5], "stable"),
    ]:
        case(
            name,
            [obs(-2, ranks[0]), obs(0, ranks[1])],
            {"trend_status": direction, "continuous_topn_minutes": 120.0, "duration_status": "ok"},
        )
    case(
        "single",
        [obs(0)],
        {"trend_status": "unknown", "continuous_topn_minutes": 0.0, "duration_status": "partial"},
    )
    case("empty", [], {"trend_status": "unknown", "continuous_topn_minutes": None})
    case(
        "outage",
        [obs(-2), obs(0, present=None, collection_status="failed", list_complete=False)],
        {"trend_status": "unknown", "continuous_topn_minutes": None, "current_topn_present": None},
    )
    case("gap", [obs(-8), obs(0)], {"trend_status": "unknown", "continuous_topn_minutes": 0.0})
    case(
        "scope-change",
        [obs(-2), obs(0, topn_scope="top10")],
        {"trend_status": "unknown", "continuous_topn_minutes": 0.0},
    )
    case(
        "no-sampling-config",
        [obs(-2), obs(0)],
        {"trend_status": "unknown", "continuous_topn_minutes": None},
        interval=None,
    )
    case(
        "exit",
        [obs(-2), obs(0, present=False)],
        {"trend_status": "unknown", "continuous_topn_minutes": 0.0, "current_topn_present": False},
    )
    case(
        "reenter",
        [obs(-4), obs(-2, present=False), obs(0)],
        {"trend_status": "unknown", "continuous_topn_minutes": 0.0},
    )
    case(
        "duplicate",
        [obs(-2, 8), obs(-2, 8), obs(0, 5)],
        {"trend_status": "rising", "continuous_topn_minutes": 120.0, "snapshot_presence_count": 2},
    )
    case("future", [obs(0), obs(2, 1)], {"trend_status": "unknown", "snapshot_presence_count": 1})
    case("stale", [obs(-10), obs(-8)], {"trend_status": "unknown", "current_topn_present": None})
    case(
        "rss-only",
        [obs(-2, platform="weibo"), obs(0, platform="weibo")],
        {"trend_status": "unknown", "continuous_topn_minutes": 120.0},
        platform="weibo",
    )
    for name, likes, versions, expected in [
        ("weibo-activity", [5, 200], ["v1", "v1"], "rising"),
        ("weibo-freshness-only", [5, 5], ["v1", "v1"], "unknown"),
        ("weibo-config-change", [5, 200], ["v1", "v2"], "unknown"),
    ]:
        sequence = []
        for hour, score, count, version in zip([-2, 0], [0.2, 0.7], likes, versions, strict=True):
            sequence.append(
                obs(
                    hour,
                    platform="weibo",
                    score_config_version=version,
                    sampling_signature="cli-search-fixed",
                    score_component_signature="likes",
                    platform_heat={
                        "event_id": "event",
                        "platform": "weibo",
                        "score_status": "ok",
                        "platform_score": score,
                        "raw_metrics_used": {"top_status_like_count": count},
                    },
                )
            )
        case(
            name,
            sequence,
            {"trend_status": expected, "continuous_topn_minutes": 120.0},
            platform="weibo",
        )


def build_collection_and_workflows():
    add(
        "collect-zhihu-normal",
        "collection",
        {
            "source": "zhihu",
            "items": [{"Title": "测试汽车公司发布召回通知", "Url": "https://fixture.invalid/q"}],
        },
        {"status": "succeeded", "normalized_count": 1, "list_complete": True},
        "正常响应通过真实 collector 解析。",
    )
    add(
        "collect-zhihu-incomplete",
        "collection",
        {
            "source": "zhihu",
            "total": 20,
            "items": [{"Title": "测试汽车公司发布召回通知", "Url": "https://fixture.invalid/q"}],
        },
        {"list_complete": False},
        "响应短于已知总量和请求上限时不能声明完整榜单。",
    )
    add(
        "collect-zhihu-timeout",
        "collection",
        {"source": "zhihu", "outage": True},
        {"status": "failed", "normalized_count": 0, "has_error": True},
        "超时应显式失败而非产生空的成功榜单。",
    )
    add(
        "collect-zhihu-empty",
        "collection",
        {"source": "zhihu", "total": 0, "items": []},
        {"status": "succeeded", "normalized_count": 0, "list_complete": True},
        "已知空榜与服务失败分开。",
    )
    xml = (
        "<rss><channel><item><title>测试汽车公司发布召回通知</title>"
        "<link>https://fixture.invalid/news</link>{}</item></channel></rss>"
    )
    for name, data, expected in [
        (
            "rss-no-date",
            {"xml": xml.format("")},
            {"status": "succeeded", "missing_published_count": 1},
        ),
        (
            "rss-valid",
            {"xml": xml.format("<pubDate>Mon, 28 Sep 2026 12:00:00 GMT</pubDate>")},
            {"status": "succeeded", "missing_published_count": 0},
        ),
        ("rss-broken", {"xml": "<broken"}, {"status": "failed", "has_error": True}),
        ("rss-http-error", {"http_status": 503}, {"status": "failed", "has_error": True}),
        ("rss-timeout", {"outage": True}, {"status": "failed", "has_error": True}),
    ]:
        add(
            f"collect-{name}",
            "collection",
            {"source": "official", **data},
            expected,
            "使用 MockTransport 调用实际 RSS collector，核对缺失字段及失败语义。",
        )
    title = corpus["live-00"]["item"]["title"]
    add(
        "workflow-unrelated-official",
        "workflow",
        {
            "rounds": [{"items": [ref(0), ref(15)]}],
            "target_title": title,
        },
        {"target_category": "E_single_platform_only", "task_count": 9, "traceable": True},
        "养老金事件与中国高校赴安哥拉讲座无关，完整编排后不应升级成有官媒支持的 D 类。",
        "real_excerpt",
    )
    add(
        "workflow-old-news",
        "workflow",
        {"rounds": [{"items": [ref(12)]}]},
        {"event_count": 0},
        "拟定产品验收要求：明确发表于 2025 年的旧官媒报道不能作为 2026 年当日新热点入池；"
        "仍待确认业务时效策略。",
        "real_excerpt",
    )
    add(
        "workflow-fresh-official",
        "workflow",
        {"rounds": [{"items": [ref(15)]}]},
        {"event_count": 1, "category_counts": {"F_official_only": 1}, "traceable": True},
        "当日独立官媒报道应进入 F 类并保留来源引用。",
        "real_excerpt",
    )
    add(
        "workflow-idempotent",
        "workflow",
        {"rounds": [{"items": [ref(0)]}], "retry": True},
        {"idempotent": True, "fetch_calls": 1, "event_count": 1, "task_count": 18},
        "相同 run_id 重试不重新采集，也不重复写事件、分数和观测；工具调用日志允许新增。",
        "derived",
    )
    add(
        "workflow-source-outage",
        "workflow",
        {
            "rounds": [
                {"items": [ref(0)]},
                {
                    "items": [],
                    "failures": ["zhihu_hot_list"],
                    "window_end": "2026-09-28T14:00:00+00:00",
                },
            ]
        },
        {"status": "partial", "all_zhihu_unknown": True, "event_count": 1, "task_count": 18},
        "后续采集失败必须保留已有事件，并将当轮存在性和趋势标为 unknown。",
        "derived",
    )
    add(
        "workflow-weak-title",
        "workflow",
        {"rounds": [{"items": [ref(8)]}]},
        {"event_count": 0, "review_count": 1},
        "ALO上海缺少明确事件行动，应进入人工复核而非自动创建热点。",
        "real_excerpt",
    )
    add(
        "workflow-empty",
        "workflow",
        {"rounds": [{"items": []}]},
        {"event_count": 0, "task_count": 9},
        "空的采集结果应正常贯穿编排，无伪造事件。",
    )
    add(
        "workflow-search-same-question",
        "workflow",
        {
            "rounds": [{"items": [ref(0), {"ref": "answer-0"}]}],
        },
        {"event_count": 1, "review_count": 0},
        "同一问题的知乎热榜与搜索答案进入完整链路，应归并到一个事件。",
        "real_excerpt",
    )
    add(
        "workflow-two-rounds",
        "workflow",
        {
            "target_title": title,
            "rounds": [
                {
                    "window_end": "2026-09-28T10:00:00+00:00",
                    "complete": True,
                    "items": [
                        ref(0, fetched_at="2026-09-28T10:00:00+00:00", raw_metrics={"rank": 10})
                    ],
                },
                {"items": [ref(0, fetched_at=END, raw_metrics={"rank": 5})], "complete": True},
            ],
        },
        {"event_count": 1, "target_trend": "rising", "target_duration": 120.0},
        "从真实标题衍生两轮固定时间与名次，检验事件身份、最新热度和观测记录的连续传递。",
        "derived",
    )
    same_title = "测试汽车公司发布召回通知引发消费者关注"
    add(
        "workflow-cross-platform",
        "workflow",
        {
            "rounds": [
                {
                    "items": [
                        minimal(same_title, "zhihu"),
                        minimal(
                            same_title,
                            "weibo",
                            source_id="weibo_rsshub_hot_search",
                            source_name="synthetic Weibo",
                            source_type="community_hotlist",
                            platform="weibo",
                            source_origin="rsshub",
                            signal_role="topic_discovery_signal",
                            raw_metrics={"list_position": 1},
                        ),
                    ]
                }
            ],
            "target_title": same_title,
        },
        {"event_count": 1, "target_category": "C_cross_platform_without_official"},
        "构造同题跨平台事件，检验事件合并后是否保留两个平台的存在证据。",
    )


def main():
    build_corpus()
    build_normalization_and_matching()
    build_official_and_classification()
    build_heat()
    build_trends()
    build_collection_and_workflows()
    suite = {
        "suite_name": "three-module-system-v0.2",
        "version": "0.2",
        "window_end": END,
        "description": (
            "20 core candidate topics, independent provisional annotations, offline system baseline"
        ),
        "provenance": {
            "capture": str(RAW.relative_to(ROOT)).replace("\\", "/"),
            "collection_sha256": hashlib.sha256((RAW / "collection.json").read_bytes()).hexdigest(),
            "search_sha256": hashlib.sha256((RAW / "zhihu-search.json").read_bytes()).hexdigest(),
            "core_seed_count": len(CORE),
            "dev_seed_count": len(set(CORE) - HOLDOUT),
            "holdout_seed_count": len(HOLDOUT),
            "excerpt_policy": (
                "titles, canonical URLs, source metadata, timestamps, metrics; "
                "no full text, users or credentials"
            ),
            "annotation_policy": (
                "assistant provisional; legacy same_event labels reused; human adjudication pending"
            ),
            "split_policy": (
                "event-group split; no threshold tuning; "
                "holdout is a reserved partition, not blind human evaluation"
            ),
        },
        "corpus": corpus,
        "cases": cases,
    }
    DEST.write_text(json.dumps(suite, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"Wrote {len(cases)} cases, {len(corpus)} excerpts to {DEST}")


if __name__ == "__main__":
    main()
