# 数据源字段映射表

## 文档目标

本文对应 Day 22：统一字段映射与 `NormalizedItem` 设计。

`NormalizedItem` 只做数据结构归一化，不做跨平台热度数值归一化。微博、知乎和官媒的原始指标保留在 `raw_metrics` 中，后续分类和类内排序只能按平台内语义解释。

## 统一字段口径

| 字段 | 要求 | 说明 |
|---|---|---|
| `source_status` | 必填 | 调度闸门和置信度输入 |
| `source_origin` | 必填 | 数据获取路径，例如 `official_rss`、`official_api`、`rsshub`、`weibo_cli` |
| `signal_role` | 必填 | 该条数据在流程中的角色 |
| `signal_contribution_role` | 可选 | MVP 不要求 collector 必填，可由分类阶段派生 |
| `raw_metrics` | 必填，可为空对象 | 保存平台原始字段或代理字段 |
| `quality_flags` | 必填，可为空数组 | 保存字段缺失、弱信号、低相关或不可比标记 |

## 枚举

### source_status

```text
use
fallback
experimental_fallback
postpone
use_pending_credentials
fallback_pending_credentials
```

### source_origin

```text
official_rss
official_api
rsshub
weibo_cli
mock
```

### signal_role

```text
event_signal
topic_discovery_signal
attention_signal
discussion_focus_signal
evidence_signal
search_enrichment_signal
mixed_signal
```

## 来源映射

| 来源 | 映射对象 | `source_origin` | `source_status` | `signal_role` | 核心 `raw_metrics` | 必要 `quality_flags` |
|---|---|---|---|---|---|---|
| 官媒 RSS | `NormalizedItem` | `official_rss` | `use` / `fallback` | `evidence_signal` 或 `event_signal` | `published_at`、`report_count`、`source_count`、`channel` | 缺发布时间写 `missing_published_at`，缺摘要写 `missing_summary` |
| 知乎 `hot_list` | `NormalizedItem` | `official_api` | `use` | `attention_signal` | `rank`、`total`、`hot_value` | 无热度值写 `missing_hot_value`，无发布时间写 `missing_published_at` |
| 知乎 `zhihu_search` | `NormalizedItem` | `official_api` | `use` | `search_enrichment_signal` | `comment_count`、`vote_count`、`ranking_score`、`edit_time`、`authority_level` | 低相关结果写审计日志，不参与分类支撑 |
| 微博 RSSHub 热搜 | `NormalizedItem` | `rsshub` | `use` | `topic_discovery_signal` | `list_position`、`hot_value`、`topic_present` | 必须写 `list_position_derived_from_rss_order` 和 `not_heat_metric`；缺热度值写 `missing_hot_value` |
| 微博 CLI 搜索补强 | `NormalizedItem` 或候选下的补强信号 | `weibo_cli` | `use` | `search_enrichment_signal` | `status_id` / `mid`、`comments_count`、`reposts_count`、`attitudes_count`、`total_number_proxy`、`matched_status_count` | CLI 不可用写 `weibo_cli_unavailable` 类标记；低相关结果只留审计日志 |

## 示例

### 知乎 hot_list

```json
{
  "source_id": "zhihu_hot_list",
  "source_name": "知乎热榜",
  "source_type": "community_hotlist",
  "source_status": "use",
  "source_origin": "official_api",
  "platform": "zhihu",
  "signal_role": "attention_signal",
  "signal_contribution_role": ["attention", "discussion", "community_hot_candidate"],
  "title": "事件 A",
  "url": "https://www.zhihu.com/question/1",
  "fetched_at": "2026-09-10T10:00:00+08:00",
  "raw_metrics": {
    "rank": 1,
    "hot_value": null
  },
  "quality_flags": ["missing_hot_value", "missing_published_at"]
}
```

### 微博 RSSHub 话题种子

```json
{
  "source_id": "weibo_rsshub_hot_search",
  "source_name": "微博热搜 RSSHub",
  "source_type": "community_hotlist",
  "source_status": "use",
  "source_origin": "rsshub",
  "platform": "weibo",
  "signal_role": "topic_discovery_signal",
  "signal_contribution_role": ["weak_attention_seed"],
  "title": "事件 B",
  "url": "https://m.weibo.cn/search?q=event-b",
  "fetched_at": "2026-09-10T10:00:00+08:00",
  "raw_metrics": {
    "list_position": 7,
    "hot_value": null
  },
  "quality_flags": [
    "list_position_derived_from_rss_order",
    "not_heat_metric",
    "missing_hot_value"
  ]
}
```

### 微博 CLI 搜索补强

```json
{
  "source_id": "weibo_cli_search_statuses_limited",
  "source_name": "微博 CLI 搜索",
  "source_type": "community_search",
  "source_status": "use",
  "source_origin": "weibo_cli",
  "platform": "weibo",
  "signal_role": "search_enrichment_signal",
  "signal_contribution_role": ["discussion", "interaction", "velocity_proxy"],
  "title": "事件 B 相关微博正文节选",
  "url": "https://m.weibo.cn/status/123",
  "published_at": "2026-09-10T09:30:00+08:00",
  "fetched_at": "2026-09-10T10:00:00+08:00",
  "raw_metrics": {
    "status_id": "123",
    "mid": "456",
    "comments_count": 10,
    "reposts_count": 5,
    "attitudes_count": 100,
    "total_number_proxy": 3000,
    "matched_status_count": 3
  },
  "quality_flags": []
}
```

## 使用规则

- `source_status = postpone` 的来源不得进入正式 collector 调度。
- `source_origin = mock` 的数据不得进入公开结果，除非明确标记为演示或测试。
- `signal_role = topic_discovery_signal` 只能创建弱候选或话题种子，不能单独写成事实。
- `signal_role = search_enrichment_signal` 必须挂到已有候选或用户指定事件下，不作为无限扩展的新热点发现入口。
- `signal_contribution_role` 只作为解释和后续扩展字段，MVP 不依赖它完成分类。
