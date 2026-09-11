# Workflow Data Schema 与 Structured Output 规范

## 文档目标

本文定义模式 A：每日热点简报工作流中的数据结构契约。

Day 6 不只定义 LLM 输出，也定义工作流输入、工具输入输出和核心业务对象。所有工具调用、LLM 输出、状态流转和数据库写入都应基于这些 schema 做校验。

## 设计原则

- 工具输入和输出必须结构化。
- LLM 输出必须通过 schema validation 后才能进入后续阶段。
- 可选字段必须显式允许为空，不伪造缺失数据。
- 低置信度、数据缺失、降级处理和归档状态必须用字段表达。
- 状态流转由 Orchestrator / 状态机控制，不由 LLM 输出直接决定。
- schema 先服务模式 A，保留少量模式 B 扩展字段。

## 通用类型

### TimeWindow

```json
{
  "start": "datetime",
  "end": "datetime"
}
```

### ToolResultMeta

```json
{
  "tool_name": "string",
  "success": true,
  "retryable": false,
  "review_required": false,
  "blocks_auto_analysis": false,
  "blocks_publish": false,
  "warnings": ["string"],
  "errors": ["string"],
  "metrics": {}
}
```

说明：

- `review_required`：进入异步复核队列，不阻断自动采集分析链路。
- `blocks_auto_analysis`：阻断本次自动分析，通常用于系统级失败。
- `blocks_publish`：阻断正式发布，但不阻断后续自动采集分析。

## 枚举

### SourceType

```text
official_news
community_hotlist
community_question_hotlist
community_video_hotlist
```

### Platform

```text
people
chinanews
xinhua
cctv
weibo
zhihu
bilibili
```

### EventStatus

```text
active
cooling
archived
ignored
```

### TrendStatus

```text
new
rising
exploding
stable_high
cooling
second_wave
long_tail
unknown
```

### DraftStatus

```text
draft
draft_ready_for_review
draft_needs_review
draft_blocked_for_publish
```

### PublishStatus

```text
published
published_with_warning
blocked
```

### SnapshotType

```text
high_frequency
daily_compressed
```

### Severity

```text
info
warn
block
```

### SourceStatus

```text
use
fallback
experimental_fallback
postpone
use_pending_credentials
fallback_pending_credentials
```

说明：

- `use`：已验证，可进入正式 collector。
- `fallback`：可访问但字段或稳定性有缺口，只做补充。
- `experimental_fallback`：只用于采样、演示或弱候选补充。
- `postpone`：不进入正式采集。
- `use_pending_credentials`：官方契约明确，但凭据或额度仍需验证。
- `fallback_pending_credentials`：授权路径明确，但稳定性、额度或成本仍未确认。

### SourceOrigin

```text
official_rss
official_api
rsshub
weibo_cli
mock
```

### SignalRole

```text
event_signal
topic_discovery_signal
attention_signal
discussion_focus_signal
evidence_signal
search_enrichment_signal
mixed_signal
```

`signal_role` 描述该条标准化数据在流程中的用途。它不是热度分，也不是跨平台数值归一化。

## 工作流输入 Schema

### DailyBriefingRunConfig

```json
{
  "run_id": "string",
  "time_window": {
    "start": "datetime",
    "end": "datetime"
  },
  "platforms": ["people", "chinanews", "xinhua", "cctv", "weibo", "zhihu", "bilibili"],
  "domains": ["社会", "财经", "科技", "文娱", "体育", "国际"],
  "keywords": ["string"],
  "max_events": 30,
  "snapshot_mode": "auto",
  "requires_manual_publish": true
}
```

### SourceFetchConfig

```json
{
  "source_id": "string",
  "platform": "string",
  "source_type": "string",
  "enabled": true,
  "fetch_interval_minutes": 180,
  "limit_per_run": 50,
  "timeout_seconds": 20,
  "retry_limit": 2
}
```

### SourceFetchResult

`SourceFetchResult` 是单个数据源在一次采集 run 中的结果摘要。Evaluation 中的 `source_success_rate`、`item_count_by_source` 和数据源稳定性指标应基于该结构计算。

```json
{
  "source_id": "string",
  "source_name": "string",
  "platform": "people",
  "source_type": "official_news",
  "success": true,
  "item_count": 0,
  "retry_count": 0,
  "latency_ms": 0,
  "error_type": "string|null",
  "error_message": "string|null"
}
```

### RetentionPolicy

```json
{
  "raw_payload_retention_days": 3,
  "item_retention_days": 14,
  "high_frequency_snapshot_retention_days": 7,
  "daily_snapshot_retention_days": 90,
  "draft_report_retention_days": 7,
  "tool_log_retention_days": 7
}
```

