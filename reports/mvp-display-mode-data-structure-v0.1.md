# MVP 最终展示模式与数据结构 v0.1

生成时间：2026-09-24

## 目标

本报告定义第一阶段 MVP 的最终展示方式和最小数据结构。

核心目标不是做全量话题聚合，而是先证明下面这条链路可运行：

```text
微博 / 知乎平台内热点
-> 事件级聚合
-> 平台内热度展示
-> Top5 官媒后台补证
-> A/B/C/D/E/F 分类展示
```

MVP 明确不做：

- 不把微博、知乎和官媒原始指标合成为一个跨平台总分。
- 不做大规模话题级聚合、话题别名合并或全量话题簇展示。
- 不对所有事件执行官媒 query，只对每日平台 Top5 去重事件执行一次后台补证。
- 不单独展示“Top5 官媒信息列表”；官媒补证结果只作为事件卡属性和分类依据出现。
- 不把官媒报道数量、站内排行或命中结果解释为公众热度。

## 展示模式

### 0. TopN 时间窗口

微博 TopN 和知乎 TopN 都按滚动 24 小时采集窗口计算，不按自然日 00:00-24:00 切片。

窗口定义：

```text
window_end = generated_at
window_start = window_end - 24h
window_type = rolling_24h
timezone = Asia/Shanghai
```

示例：如果周一上午 09:00 生成展示结果，则 TopN 输入窗口为 `[周日 09:00, 周一 09:00)`。

计算规则：

- TopN 输入只使用窗口内采集到的微博 / 知乎信号，窗口判断优先使用 `fetched_at` 或 `snapshot_time`。
- `published_at`、事件发生时间或内容更新时间只能作为事件解释、匹配和 freshness 辅助，不用于判断信号是否进入本轮 TopN 窗口。
- 窗口内信号先进入事件抽取和事件级聚合，再按平台内分数生成微博 TopN / 知乎 TopN。
- 微博 TopN / 知乎 TopN 事件进入最终展示候选池，并作为 Top5 官媒后台补证的候选来源。
- `report_date` 是 `window_end` 所在本地日期的展示标签，不代表自然日全量统计。

### 1. 每日微博 TopN 事件榜

展示微博平台内滚动 24 小时窗口 TopN 事件。

展示单位是 `event_id`，不是微博原始话题。

数据来源：

```text
第 6 周事件聚合结果
Day 44 weibo PlatformScore
Day 46 HotspotClassificationAssembly
```

展示字段：

```text
event_id
title
weibo_rank
weibo_platform_score
weibo_platform_bucket
weibo_score_status
platform_presence
priority_category
official_support_status
official_evidence_badge
```

说明：

- 微博榜只按微博平台内结果排序。
- 如果同一事件也出现在知乎，展示知乎 badge，但不把知乎分数加到微博榜排序中。
- 如果事件属于 Top5 query 范围，展示官媒依据状态；不额外展示一条官媒列表项。

### 2. 每日知乎 TopN 事件榜

展示知乎平台内滚动 24 小时窗口 TopN 事件。

展示单位是 `event_id`，不是知乎原始问题或搜索结果。

数据来源：

```text
第 6 周事件聚合结果
Day 44 zhihu PlatformScore
Day 46 HotspotClassificationAssembly
```

展示字段：

```text
event_id
title
zhihu_rank
zhihu_platform_score
zhihu_platform_bucket
zhihu_score_status
platform_presence
priority_category
official_support_status
official_evidence_badge
```

说明：

- 知乎榜只按知乎平台内结果排序。
- 如果同一事件也出现在微博，展示微博 badge，但不把微博分数加到知乎榜排序中。
- `zhihu_search` 只能作为补强证据，不等于知乎 TopN。

### 3. A/B/C/D/E/F 分类汇总

展示微博、知乎和官媒三类证据通道下的事件状态。

分类依据来自 Day 46：

