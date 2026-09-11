# Evaluation cases v0.1

生成时间：2026-09-11

## 目标

本文对应 Day 27：把第 3 周真实采样结果整理成可复用的人工标注评测样例。

产物分两层：

- 结构化样例：`backend/app/evaluation/cases/day27_evaluation_cases_v0_1.json`
- 人工说明：本文档

这批样例不是完整 Evaluation Runner，也不是新的采集器实现。它是后续评估 Agent 和确定性工具链的 gold cases，用于回放事件匹配、搜索增强过滤、热点分类、类内排序和 guardrails 表达边界。

## 输入材料

| 材料 | 用途 |
|---|---|
| `reports/zhihu-hotlist-search-sampling-v0.1.md` | 知乎 hot_list 与 zhihu_search 同平台增强样例 |
| `reports/weibo-rsshub-sampling-v0.1.md` | 微博 RSSHub 话题种子、字段缺失和弱 rank 边界 |
| `reports/weibo-to-zhihu-search-sampling-v0.1.md` | 微博话题到知乎搜索的正负匹配样例 |
| `reports/weibo-cli-search-statuses-limited-live-v0.1.md` | 微博 CLI 正文、时间、转评赞和 total_number proxy 边界 |
| `reports/official-news-structure-probe-v0.1.md` | 官媒 only 与 fallback 来源样例 |
| `reports/cross-source-classification-probe-v0.1.md` | Day20 分类和排序基线 |

## 样例覆盖

| 类型 | 样例 | 评估点 |
|---|---|---|
| 社区高热但官媒未报道 | `evt_c_001_taizi_li_tuchun_death` | 微博 TopN + 知乎搜索补证，官媒 `not_found` 时应归 C 类并保守表达 |
| 社区高热但官媒未报道 | `evt_c_002_children_house_second_bedroom` | 搜索命中含噪声时，只保留真正同事件结果 |
| 社区高热但官媒未报道 | `evt_c_003_netizen_donation_one_yuan` | 当前规则误拒同事件知乎结果，作为 recall 误差样例 |
| 单平台知乎热点 | `evt_e_001_zhihu_ancient_towns_similarity` | `zhihu_search` 只能增强 hot_list，不构成跨平台 |
| 微博 RSSHub 话题无法补到知乎讨论 | `evt_e_002_eating_show_hypokalemia` | 保留微博候选，但不能补造知乎讨论热度 |
| 官媒报道但社区低热 | `evt_f_001_chinanews_green_low_carbon_forum` | 官媒 use 来源可进入 F 类，但不能写成公众热议 |
| 官媒报道但社区低热 | `evt_f_002_xinhua_video_new_in_china` | fallback + 缺发布时间时只能弱支撑，类内排序低于新鲜 use 来源 |
| 微博 RSSHub 缺失 / 未覆盖降级 | `evt_degrade_001_zhihu_only_when_weibo_rsshub_missing` | 微博缺失不阻塞知乎候选分类，只降低微博 presence |

当前真实采样没有观察到 A/B/D 类正例，因此 v0.1 不伪造：

- `A_cross_platform_with_official`
- `B_single_platform_with_search_and_official`
- `D_single_platform_with_official`

这些会作为后续多轮同窗口采集的 coverage gaps。

## 人工期望分类

| Case | 人工期望分类 | 置信度 | 关键原因 |
|---|---|---|---|
| `evt_c_001_taizi_li_tuchun_death` | `C_cross_platform_without_official` | `medium` | 微博 TopN + 知乎高相关搜索补证，官媒未命中 |
| `evt_c_002_children_house_second_bedroom` | `C_cross_platform_without_official` | `medium` | 有一个有效知乎讨论补证，但存在搜索噪声 |
| `evt_c_003_netizen_donation_one_yuan` | `C_cross_platform_without_official` | `medium` | 人工判断应接受知乎同事件结果，当前规则误拒 |
| `evt_e_001_zhihu_ancient_towns_similarity` | `E_single_platform_only` | `medium` | 只有知乎 TopN；知乎搜索是同平台内部增强 |
| `evt_e_002_eating_show_hypokalemia` | `E_single_platform_only` | `low` | 微博 RSSHub + CLI 可保留，但知乎搜索无高相关结果 |
| `evt_f_001_chinanews_green_low_carbon_forum` | `F_official_only` | `medium` | 中国新闻网 use 来源，有发布时间，无社区命中 |
| `evt_f_002_xinhua_video_new_in_china` | `F_official_only` | `low` | 新华网 fallback，发布时间缺失，无社区命中 |
| `evt_degrade_001_zhihu_only_when_weibo_rsshub_missing` | `E_single_platform_only` | `medium` | 微博 RSSHub 缺失或未覆盖时，不阻塞知乎链路 |