## 采集与标准化 Schema

### RawItem

`RawItem` 是采集工具返回的原始条目。它不直接进入事件分析，必须先标准化。

```json
{
  "source_id": "string",
  "source_name": "string",
  "source_type": "official_news",
  "platform": "people",
  "raw_payload": {},
  "fetched_at": "datetime"
}
```

### NormalizedItem

`NormalizedItem` 是所有来源统一后的基础内容结构。这里的 normalized 指数据结构归一化，不表示把微博、知乎和官媒的热度指标归一化成同一个数值尺度。

```json
{
  "item_id": "string",
  "source_id": "string",
  "source_name": "string",
  "source_type": "official_news",
  "source_status": "use",
  "source_origin": "official_rss",
  "platform": "people",
  "signal_role": "evidence_signal",
  "channel": "string|null",
  "rank": "number|null",
  "title": "string",
  "url": "string",
  "published_at": "datetime|null",
  "fetched_at": "datetime",
  "author": "string|null",
  "summary": "string|null",
  "content_text": "string|null",
  "raw_metrics": {},
  "raw_payload": {},
  "normalized": {},
  "source_citation": {},
  "quality_flags": ["string"],
  "signal_contribution_role": ["string"],
  "content_hash": "string",
  "event_text_for_match": "string|null",
  "event_text_for_embedding": "string|null",
  "retention_until": "datetime|null"
}
```

必填字段：

```text
item_id
source_id
source_name
source_type
source_status
source_origin
platform
signal_role
title
url
fetched_at
content_hash
```

`signal_contribution_role` 是可选派生字段，MVP 阶段不要求 collector 必填。分类阶段可以根据 `source_origin`、`signal_role`、`raw_metrics` 和 `quality_flags` 推导该信号对 `attention`、`discussion`、`evidence`、`authority`、`velocity` 等维度的贡献。

字段质量约束：

- 缺少 `title` 时必须写入 `quality_flags = ["missing_title"]`。
- 缺少 `url` 时必须写入 `quality_flags = ["missing_url"]`。
- RSSHub 微博列表序号必须标记为 `list_position_derived_from_rss_order` 和 `not_heat_metric`。
- 搜索增强结果低相关时只进入审计日志，不进入事件分类支撑。

### RawMetric

`RawMetric` 用于保存平台原始字段或代理字段，不做跨平台数值归一化。

允许出现但不保证每个来源都有的 key：

```text
rank
rank_delta
list_position
hot_value
comment_count
vote_count
ranking_score
edit_time
read_count
mention_count
report_count
total_number_proxy
matched_status_count
published_at
fetched_at
```

规则：

- `rank` / `list_position` 必须记录来源，例如 `rank_source` 或 quality flag。
- `hot_value` 缺失时不得补造。
- `total_number_proxy` 只能表达搜索结果规模代理，不等于平台真实讨论总量。
- 官媒报道数量只能作为覆盖和证据支撑，不能直接解释为公众热度。

## 事件处理 Schema

### SourceCitation

```json
{
  "item_id": "string|null",
  "source_signal_id": "string|null",
  "title": "string",
  "url": "string|null",
  "source_name": "string",
  "source_type": "official_news",
  "source_status": "use",
  "source_origin": "official_rss",
  "platform": "people",
  "published_at": "datetime|null",
  "fetched_at": "datetime",
  "quality_flags": ["string"],
  "raw_metrics_used": {},
  "citation_role": "primary|supporting|discussion|official_support|search_enrichment|audit"
}
```

规则：

- 缺少原始链接时必须写入 `quality_flags = ["missing_url"]`。
- `source_status` 和 `source_origin` 必须保留到展示层，供前端和日报解释数据可用性。
- `citation_role = audit` 的证据只能用于审计说明，不应作为事实或分类支撑。

### SearchEnrichmentRelation

`SearchEnrichmentRelation` 记录搜索增强结果与已有候选之间的相关性判断。它用于审计 `zhihu_search` 和微博 CLI 搜索结果是否可以参与分类支撑。

```json
{
  "source": "zhihu_search|weibo_cli|global_search",
  "parent_candidate_id": "string|null",
  "parent_signal_id": "string|null",
  "user_query_id": "string|null",
  "parent_title": "string",
  "enrichment_title": "string|null",
  "decision": "accepted|audit_only|rejected",
  "confidence": 0.0,
  "matched_by": ["same_zhihu_question_id|title_containment|hashtag_match|keyword_overlap_high"],
  "rejected_by": ["keyword_overlap_low|historical_content_pollution|time_window_conflict"],
  "quality_flags": ["string"],
  "audit_only": false
}
```

