# 热点分类组装说明

## 文档目标

本文定义 Day 46 的跨平台覆盖形态判断与热点分类组装逻辑。

Day 46 不重新计算平台热度，也不重新执行事件融合。它读取：

```text
第 6 周 Event / EventResolution
Day 44 platform_scores
Day 45 增强后的 OfficialSupportResult
```

然后生成展示层需要的分类和解释字段：

```text
platform_presence
cross_platform_match_type
priority_category
category_rank
confidence_level
evidence_summary
classification_detail_json
```

## 与其他阶段的边界

| 阶段 | 负责内容 | Day 46 是否重做 |
|---|---|---|
| 第 6 周 | 事件抽取、事件融合、稳定 `event_id` | 不重做 |
| Day 43 | 官媒支撑识别、`official_support_status`、官媒引用 | 不重做，只读取结果 |
| Day 44 | 知乎 / 微博平台内热度分、分桶、状态 | 不重算 |
| Day 45 | 官媒定向检索、官媒内部议程聚合、增强 `OfficialSupportResult` | 不重做，只读取结果 |
| Day 46 | 平台覆盖形态、热点分类、展示依据 | 本日实现 |
| Day 47 | 类内排序和走势状态 | 不在本日完整实现 |

一句话：

```text
Day 44 计算“这个事件在各平台内有多强”。
Day 46 判断“这个事件属于哪种热点类型，展示时应该如何解释”。
```

## 输入

Day 46 的输入应来自已经存在的结构化结果。

```text
Event
EventResolution / match_features_json
PlatformScore[]
accepted source signals
OfficialSupportResult
SourceCitation[]
quality_flags
```

推荐组装入口：

```text
assemble_hotspot_classification(
    event,
    platform_scores,
    source_signals,
    official_support_result,
    event_resolution,
    source_citations,
    window_start,
    window_end,
) -> HotspotClassificationAssembly
```

## 数据窗口

Day 46 默认只读取当前 run 对应的滚动 24 小时窗口：

```text
window_end = 当前 run 固定的分类时间（重试复用）
window_start = window_end - 24h
```

窗口内读取：

```text
PlatformScore
accepted source signals
OfficialSupportResult
```

Day 46 / Day 47 共用 UTC `(window_start, window_end]`，平台分按关联来源 `observed_at`、signal 按 `fetched_at` 入窗；不能用重新评分的 `calculated_at` 引入旧信号。窗口外历史只供审计，不参与本轮 presence、连续上榜时间、趋势或平台内排序。

## 输出

Day 46 输出包括：

```text
HotspotClassification
EvidenceSummary
classification_detail_json
```

其中 `HotspotClassification` 继续使用现有：

```text
backend/app/schemas/classification.py
backend/app/services/event_classification.py
```

Day 46 需要补的是从上游结果组装 `HotspotClassificationInput` 的逻辑，以及生成 `EvidenceSummary` 和 `classification_detail_json`。

如对外接口需要强调输出语义，可以导出 `HotspotClassificationResult` 作为 `HotspotClassification` 的别名；不要在 MVP 中维护两套结果 schema。

## platform_presence

`platform_presence` 表示事件在当前样本中出现在哪些平台和证据通道中。

`platform_presence` 必须基于同一稳定 `Event.event_id` 下的来源证据派生。Day 46 不重新用标题或关键词匹配微博 TopN 与知乎 TopN。

字段：

```text
zhihu_topn
zhihu_search
weibo_topn
weibo_cli
official_source
mock
```

推荐判定：

```text
zhihu_topn:
  存在知乎 hot_list 来源，且 zhihu platform score_status != unknown

zhihu_search:
  存在高相关 zhihu_search 补强

weibo_topn:
  存在微博 RSSHub topic seed，且 weibo platform score_status != unknown

weibo_cli:
  存在高相关微博 CLI 补强

official_source:
  official_support_status in supported / weak_supported

mock:
  事件或来源包含 mock 标记
```

注意：

- `zhihu_search = true` 不等于知乎 TopN。
- `weibo_cli = true` 不等于微博 TopN。
- `official_source = true` 不等于公众热度高。
- 官媒支撑只进入分类输入、证据摘要和 `classification_detail_json`，不得反向改变 `PlatformScore` 或平台热度。

## cross_platform_match_type

`cross_platform_match_type` 表示事件的跨平台覆盖形态。

枚举：

```text
natural_topn_overlap
search_supported
weak_search_supported
single_platform_only
official_only
none
unknown
```