```text
Event
+ PlatformScore[]
+ OfficialSupportResult
-> priority_category
```

分类含义：

| 分类 | 判断依据 | 展示含义 |
|---|---|---|
| A | 微博 TopN + 知乎 TopN + 官媒支撑 | 双社区平台共同关注，且有官媒依据 |
| B | 单平台 TopN + 另一平台搜索 / CLI 补强 + 官媒支撑 | 单平台上榜，另一平台有讨论补证，且有官媒依据 |
| C | 跨社区平台成立，但无官媒支撑 | 社区跨平台讨论成立，但当前未发现官媒依据 |
| D | 单平台 TopN + 官媒支撑 | 单平台关注，且有官媒依据 |
| E | 单平台 TopN，无官媒和跨平台补强 | 单平台候选，需保守展示 |
| F | 官媒议程事件，无社区 TopN / 搜索补强 | 官媒重点报道 / 高证据低讨论 |

说明：

- A-E 是社区热点主展示区域。
- F 单独作为“官媒重点报道 / 高证据低讨论”区域，不进入微博 / 知乎主榜。
- 官媒支撑只影响证据状态和分类，不形成公众热度。
- `not_checked` 和 `not_found` 必须区分：未查询不能写成未找到。

## 官媒依据策略

Top5 官媒 query 是后台补证策略，不是独立展示模块。

MVP 只对每日平台 Top5 去重事件执行官媒 query。

流程：

```text
weibo_top5_events
+ zhihu_top5_events
-> dedupe by event_id
-> one official_query per event per day
-> OfficialSupportResult
-> write back to event card / category section
```

状态规则：

| 状态 | 含义 |
|---|---|
| `supported` | 已查询，官媒强匹配 |
| `weak_supported` | 已查询，官媒弱匹配或字段不足 |
| `not_found` | 已查询，但无有效官媒命中 |
| `not_checked` | 未进入本轮 Top5 query 范围，或本轮未执行 query |

限制：

- 每个 `event_id + report_date` 最多执行一次官媒 query。
- Top5 之外默认 `not_checked`，不能标记为 `not_found`。
- 官媒 query 结果可以复用到当天两个平台榜和分类汇总。
- 展示层只在事件卡中显示官媒 badge、依据摘要和 citation，不增加独立 Top5 官媒列表。

## 最小数据结构

### DailyHotspotDisplay

```json
{
  "report_date": "2026-09-24",
  "run_id": "daily-2026-09-24T09:00:00+08:00",
  "time_window": {
    "window_type": "rolling_24h",
    "window_start": "2026-09-23T09:00:00+08:00",
    "window_end": "2026-09-24T09:00:00+08:00",
    "timezone": "Asia/Shanghai"
  },
  "query_scope": {
    "official_query_scope": "daily_top5_mvp",
    "weibo_top_k": 5,
    "zhihu_top_k": 5,
    "deduped_query_event_count": 8
  },
  "weibo_top_events": [],
  "zhihu_top_events": [],
  "category_sections": [],
  "generated_at": "2026-09-24T09:00:00+08:00"
}
```

说明：

- 不再单独设置 `top5_official_evidence_events`。
- F 类事件放在 `category_sections` 中的 `F_official_only` section。

### PlatformEventCard

```json
{
  "event_id": "evt_xxx",
  "title": "事件标题",
  "summary": "事件摘要",
  "primary_platform": "weibo",
  "platform_rank": 3,
  "platform_score": 0.86,
  "platform_bucket": "top3",
  "platform_strength": "strong",
  "score_status": "ok",
  "platform_presence": {
    "zhihu_topn": false,
    "zhihu_search": true,
    "weibo_topn": true,
    "weibo_cli": true,
    "official_source": true,
    "mock": false
  },
  "priority_category": "D_single_platform_with_official",
  "official_support_status": "supported",
  "official_evidence_badge": "有官媒依据",
  "official_evidence_summary": "中国新闻网报道与事件实体和时间窗口匹配",
  "badges": ["微博Top3", "知乎搜索补强", "官媒支撑"],
  "quality_flags": []
}
```