规则：

- `zhihu_search` 和微博 CLI 搜索结果必须挂到已有候选、已有信号或用户指定事件。
- `decision = accepted` 才能参与分类支撑。
- `decision = audit_only` 或 `rejected` 只能进入审计日志，不参与分类支撑。
- `global_search` 只用于模式 B 或人工补证，不进入模式 A 常规定时热榜采集。

### SourceSignal

`SourceSignal` 保存来源级原始信号、平台内排序特征、搜索增强关系和分类依据。它位于 `NormalizedItem` 和 `EventSignal` 之间。

```json
{
  "source_signal_id": "string",
  "item_id": "string|null",
  "source_id": "string",
  "source_type": "community_hotlist",
  "source_status": "use",
  "source_origin": "official_api",
  "platform": "zhihu",
  "signal_role": "attention_signal",
  "title": "string",
  "url": "string|null",
  "published_at": "datetime|null",
  "fetched_at": "datetime",
  "parent_candidate_id": "string|null",
  "parent_signal_id": "string|null",
  "user_query_id": "string|null",
  "raw_metrics": {},
  "platform_features": {},
  "classification_features": {},
  "relation_to_parent": {},
  "contributes_to_classification": true,
  "audit_only": false,
  "quality_flags": ["string"]
}
```

边界：

- `signal_role = search_enrichment_signal` 时，必须有 `parent_candidate_id`、`parent_signal_id` 或 `user_query_id`。
- 搜索增强结果如果 `audit_only = true`，必须设置 `contributes_to_classification = false`。
- RSSHub 微博话题种子可以生成弱候选，但不能单独写成微博真实热度证据。

### EventSignal

`EventSignal` 可以由规则或 LLM 生成，但必须通过 schema 校验。

```json
{
  "event_signal_id": "string",
  "source_signal_ids": ["string"],
  "title": "string",
  "keywords": ["string"],
  "entities": ["string"],
  "event_type": "string|null",
  "action_terms": ["string"],
  "event_time_hint": "datetime|null",
  "event_text_for_match": "string|null",
  "semantic_fingerprint": {
    "event_text_for_embedding": "string|null",
    "embedding_model": "string|null",
    "embedding_vector_id": "string|null",
    "embedding_created_at": "datetime|null",
    "embedding_quality_flags": ["string"]
  },
  "confidence": 0.0,
  "is_weak_signal": false,
  "weak_signal_reason": "string|null",
  "source_statuses": ["use"],
  "source_roles": ["attention_signal"],
  "platforms": ["zhihu"],
  "source_signal_count": 1,
  "quality_flags": ["string"]
}
```

B站视频条目默认可以标记：

```json
{
  "is_weak_signal": true,
  "weak_signal_reason": "video_title_may_not_represent_event_fact"
}
```

### EventCandidate

`EventCandidate` 是事件合并前的候选事件容器，用于把主发现来源和搜索增强来源挂在同一个候选下。

```json
{
  "event_candidate_id": "string",
  "title": "string",
  "candidate_key": "string",
  "primary_event_signal_id": "string",
  "event_signal_ids": ["string"],
  "source_signal_ids": ["string"],
  "created_from": "official_rss|zhihu_hot_list|weibo_rsshub|user_query|mixed",
  "platform_presence": {
    "zhihu_hot_list": true,
    "zhihu_search": false,
    "weibo_rsshub": false,
    "weibo_cli": false,
    "official_rss": false
  },
  "source_statuses": ["use"],
  "search_enrichment_relations": [],
  "rejected_enrichment_count": 0,
  "audit_only_signal_count": 0,
  "confidence_level": "high|medium|low|unknown",
  "eligible_for_classification": true,
  "quality_flags": ["string"]
}
```

候选生成边界：

- 知乎 `hot_list` 可以创建模式 A 热点候选。
- 微博 RSSHub 可以创建模式 A 话题候选，但默认是弱话题种子。
- 官媒 RSS 可以创建事实 / 报道候选。
- `zhihu_search` 和微博 CLI 只补强已有候选或用户指定事件，不能递归扩展新采集链路。

### Event

```json
{
  "event_id": "string",
  "title": "string",
  "summary": "string|null",
  "domain": "string|null",
  "event_type": "string|null",
  "keywords": ["string"],
  "entities": ["string"],
  "status": "active",
  "first_seen_at": "datetime",
  "last_seen_at": "datetime",
  "source_count": 0,
  "platform_count": 0,
  "primary_sources": [],
  "peak_score": 0.0,
  "last_score": 0.0,
  "archive_reason": "string|null",
  "archived_at": "datetime|null"
}
```

