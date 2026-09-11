# 热点事件分类与排序边界

## 文档目标

本文对应 Day 25：定义热点事件分类、类内排序和禁止跨平台总分的边界。

MVP 不输出单一 `total_priority_score`。热点结果拆成分类、平台出现情况、跨平台匹配方式、官媒支撑、置信度和证据摘要。

## 分类字段

```text
priority_category
platform_presence
cross_platform_match_type
official_support_status
confidence_level
category_rank
classification_detail
```

## 分类枚举

| 分类 | 条件 | 输出口径 |
|---|---|---|
| `A_cross_platform_with_official` | 微博 TopN 与知乎 TopN 自然重合，且官媒 `supported` / `weak_supported` | 多社区平台共同关注，且有官方报道支撑 |
| `B_single_platform_with_search_and_official` | 单一平台 TopN，另一社区平台搜索高相关命中，且官媒 `supported` / `weak_supported` | 一个社区平台上榜，另一平台有讨论补证，且有官方报道 |
| `C_cross_platform_without_official` | 两个社区平台有自然重合或高相关搜索补证，但官媒 `not_found` | 社区跨平台讨论成立，但缺少官媒事实支撑 |
| `D_single_platform_with_official` | 单一平台 TopN，无高相关社区搜索补证，但官媒 `supported` / `weak_supported` | 一个社区平台关注，且有官方报道 |
| `E_single_platform_only` | 单一平台 TopN，无官媒支撑，无高相关跨平台搜索补证 | 平台内候选，只能低置信或保守展示 |
| `F_official_only` | 只在官媒 RSS 出现，未匹配社区 TopN / 搜索补证 | 官方报道事件，当前社区样本未捕捉到讨论 |

## presence 字段

```text
zhihu_topn
zhihu_search
weibo_topn
weibo_cli
official_source
mock
```

边界：

- `zhihu_search = true` 不等于知乎 TopN。
- `weibo_cli = true` 不等于微博 TopN。
- `search_supported` 弱于 `natural_topn_overlap`。
- `official_source = true` 代表当前样本中存在官媒证据，不代表公众热度高。

## confidence_level

`confidence_level` 是证据链和分类稳定性的置信度，不是事实真伪判定。

MVP 默认规则：

| 分类 | 默认置信度 |
|---|---|
| A | `high` |
| B | `high` / `medium`，取决于搜索命中质量 |
| C | `medium` |
| D | `medium` |
| E | `low` / `medium`，取决于快照出现次数和补强质量 |
| F | `medium` / `low`，取决于官媒支撑强弱 |

## 类内排序

类内排序使用字典序 sort keys，不生成跨平台总分。

通用 sort keys：

```text
category_rank
official_support_rank      # supported > weak_supported > not_found > not_checked
match_strength_rank        # natural_topn_overlap > search_supported > weak_search_supported > single_platform_only > official_only > none
primary_rank_bucket        # top3 > top10 > top20 > tail > unknown
snapshot_presence_count    # 多快照出现次数，desc
rank_delta_direction       # rising > stable > falling > unknown
search_hit_quality_rank    # same_id/title_contained > entity_action_match > keyword_overlap > none
freshness_bucket           # <=24h > <=72h > <=7d > stale > unknown
source_health_rank         # use > fallback > unavailable > unknown
noise_rank                 # quality_flags 少者优先
```

## 禁止项

- 禁止把 `weibo_rank + zhihu_vote_count + official_source_count` 合成总分。
- 禁止把 `zhihu_search` 的互动数描述为知乎热榜热度。
- 禁止把微博 CLI `total_number_proxy` 描述为全站讨论量。
- 禁止把 RSSHub item 顺序描述为微博官方热度值。
- 禁止把 `confidence_level` 写成事实真伪保证。

## 代码位置

```text
backend/app/schemas/classification.py
backend/app/services/event_classification.py
```