## 匹配评测点

| Case | 人工标签 | 评估用途 |
|---|---|---|
| `match_001_zhihu_hotlist_same_question` | 同一事件，`accepted` | 同 question id / 标题强包含的正例 |
| `match_002_weibo_taizi_to_zhihu` | 同一事件，`accepted` | 微博短话题到知乎长问题的正例 |
| `match_003_weibo_netizen_donation_false_negative` | 同一事件，`accepted` | 当前规则 recall 漏召回样例 |
| `match_004_weibo_children_to_zhihu_daily_pollution` | 非同一事件，`audit_only` | 历史日报 / 聚合页污染样例 |
| `match_005_generic_apple_huawei_noise` | 非同一事件，`audit_only` | 泛词短话题误命中样例 |
| `match_006_weibo_eating_show_no_zhihu_support` | 非同一事件，`audit_only` | 相近健康/吃播话题不能自动合并 |

## Raw feature 标注

允许参与分类或类内排序的字段：

```text
zhihu_hot_list.rank
zhihu_hot_list.fetched_at
zhihu_search.retained_result_count_after_relevance_filter
zhihu_search.comment_count_after_relevance_filter
zhihu_search.vote_up_count_after_relevance_filter
zhihu_search.ranking_score_after_relevance_filter
zhihu_search.edit_time_after_relevance_filter
weibo_rsshub.topic_present
weibo_rsshub.list_position_as_weak_attention_signal
weibo_rsshub.fetched_at
weibo_cli.matched_status_count_after_relevance_filter
weibo_cli.latest_status_created_at_after_relevance_filter
weibo_cli.comments_count_after_relevance_filter
weibo_cli.reposts_count_after_relevance_filter
weibo_cli.attitudes_count_after_relevance_filter
weibo_cli.total_number_proxy_with_proxy_flag
official_rss.published_at_when_present
official_rss.source_name
official_rss.source_status
official_rss.source_coverage_count
```

必须由 `quality_flags` 排除的字段：

```text
zhihu_search.rejected_or_audit_only_results
zhihu_search.metrics_from_low_related_or_historical_results
weibo_cli.low_related_or_marketing_statuses
weibo_cli.total_number_proxy_without_search_result_total_proxy_flag
weibo_rsshub.hot_value_when_missing_or_unparsed
weibo_rsshub.pubDate_when_missing
weibo_rsshub.list_position_as_official_heat_value
official_rss.raw_metrics_when_absent
official_source_presence_as_public_heat_claim
```

## 误差样例

| Case | 类型 | 期望修正 |
|---|---|---|
| `err_001_false_negative_netizen_donation` | 搜索过滤 false negative | 加强实体、动作、对象等价匹配，不只看 n-gram 重合 |
| `err_002_false_positive_zhihu_daily_page` | 历史 / 聚合页污染 | 聚合页进入 audit-only，不参与分类和排序 |
| `err_003_false_positive_generic_apple_huawei` | 泛词短 query 污染 | 没有实体动作对象和时间支撑时只进 audit-only |
| `err_004_rsshub_rank_misuse` | feature misuse | RSSHub 顺序只能是弱 attention signal，不能写成官方热度值 |

## 后续接入建议

第 6 周 Evaluation Runner 可以直接消费结构化 JSON，最小指标包括：

```text
match_precision
match_recall
search_enrichment_false_positive_count
search_enrichment_false_negative_count
classification_accuracy
sort_order_agreement_within_category
audit_only_exclusion_accuracy
guardrail_overclaim_violation_count
```

Runner 对 event case 的最小回放方式：

```text
case.source_observations
-> build HotspotClassificationInput
-> classify_hotspot
-> compare with case.expected.priority_category / confidence_level / sort expectation
```

Runner 对 matching case 的最小回放方式：

```text
parent_title + enrichment_title + source_pair
-> match_event / SearchEnrichmentRelation
-> compare same_event, relation_decision, should_contribute_to_classification
```