### EventResolution

```json
{
  "event_id": "string",
  "action": "merge|create|skip|candidate_review",
  "item_ids": ["string"],
  "confidence": 0.0,
  "reason": "string",
  "matched_by": ["id_match|url_match|title_containment|entity_time_rule|bm25_ngram|embedding_rerank"],
  "match_features_json": {
    "id_match": false,
    "url_match": false,
    "title_containment": false,
    "keyword_overlap": 0.0,
    "ngram_overlap": 0.0,
    "bm25_score": 0.0,
    "embedding_similarity": null,
    "entity_overlap": 0.0,
    "action_overlap": 0.0,
    "object_overlap": 0.0,
    "time_distance_hours": null,
    "hard_constraints_passed": false,
    "guardrail_flags": ["string"]
  },
  "review_required": false,
  "blocks_auto_analysis": false,
  "blocks_publish": false
}
```

合并策略：

- 高置信度自动合并。
- 中置信度写入候选关系，不阻断自动链路。
- 核心实体或时间冲突禁止自动合并。
- BM25 / n-gram 提供可解释召回，embedding 提供语义召回或 rerank。
- embedding 高相似不能单独触发自动合并，必须同时满足实体、动作、对象或时间窗口硬约束。
- 弱信号不强行写成事实。

## 评分与快照 Schema

### PlatformScore

```json
{
  "event_id": "string",
  "platform": "weibo",
  "score": 0.0,
  "score_status": "ok|partial|unknown",
  "raw_metrics_used": {},
  "normalized_score": 0.0,
  "weight": 1.0,
  "score_detail": {}
}
```

### EventScore

```json
{
  "event_id": "string",
  "official_score": 0.0,
  "community_score": 0.0,
  "coverage_score": 0.0,
  "velocity_score": 0.0,
  "controversy_score": 0.0,
  "total_score": 0.0,
  "trend_status": "unknown",
  "score_detail_json": {},
  "calculated_at": "datetime"
}
```

### EventSnapshot

```json
{
  "snapshot_id": "string",
  "event_id": "string",
  "snapshot_type": "high_frequency",
  "snapshot_at": "datetime",
  "total_score": 0.0,
  "trend_status": "unknown",
  "platform_scores": [],
  "source_count": 0,
  "platform_count": 0,
  "is_compressed": false,
  "retention_until": "datetime|null"
}
```

### DailyCompressedSnapshot

```json
{
  "event_id": "string",
  "snapshot_date": "date",
  "snapshot_type": "daily_compressed",
  "max_score": 0.0,
  "avg_score": 0.0,
  "last_score": 0.0,
  "peak_platform": "string|null",
  "source_count": 0,
  "platform_count": 0,
  "trend_status": "unknown",
  "compressed_from_count": 0
}
```

## 简报与发布 Schema

### EventCard

```json
{
  "event_id": "string",
  "title": "string",
  "summary": "string",
  "domain": "string|null",
  "priority_category": "A_cross_platform_with_official|B_single_platform_with_search_and_official|C_cross_platform_without_official|D_single_platform_with_official|E_single_platform_only|F_official_only|unknown",
  "category_rank": 1,
  "platform_presence": {
    "zhihu_topn": true,
    "zhihu_search": false,
    "weibo_topn": false,
    "weibo_cli": false,
    "official_source": false,
    "mock": false
  },
  "cross_platform_match_type": "natural_topn_overlap|search_supported|weak_search_supported|single_platform_only|official_only|none|unknown",
  "official_support_status": "supported|weak_supported|not_found|not_checked",
  "confidence_level": "high|medium|low|unknown",
  "primary_platform": "zhihu|null",
  "primary_platform_rank": 3,
  "platform_bucket": "top3|top10|top20|tail|null",
  "platform_strength": "strong|medium|weak|unknown|null",
  "rank_delta": "number|null",
  "trend_status": "unknown",
  "evidence_summary": {
    "lead": "string",
    "platform_evidence": ["string"],
    "official_evidence": ["string"],
    "discussion_evidence": ["string"],
    "limitations": ["string"],
    "conservative_language_required": false
  },
  "source_citations": [],
  "quality_flags": ["string"],
  "risk_notes": ["string"],
  "publish_eligibility": "ready_for_review|needs_review|blocked_for_publish",
  "classification_detail": {}
}
```

规则：

- `EventCard` 必须至少包含一个 `SourceCitation`。
- `official_support_status = not_found` 时，必须使用保守措辞，并在 `risk_notes` 中标记 `official_support_not_found`。
- `confidence_level = low` 的事件不能直接 `ready_for_review`。
- `priority_category` 只表达分类，不是跨平台总分。
- `platform_presence` 必须区分 TopN 出现和搜索补强出现，避免把 `search_supported` 写成自然双平台上榜。