推荐判定：

```text
natural_topn_overlap:
  zhihu_topn = true
  and weibo_topn = true
  and both are evidence under the same stable event_id

search_supported:
  单平台 TopN = true
  and 另一社区平台存在高相关 search / CLI 补强
  and search_hit_quality in same_id_or_title / entity_action_match
  and the enrichment signal has already been accepted into the same event_id or linked through 第 6 周 match_features_json

weak_search_supported:
  单平台 TopN = true
  and 另一社区平台存在 search / CLI 补强
  and search_hit_quality = keyword_overlap
  or 补强字段不完整但未被拒绝

single_platform_only:
  只有一个社区平台 TopN
  and 无高相关另一平台补强

official_only:
  official_source = true
  and zhihu_topn = false
  and weibo_topn = false
  and zhihu_search = false
  and weibo_cli = false

none:
  无足够社区平台证据或官媒支撑
```

`unknown` 只应在数据不足或分类输入缺失时使用。

## 匹配证据记录

Day 46 应保留跨平台覆盖形态的匹配来源。

建议记录：

```text
matched_by
match_features_json
event_id_aggregation_basis
embedding_similarity
hard_constraints_passed
review_status
guardrail_flags
```

`matched_by` 可以包含：

```text
id_match
url_match
title_containment
entity_time_rule
bm25_ngram
embedding_rerank
```

边界：

- 规则强匹配可直接进入分类证据链。
- embedding 辅助匹配必须保留 `embedding_similarity`、硬约束命中情况和复核状态。
- 只有 embedding 高相似但缺少硬约束支撑时，不能直接标记为强跨平台覆盖。
- `natural_topn_overlap` 和 `search_supported` 的依据来自第 6 周事件聚合结果，不在 Day 46 重新执行 `match_and_resolve_events`。

## priority_category

Day 46 使用 `classify_hotspot()` 生成 `priority_category`。

分类规则：

| 分类 | 条件 | 展示含义 |
|---|---|---|
| `A_cross_platform_with_official` | `zhihu_topn && weibo_topn`，且官媒 `supported / weak_supported` | 双社区平台共同关注，且有官媒支撑 |
| `B_single_platform_with_search_and_official` | 单平台 TopN，另一平台搜索 / CLI 高相关补强，且官媒支撑 | 单平台上榜，另一平台有讨论补证，且有官媒支撑 |
| `C_cross_platform_without_official` | 双社区覆盖成立，但官媒 `not_found` | 社区跨平台讨论成立，但缺少官媒支撑 |
| `D_single_platform_with_official` | 单平台 TopN，且官媒支撑 | 单平台关注，且有官媒支撑 |
| `E_single_platform_only` | 单平台 TopN，无官媒和跨平台补强 | 单平台候选，需保守展示 |
| `F_official_only` | 只有官媒支撑，无社区 TopN / search support | 官方报道事件，当前社区样本未捕捉明显讨论 |

## confidence_level

`confidence_level` 表示证据链和分类稳定性，不是事实真伪保证。

MVP 规则沿用现有 `classify_hotspot()`：

```text
A -> high
B -> high / medium，取决于 search_hit_quality
C -> medium
D -> medium
E -> low / medium，取决于快照出现次数和补强质量
F -> medium / low，取决于官媒支撑强弱
```

如果存在关键来源不可用：

```text
quality_flags += source_unavailable
confidence_level 降级为 low
```

## EvidenceSummary

`EvidenceSummary` 是展示给用户看的“为什么纳入简报”。

结构：

```text
lead
platform_evidence
official_evidence
discussion_evidence
limitations
conservative_language_required
```

生成建议：

### lead

根据分类生成一句概述。

示例：

```text
A: 该事件同时出现在知乎和微博热点样本中，并有官媒报道支撑。
B: 该事件在单一社区平台上榜，另一平台存在相关讨论补强，并有官媒支撑。
C: 该事件在多个社区平台出现讨论，但当前未发现官媒支撑。
D: 该事件在单一社区平台受到关注，并有官媒报道支撑。
E: 该事件目前主要来自单一社区平台样本，需保守展示。
F: 该事件来自官媒报道，当前社区热点样本中未捕捉到明显讨论。
```

### platform_evidence

来自 Day 44 `platform_scores`。

示例：

```text
知乎 Top10
微博 RSSHub 话题样本出现
微博平台分为 partial，CLI 当轮不可用
```