### PlatformEvidenceView

```json
{
  "platform": "weibo",
  "platform_score_id": 123,
  "platform_score": 0.86,
  "platform_bucket": "top3",
  "platform_strength": "strong",
  "score_status": "ok",
  "primary_platform_rank": 3,
  "rank_delta": null,
  "snapshot_presence_count": 1,
  "raw_metrics_used": {
    "list_position": 3,
    "matched_status_count": 20
  },
  "quality_flags": ["insufficient_history"]
}
```

### OfficialEvidenceView

```json
{
  "official_support_status": "supported",
  "official_query_scope": "daily_top5_mvp",
  "official_query_checked_at": "2026-09-24T00:00:00+08:00",
  "official_coverage_level": "medium",
  "official_source_count": 2,
  "unique_story_count": 1,
  "authority_sources": ["人民网", "中国新闻网"],
  "official_references": [
    {
      "title": "官媒报道标题",
      "url": "https://example.com/news.html",
      "source_name": "中国新闻网",
      "published_at": "2026-09-24T00:00:00+08:00",
      "match_reason": "entity_time_rule"
    }
  ],
  "quality_flags": []
}
```

说明：

- `OfficialEvidenceView` 是事件卡内部证据视图，不是单独列表。
- 同一 `OfficialEvidenceView` 可在微博榜、知乎榜和分类 section 中复用。

### CategorySection

```json
{
  "priority_category": "A_cross_platform_with_official",
  "title": "双平台共同关注，且有官媒依据",
  "event_count": 3,
  "events": [],
  "limitations": []
}
```

### IdempotencyState

```json
{
  "event_id": "evt_xxx",
  "report_date": "2026-09-24",
  "classification_run_id": "day46-2026-09-24",
  "official_query_key": "evt_xxx:2026-09-24:daily_top5_mvp",
  "input_fingerprint": "sha256(platform_scores+official_support+event_resolution)",
  "last_written_at": "2026-09-24T00:00:00+08:00"
}
```

## 主平台选择规则

`primary_platform` 只在社区平台中选择。

规则：

1. 如果只有一个社区 TopN 平台，用该平台作为主平台。
2. 如果微博和知乎都是 TopN，选择平台内 rank 更靠前的平台。
3. 如果 rank 都缺失，选择 `platform_score` 更高的平台。
4. 如果只有搜索补强，没有 TopN，不设置社区主平台。
5. 官媒不作为 `primary_platform`，只进入 `official_evidence`。
6. `F_official_only` 没有社区主平台，展示 lane 为“官媒重点报道”。

## 幂等策略

每日展示链路必须支持重复运行。

规则：

- 展示组装幂等键包含 `window_start + window_end + official_query_scope`，避免同一天多次滚动窗口运行互相覆盖。
- `official_query` 幂等键为 `event_id + report_date + official_query_scope`。
- 同一事件同一天只执行一次 Top5 官媒 query。
- `source_citations` 按 `url + item_id + citation_role` 去重。
- `classification_detail_json` 按 `classification_run_id` 覆盖写入，不重复追加。
- `quality_flags`、`risk_notes` 和 `official_references` 去重后写入。
- 输入未变化时，如果 `input_fingerprint` 一致，可以跳过重复组装。
- Day47 走势读取明确快照，不从混杂历史 JSON 中推断趋势。

## 与后续增强的关系

后续可增强：

- 话题级聚合和别名归并。
- Top5 扩展为 Top10 / Top20 官媒 query。
- 更细的官媒来源覆盖强度。
- 展示二次传播、长尾和 second wave。
- 按领域或主题生成子榜单。

MVP 暂不做这些增强，优先保证事件级展示链路稳定。