### HotspotClassification

`HotspotClassification` 是 Day25 的分类结果，可以写入 `EventCard.classification_detail`。

```json
{
  "priority_category": "A_cross_platform_with_official|B_single_platform_with_search_and_official|C_cross_platform_without_official|D_single_platform_with_official|E_single_platform_only|F_official_only|unknown",
  "confidence_level": "high|medium|low|unknown",
  "category_rank": 0,
  "cross_platform_match_type": "natural_topn_overlap|search_supported|weak_search_supported|single_platform_only|official_only|none|unknown",
  "official_support_status": "supported|weak_supported|not_found|not_checked",
  "platform_presence": {},
  "sort_key": {
    "category_rank": 0,
    "official_support_rank": 0,
    "match_strength_rank": 0,
    "primary_rank_bucket_rank": 0,
    "snapshot_presence_rank": 0,
    "rank_delta_rank": 0,
    "search_hit_quality_rank": 0,
    "freshness_rank": 0,
    "source_health_rank": 0,
    "noise_rank": 0
  },
  "reasons": ["string"],
  "limitations": ["string"],
  "quality_flags": ["string"]
}
```

规则：

- `sort_key` 是字典序排序键，不是跨平台综合分。
- MVP 禁止输出或依赖 `total_priority_score`。
- `confidence_level` 表示证据链完整度和分类稳定性，不表示事实真伪保证。

### BriefingSection

```json
{
  "section_type": "overview|top_events|domain_hotspots|platform_hotspots|rising_events|risk_notes|sources",
  "title": "string",
  "summary": "string|null",
  "event_cards": [],
  "notes": ["string"]
}
```

### DailyBriefing

```json
{
  "daily_report_id": "string|null",
  "run_id": "string",
  "report_date": "date",
  "title": "string",
  "summary": "string",
  "sections": [],
  "draft_status": "draft|draft_ready_for_review|draft_needs_review|draft_blocked",
  "publish_status": "published|published_with_warning|blocked|draft|null",
  "source_citation_count": 0,
  "risk_notes": ["string"],
  "created_at": "datetime",
  "retention_until": "datetime|null"
}
```

规则：

- `source_citation_count` 由所有 `EventCard.source_citations` 统计得到。
- `DailyBriefing` 必须至少包含一个 section。
- Evaluation / guardrails 可以阻断发布，但不应删除已采集证据。

### PublishedReport

```json
{
  "daily_report_id": "string",
  "source_draft_id": "string",
  "publish_status": "published",
  "published_at": "datetime",
  "confirmed_by": "human|null",
  "summary": "string",
  "sections": []
}
```

## 质量检查与 Guardrails Schema

### CriticIssue

```json
{
  "severity": "warn",
  "issue_type": "duplicate|missing_source|overclaim|imbalance|weak_signal|format_error",
  "message": "string",
  "event_id": "string|null",
  "suggested_fix": "string|null"
}
```

### CriticResult

```json
{
  "status": "pass|warn|block",
  "issues": [],
  "review_required": false,
  "blocks_publish": false
}
```

### GuardrailViolation

```json
{
  "rule_id": "string",
  "severity": "warn",
  "message": "string",
  "event_id": "string|null",
  "created_at": "datetime"
}
```

### GuardrailResult

```json
{
  "decision": "ready_for_review|needs_review|blocked_for_publish",
  "violations": [],
  "review_required": false,
  "blocks_publish": false
}
```

## 异步复核 Schema

### HumanReviewTask

```json
{
  "review_task_id": "string",
  "run_id": "string",
  "review_type": "event_merge_candidate|briefing_quality|publish_decision",
  "status": "pending|resolved|ignored",
  "priority": "low|medium|high",
  "reason": "string",
  "payload": {},
  "created_at": "datetime",
  "resolved_at": "datetime|null"
}
```

人工复核是异步队列，不阻断每 2-3 小时一次的自动采集分析链路。

## Evaluation Schema

### EvaluationMetric

```json
{
  "name": "string",
  "value": 0.0,
  "unit": "ratio|count|bytes|milliseconds",
  "thresholds": {
    "pass": "string|null",
    "warn": "string|null",
    "fail": "string|null"
  },
  "status": "pass|warn|fail"
}
```

### EvaluationRun

```json
{
  "evaluation_run_id": "string",
  "run_id": "string",
  "daily_report_id": "string|null",
  "metrics": [],
  "created_at": "datetime"
}
```