### official_evidence

来自 Day 45 增强后的 `official_references`。

示例：

```text
人民网报道与事件实体和时间窗口匹配
新华网 fallback 来源有相关报道
```

### discussion_evidence

来自搜索补强和代表性互动样本。

示例：

```text
知乎搜索有高相关问题
微博 CLI 有相关微博样本
```

### limitations

必须记录限制。

常见限制：

```text
official_support_not_found
single_community_source
search_enrichment_only
weibo_cli_unavailable
zhihu_credentials_missing
low_confidence_match
embedding_only_without_hard_constraint
missing_source_url
insufficient_history
```

### conservative_language_required

以下情况应为 true：

```text
official_support_status = not_found
priority_category = E_single_platform_only
只有单一社区来源
存在弱匹配或中置信复核
```

## classification_detail_json

`classification_detail_json` 面向调试、审计和 Evaluation。

建议结构：

```json
{
  "run_id": null,
  "window_start": null,
  "window_end": null,
  "platform_scores": {},
  "source_signals": [],
  "official_support_detail": {},
  "platform_presence_reason": {},
  "cross_platform_match_reason": {},
  "event_id_aggregation_basis": {},
  "evidence_summary": {},
  "match_features_json": {},
  "matched_by": [],
  "embedding_similarity": null,
  "hard_constraints_passed": null,
  "review_status": "none|candidate_review|resolved|rejected",
  "missing_fields": [],
  "rejected_reasons": [],
  "source_statuses": [],
  "quality_flags": [],
  "updated_at": null
}
```

必须保留：

```text
匹配命中
拒绝原因
缺失字段
source_status
quality_flags
guardrail_flags
```

持久化位置：

```text
events.event_detail_json.classification_detail
```

重复运行同一 run / 同一窗口时覆盖本轮 `classification_detail` 对象，保留 `event_detail_json` 其他 key；不得 append 历史数组，也不得把旧轮次 JSON 合并进当前结果。

## Day46 需要实现的组装步骤

推荐实现顺序：

```text
1. load event-level inputs in rolling 24h window
2. derive platform_presence
3. derive search_hit_quality
4. derive cross_platform_match_type
5. build HotspotClassificationInput
6. call classify_hotspot()
7. build evidence_summary
8. build classification_detail_json
9. persist by overwriting events.event_detail_json.classification_detail
```

## 和 Day47 的关系

Day46 只生成分类和依据，不负责最终类内排序与走势。

Day47 读取同一 24 小时窗口内逐轮 `PlatformScore`、真实来源观测及 Day46 分类，分别生成知乎 / 微博的 `PlatformTrendResult`，组成 `EventHeatAnalysis`。当前连续段只连接相邻 true 观测；缺席、未知或超长间隔切断，单点仅有 0 分钟下界。趋势仅使用 `rising / stable / cooling / unknown`；知乎优先比较真实 rank，微博仅在平台分可比时比较，RSSHub 顺序不作为 rank。

同平台、同分类内按可用状态、真实 rank 或同口径平台分、连续观测时长排序；官媒只保留证据解释，不参与平台热度排序。详细 schema、比较阈值和 fallback 以实施计划 Day47 为准。

Day47 覆盖写入 `events.event_detail_json.platform_heat_analysis`，Day46 覆盖写入 `classification_detail`，双方保留对方 key；展示只读取同 run / 同窗口结果，过期分析回退 unknown。原始观测保存在 `event_snapshots.metrics_json.platform_observations`，不从分类结果重建观测。

## 最小测试用例

- 双平台 TopN + 官媒支撑 -> A。
- 单平台 TopN + 另一平台高相关搜索补强 + 官媒支撑 -> B。
- 双平台社区覆盖但无官媒支撑 -> C。
- 单平台 TopN + 官媒支撑 -> D。
- 单平台 TopN 且无补强 -> E。
- 只有官媒支撑 -> F。
- `zhihu_search` only 不标记 `zhihu_topn`。
- `weibo_cli` only 不标记 `weibo_topn`。
- 官媒支撑和 `official_coverage_level` 只影响分类、证据摘要和详情，不影响平台热度。
- 重复运行不会在 `events.event_detail_json.classification_detail` 中累加旧 JSON。
- embedding-only 匹配不能直接生成强跨平台覆盖。
- `evidence_summary` 对 `not_found` 官媒支撑启用保守表述。
- `classification_detail_json` 保留匹配证据、缺失字段和 source_status。