建议指标：

- 工具调用成功率。
- 数据源采集成功率。
- 字段完整率。
- 候选合并比例。
- 异步复核任务数量。
- 日报重复率。
- 来源覆盖率。
- Guardrail violation 数量。
- 数据库大小。
- 快照压缩数量。
- 清理任务成功率。

## 清理与归档 Schema

### CleanupRun

```json
{
  "cleanup_run_id": "string",
  "started_at": "datetime",
  "finished_at": "datetime|null",
  "deleted_raw_payload_count": 0,
  "deleted_draft_count": 0,
  "deleted_tool_log_count": 0,
  "compressed_snapshot_count": 0,
  "archived_event_count": 0,
  "status": "succeeded|failed|partial",
  "errors": ["string"]
}
```

### StorageMetric

```json
{
  "measured_at": "datetime",
  "database_size_bytes": 0,
  "items_count": 0,
  "events_count": 0,
  "event_snapshots_count": 0,
  "daily_reports_count": 0,
  "tool_call_count": 0,
  "storage_status": "ok|warning|critical"
}
```

## 模式 B 预留 Schema

v0.3 不实现模式 B 完整链路，只保留可复用结构。

### EventQuery

```json
{
  "event_query_id": "string",
  "query": "string",
  "platforms": ["string"],
  "time_window": {
    "start": "datetime",
    "end": "datetime"
  },
  "filters": {},
  "created_at": "datetime"
}
```

### RetrievedEvidence

```json
{
  "evidence_id": "string",
  "event_id": "string|null",
  "item_id": "string|null",
  "source_signal_id": "string|null",
  "title": "string",
  "url": "string|null",
  "source_name": "string",
  "platform": "string",
  "published_at": "datetime|null",
  "match_score": 0.0,
  "confidence": 0.0,
  "quality_flags": ["string"]
}
```

### EventSearchResult

```json
{
  "event_query_id": "string",
  "matched_events": [],
  "retrieved_evidence": [],
  "platform_heat": [],
  "limitations": ["string"],
  "generated_at": "datetime"
}
```

模式 B 边界：

- `matched_events` 复用 `EventCard`，避免指定事件搜索与日报展示产生两套解释口径。
- `retrieved_evidence` 可以包含历史 item、source signal 或 event 级证据。
- `platform_heat` 只能使用已有平台内字段和代理字段，不输出跨平台真实热度结论。

## 工具 Input / Output Wrapper

工具 wrapper 用于把核心业务对象组合成工具级输入输出。核心字段定义仍以本文前面的业务对象 schema 为准。

### FetchSourceItemsInput

```json
{
  "run_config": {},
  "source_configs": []
}
```

字段：

- `run_config`：`DailyBriefingRunConfig`
- `source_configs`：`SourceFetchConfig[]`

### FetchSourceItemsOutput

```json
{
  "meta": {},
  "raw_items": [],
  "source_results": []
}
```

字段：

- `meta`：`ToolResultMeta`
- `raw_items`：`RawItem[]`
- `source_results`：`SourceFetchResult[]`

### NormalizeRawItemsInput

```json
{
  "run_id": "string",
  "raw_items": []
}
```

字段：

- `raw_items`：`RawItem[]`

### NormalizeRawItemsOutput

```json
{
  "meta": {},
  "normalized_items": [],
  "dropped_items": []
}
```

字段：

- `meta`：`ToolResultMeta`
- `normalized_items`：`NormalizedItem[]`
- `dropped_items`：因缺少标题、链接等关键字段被丢弃的条目摘要

### ExtractEventSignalsInput

```json
{
  "run_id": "string",
  "items": [],
  "use_llm": true
}
```

字段：

- `items`：`NormalizedItem[]`

### ExtractEventSignalsOutput

```json
{
  "meta": {},
  "event_signals": []
}
```

字段：

- `meta`：`ToolResultMeta`
- `event_signals`：`EventSignal[]`

### MatchAndResolveEventsInput

```json
{
  "run_id": "string",
  "event_signals": [],
  "existing_events": [],
  "match_config": {
    "use_bm25_ngram": true,
    "use_embedding": true,
    "auto_merge_confidence_threshold": 0.85,
    "candidate_review_confidence_threshold": 0.60
  }
}
```

字段：

- `event_signals`：`EventSignal[]`
- `existing_events`：`Event[]`
- `match_config`：事件匹配配置，允许 embedding 不可用时降级为规则 + BM25 / n-gram

### EventMatchRetrievalInput

```json
{
  "run_id": "string",
  "event_signal": {},
  "candidate_events": [],
  "limit": 20
}
```

字段：

- `event_signal`：当前待匹配的 `EventSignal`
- `candidate_events`：候选 `Event[]`
- `limit`：最大召回数量

### EventMatchRetrievalOutput

```json
{
  "meta": {},
  "candidates": [
    {
      "event_id": "string",
      "retrieval_method": "bm25_ngram|embedding",
      "bm25_score": 0.0,
      "ngram_overlap": 0.0,
      "embedding_similarity": null,
      "matched_fragments": ["string"]
    }
  ]
}
```

字段：

- `meta`：`ToolResultMeta`
- `candidates`：召回候选及可审计分数

### EmbedEventTextInput

```json
{
  "run_id": "string",
  "event_signal_id": "string",
  "event_text_for_embedding": "string",
  "embedding_model": "string"
}
```

### EmbedEventTextOutput

```json
{
  "meta": {},
  "embedding_model": "string",
  "embedding_vector_id": "string|null",
  "embedding_created_at": "datetime|null",
  "embedding_quality_flags": ["string"]
}
```

### RerankEventMatchesInput

```json
{
  "run_id": "string",
  "event_signal": {},
  "retrieved_candidates": [],
  "match_config": {}
}
```

### RerankEventMatchesOutput

```json
{
  "meta": {},
  "ranked_candidates": [
    {
      "event_id": "string",
      "match_confidence": "high|medium|low|rejected",
      "recommended_action": "merge|candidate_review|reject|create",
      "matched_by": ["string"],
      "match_features_json": {}
    }
  ]
}
```

### MatchAndResolveEventsOutput

```json
{
  "meta": {},
  "event_resolutions": [],
  "events": [],
  "review_tasks": []
}
```

字段：

- `meta`：`ToolResultMeta`
- `event_resolutions`：`EventResolution[]`
- `events`：新建或更新后的 `Event[]`
- `review_tasks`：`HumanReviewTask[]`

### CalculateEventScoresInput

```json
{
  "run_id": "string",
  "events": [],
  "items": [],
  "historical_snapshots": []
}
```

字段：

- `events`：`Event[]`
- `items`：`NormalizedItem[]`
- `historical_snapshots`：`EventSnapshot[]`

### CalculateEventScoresOutput

```json
{
  "meta": {},
  "event_scores": [],
  "platform_scores": [],
  "event_snapshots": []
}
```

字段：

- `meta`：`ToolResultMeta`
- `event_scores`：`EventScore[]`
- `platform_scores`：`PlatformScore[]`
- `event_snapshots`：`EventSnapshot[]`

### GenerateDailyBriefingInput

```json
{
  "run_id": "string",
  "report_date": "date",
  "events": [],
  "event_scores": [],
  "source_citations": [],
  "section_config": []
}
```

字段：

- `events`：`Event[]`
- `event_scores`：`EventScore[]`
- `source_citations`：`SourceCitation[]`

### GenerateDailyBriefingOutput

```json
{
  "meta": {},
  "daily_briefing": {}
}
```

字段：

- `meta`：`ToolResultMeta`
- `daily_briefing`：`DailyBriefing`

### ReviewBriefingQualityInput

```json
{
  "run_id": "string",
  "daily_briefing": {},
  "events": [],
  "source_citations": []
}
```

字段：

- `daily_briefing`：`DailyBriefing`
- `events`：`Event[]`
- `source_citations`：`SourceCitation[]`

### ReviewBriefingQualityOutput

```json
{
  "meta": {},
  "critic_result": {}
}
```

字段：

- `meta`：`ToolResultMeta`
- `critic_result`：`CriticResult`

### RunBriefingGuardrailsInput

```json
{
  "run_id": "string",
  "daily_briefing": {},
  "critic_result": {},
  "events": [],
  "source_citations": []
}
```

字段：

- `daily_briefing`：`DailyBriefing`
- `critic_result`：`CriticResult`
- `events`：`Event[]`
- `source_citations`：`SourceCitation[]`

### RunBriefingGuardrailsOutput

```json
{
  "meta": {},
  "guardrail_result": {},
  "violations": []
}
```

字段：

- `meta`：`ToolResultMeta`
- `guardrail_result`：`GuardrailResult`
- `violations`：`GuardrailViolation[]`

### CreateHumanReviewTaskInput

```json
{
  "run_id": "string",
  "review_type": "event_merge_candidate|briefing_quality|publish_decision",
  "payload": {},
  "reason": "string",
  "priority": "low|medium|high"
}
```

### CreateHumanReviewTaskOutput

```json
{
  "meta": {},
  "human_review_task": {}
}
```

字段：

- `meta`：`ToolResultMeta`
- `human_review_task`：`HumanReviewTask`

### SaveDailyReportInput

```json
{
  "run_id": "string",
  "daily_briefing": {},
  "guardrail_result": {},
  "publish_status": "draft|draft_ready_for_review|draft_needs_review|draft_blocked_for_publish|published|published_with_warning|blocked"
}
```

字段：

- `daily_briefing`：`DailyBriefing`
- `guardrail_result`：`GuardrailResult`

### SaveDailyReportOutput

```json
{
  "meta": {},
  "daily_briefing": {},
  "published_report": {}
}
```

字段：

- `meta`：`ToolResultMeta`
- `daily_briefing`：`DailyBriefing|null`
- `published_report`：`PublishedReport|null`

### RenderBriefingImageInput

```json
{
  "run_id": "string",
  "daily_report": {},
  "template": "default",
  "output_format": "png"
}
```

字段：

- `daily_report`：`PublishedReport`

### RenderBriefingImageOutput

```json
{
  "meta": {},
  "render_result": {}
}
```

字段：

- `meta`：`ToolResultMeta`
- `render_result`：`RenderResult`

### RenderResult

```json
{
  "success": true,
  "image_path": "string",
  "width": 1080,
  "height": 1920,
  "warnings": ["string"]
}
```

### RunEvaluationSuiteInput

```json
{
  "run_id": "string",
  "daily_report_id": "string|null",
  "evaluation_scope": ["string"]
}
```

### RunEvaluationSuiteOutput

```json
{
  "meta": {},
  "evaluation_run": {}
}
```

字段：

- `meta`：`ToolResultMeta`
- `evaluation_run`：`EvaluationRun`

### SearchExistingEvidenceInput

```json
{
  "event_query": {}
}
```

字段：

- `event_query`：`EventQuery`

### SearchExistingEvidenceOutput

```json
{
  "meta": {},
  "event_search_result": {}
}
```

字段：

- `meta`：`ToolResultMeta`
- `event_search_result`：`EventSearchResult`

## 工具输入输出映射

| 工具 | 输入 | 输出 |
|---|---|---|
| `fetch_source_items` | `FetchSourceItemsInput` | `FetchSourceItemsOutput` |
| `normalize_raw_items` | `NormalizeRawItemsInput` | `NormalizeRawItemsOutput` |
| `extract_event_signals` | `ExtractEventSignalsInput` | `ExtractEventSignalsOutput` |
| `match_and_resolve_events` | `MatchAndResolveEventsInput` | `MatchAndResolveEventsOutput` |
| `calculate_event_scores` | `CalculateEventScoresInput` | `CalculateEventScoresOutput` |
| `generate_daily_briefing` | `GenerateDailyBriefingInput` | `GenerateDailyBriefingOutput` |
| `review_briefing_quality` | `ReviewBriefingQualityInput` | `ReviewBriefingQualityOutput` |
| `run_briefing_guardrails` | `RunBriefingGuardrailsInput` | `RunBriefingGuardrailsOutput` |
| `create_human_review_task` | `CreateHumanReviewTaskInput` | `CreateHumanReviewTaskOutput` |
| `save_daily_report` | `SaveDailyReportInput` | `SaveDailyReportOutput` |
| `render_briefing_image` | `RenderBriefingImageInput` | `RenderBriefingImageOutput` |
| `run_evaluation_suite` | `RunEvaluationSuiteInput` | `RunEvaluationSuiteOutput` |
| `search_existing_evidence` | `SearchExistingEvidenceInput` | `SearchExistingEvidenceOutput` |

## 校验规则

基础校验：

- `title` 和 `url` 缺失的 item 不进入事件抽取。
- LLM 输出的 `confidence` 必须在 0 到 1 之间。
- `EventCard` 必须至少有一个 `source_citations`，否则不能进入正式发布。
- `draft_status` 和 `publish_status` 不能混用。
- `archived` 和 `ignored` 事件默认不参与热度重算。
- `raw_payload` 必须有 `retention_until` 或可清理标记。

发布校验：

- 单一社区来源不能写成确定事实。
- B站弱信号不能单独作为事实来源。
- `blocks_publish = true` 时不能生成 `published` 状态。
- 未人工确认的草稿不能成为公开发布版本。

## 结论

Day 6 的 schema 设计把前五天的产品模式、数据源、工作流、工具和数据生命周期统一为可校验的数据契约。

后续实现时，数据库模型、Pydantic 模型、工具注册表和测试用例都应以本文为基础。工具文档可以描述“做什么”，本文负责约束“输入输出长什么样、哪些字段必须存在、哪些状态允许流转”。
