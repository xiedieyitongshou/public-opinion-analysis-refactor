# 事件抽取设计：规则主路径与 LLM fallback

## 目标

本文定义 `extract_event_signals` 的第一版实现策略。

第一版目标不是做完整语义理解，而是稳定地产出可被 Day 39 事件匹配使用的 `EventSignal`：

```text
SourceSignal
-> rule_extract_signal
-> assess_extraction_quality
-> optional llm_refine_signal
-> strict schema validation
-> EventSignal
```

## 设计判断

旧项目中基于词频的方案对复杂话题效果有限，主要问题是：

- 高频词不等于事件主题。
- 泛词、营销词和平台壳会污染结果。
- 同义改写、实体别名和动作关系难以处理。
- 短话题只靠词频容易误判。

当前版本可以引入 LLM，但不应让 LLM 成为主路径。推荐采用 hybrid：

- 规则抽取负责稳定、可解释、低成本的 baseline。
- LLM 作为低置信度场景的 fallback / refinement。
- LLM 输出必须通过 strict schema validation。
- LLM 失败时保留规则结果，并写入审计日志。

## 输入与输出

输入使用 Day 37 已定义的工具 schema：

```text
ExtractEventSignalsInput.source_signals: SourceSignal[]
```

不得绕过 `SourceSignal` 直接从多个 `NormalizedItem` 生成跨来源 `EventSignal`。

输出：

```text
ExtractEventSignalsOutput.event_signals: EventSignal[]
```

`EventSignal` 只保存事件级字段和来源引用，不重复存储 `raw_metrics`、`source_citation`、`source_id` 等来源级字段。

## 规则主路径

### 1. 输入过滤

对每个 `SourceSignal`：

- `audit_only = true`：不生成可合并 `EventSignal`，写入 `audit_only_source_signal_ids`。
- `contributes_to_classification = false`：默认不进入可合并信号，可保留审计记录。
- `title` 缺失：记录错误，不生成信号。

### 2. 基础文本

第一版主要使用：

```text
SourceSignal.title
SourceSignal.platform_features
SourceSignal.classification_features
SourceSignal.raw_metrics 中可解释的 topic / query / keyword / tag 类字段
SourceSignal.quality_flags
```

Day 38 第一版不依赖摘要或正文回溯做抽取，只要求保证证据链可追踪。后续如果需要摘要或正文，必须通过 `SourceSignal.item_id` 回溯 `NormalizedItem`，不要假设 `SourceSignal` 自带 `summary` 或 `content_text`。

证据链为：

```text
EventSignal.source_signal_ids
-> SourceSignal.item_id
-> NormalizedItem.source_citation / raw_payload / summary / content_text
```

### 3. 候选词生成

候选词来源：

- 标题按标点、空格、数字和英文边界切分。
- 提取 hashtag、书名号、引号中的短语。
- 对连续中文片段生成 2 到 4 字 n-gram。
- 保留英文、数字、品牌名和平台字段中的 topic / query / keyword。

### 4. 停用词与泛词过滤

停用词用于过滤 `keywords`，但不要直接丢弃所有动作词。

示例泛词：

```text
最新
突发
热搜
官方
网友
视频
现场
如何看待
怎么回事
一男子
一女子
多人
多地
```

例如“回应”“通报”不适合作为 `keywords`，但适合进入 `action_terms`。

### 5. 字段抽取

`keywords`：

- 输出 3 到 8 个。
- 保持原文顺序。
- 优先保留 hashtag 核心词、专名短语、高信息量 n-gram。

`entities`：

- 使用地名、机构、品牌、平台、引号/书名号等弱规则提取。
- 无法确定时允许为空数组，不要编造。

`action_terms`：

维护动作词表，例如：

```text
回应
通报
发布
曝光
举报
调查
处罚
起诉
判决
道歉
辟谣
下架
召回
暂停
恢复
涉嫌
发生
引发
确认
否认
约谈
立案
抓获
救援
```

`event_type`：

第一版使用浅规则分类：

```text
policy_notice
public_safety
legal_case
consumer_rights
education
health
public_issue
```

无法判断时为 `null`。

`event_time_hint`：

- 优先使用 `SourceSignal.published_at`。
- 不要用 `fetched_at` 伪造事件发生时间。

### 6. 匹配文本

`event_text_for_match` 使用确定性拼接：

```text
title
keywords
entities
action_terms
event_type
event_time_hint date
```

该字段供 Day 39 的规则匹配、BM25 / n-gram 和 embedding rerank 使用。

### 7. 置信度

第一版使用规则评分：

```text
base = 0.35
+0.20 title 清晰且长度足够
+0.15 entities 非空
+0.15 action_terms 非空
+0.10 event_time_hint 非空
+0.10 source_status = use
-0.15 low_related_search_result / audit-only 类标记
-0.10 标题过短或泛词化
```

最终值裁剪到：

```text
0.0 <= confidence <= 1.0
```

`confidence` 只表示抽取质量，不表示公众热度、事实真伪保证或事件合并置信度。

### 8. 弱信号

设置 `is_weak_signal = true` 的典型条件：

- 标题 / 话题名过短。
- 只有 hashtag 或泛词话题。
- 缺少实体、动作、对象、时间窗口中的至少两个关键约束。
- `signal_role = topic_discovery_signal` 且没有明确实体或动作。

示例原因：

```text
short_or_generic_topic_without_enough_event_constraints
topic_discovery_signal_without_event_constraints
video_title_may_not_represent_event_fact
```

注意：对象约束只作为抽取质量判断或 Day 39 派生特征，不作为 MVP `EventSignal.object_terms` 字段。

## LLM fallback / refinement

### 触发条件

LLM 不做默认主路径。开关语义统一为三层：全局配置 `EVENT_EXTRACTION_USE_LLM=false` 表示默认不启用 LLM；工具输入 `use_llm = true` 只表示本轮允许使用 LLM；实际是否调用由 `should_use_llm` 按预算、阈值和信号价值逐条判断。

只有全局配置允许、`use_llm = true`，且满足以下条件之一时，才允许 `should_use_llm = true`：

- 规则抽取 `confidence < 0.65`。
- `entities` 为空。
- `action_terms` 为空。
- `is_weak_signal = true`。
- 标题过短、只有 hashtag、只有 RSSHub 话题名。
- 该条为平台内 TopN 或高价值来源。

TopN / 高价值来源只用于控制 LLM 调用预算。LLM 用于话题归纳、实体 / 动作词优化和弱信号判断，不负责热度评分。

### LLM 允许补强的字段

LLM 可以建议：

```text
keywords
entities
event_type
action_terms
is_weak_signal
weak_signal_reason
confidence_delta
normalized_topic
```

LLM 不得生成或覆盖：

```text
event_signal_id
source_signal_ids
source_statuses
source_roles
platforms
source_signal_count
audit_only
source traceability
```

### LLM 输出 schema

LLM 不直接输出完整 `EventSignal`，而是输出受控 refinement：

```json
{
  "normalized_topic": "string|null",
  "keywords": ["string"],
  "entities": ["string"],
  "event_type": "string|null",
  "action_terms": ["string"],
  "is_weak_signal": false,
  "weak_signal_reason": "string|null",
  "confidence_delta": 0.0
}
```

代码负责把规则结果和 LLM refinement 合并成正式 `EventSignal`。

### Prompt 输入

LLM 输入应短而结构化：

```json
{
  "title": "string",
  "platform": "weibo|zhihu|...",
  "signal_role": "topic_discovery_signal|attention_signal|...",
  "published_at": "datetime|null",
  "rule_keywords": ["string"],
  "rule_entities": ["string"],
  "rule_action_terms": ["string"],
  "rule_confidence": 0.52,
  "quality_flags": ["string"]
}
```

不要把全文或大批原始 payload 直接交给 LLM。需要摘要时，先限制在 300 到 500 字。

## DeepSeek API 接入边界

DeepSeek 可作为第一版 LLM provider，因为其 API 文档提供 OpenAI-compatible 接入方式。项目中应通过 provider adapter 隔离实现。

配置建议：

```env
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=...
DEEPSEEK_BASE_URL=https://api.deepseek.com
DEEPSEEK_MODEL=deepseek-chat
EVENT_EXTRACTION_USE_LLM=false
EVENT_EXTRACTION_LLM_CONFIDENCE_THRESHOLD=0.65
```

实现建议：

```text
RuleEventExtractor
LLMEventExtractionRefiner
DeepSeekEventExtractionClient
EventSignalAssembler
```

价格和模型可用性不固化到代码或文档正文中，按 DeepSeek 官方文档和控制台为准：

- https://api-docs.deepseek.com/
- https://api-docs.deepseek.com/quick_start/pricing

## Agent 编排

推荐编排：

```text
extract_event_signals
  -> rule_extract_signal
  -> assess_extraction_quality
  -> if should_use_llm:
       llm_refine_signal
       validate_llm_refinement
       merge_rule_and_llm_result
     else:
       use_rule_result
  -> validate_event_signal
  -> output EventSignal
```

LLM 是规则抽取后的可选 refinement / fallback，不是主抽取链路。

规则结果始终保留。LLM 调用失败、超时或 validation 失败时：

- 不阻断整轮抽取。
- 使用规则结果。
- 写入 `quality_flags` 和 `errors`。

## 输出状态

```text
skipped
  source_signals 为空

succeeded
  所有可处理输入都成功生成 EventSignal

partial
  部分输入 audit-only、LLM 失败或 schema validation 失败，但至少生成一个 EventSignal

failed
  非空输入下没有生成任何 EventSignal，且不是全部 audit-only
```

## 审计与观测

每次 LLM refinement 应记录：

```text
llm_used
llm_provider
llm_model
prompt_version
token_usage
latency_ms
validation_error
fallback_reason
```

LLM 审计结构化保存，但不进入 `EventSignal`。MVP 默认写入 Tool Runtime 日志；如果后续需要进入工具输出，再显式扩展 `ExtractEventSignalsOutput.llm_audit` schema。

工具级输出记录：

```text
audit_only_source_signal_ids
quality_flags
errors
```

## Day 38 最小实现范围

第一版应完成：

- `extract_event_signals` 工具从占位实现切换为真实规则抽取。
- 输入为 `SourceSignal[]`。
- 跳过 audit-only 信号。
- 生成稳定 `event_signal_id`，推荐 `event_signal_id = hash(run_id + source_signal_id)`；它只是本轮抽取信号 ID，不是稳定事件 ID。跨轮次追踪同一事件依赖 Day 39 匹配 / 合并阶段生成或复用的 `event_id`。
- 抽取 `keywords`、`entities`、`action_terms`、`event_type`、`event_time_hint`。
- 生成 `event_text_for_match`。
- 计算 `confidence`。
- 标记弱信号。
- 输出 strict `EventSignal`。
- 预留 DeepSeek LLM refinement adapter，并按“全局配置开关 + 工具输入允许 + 单条 `should_use_llm` 决策”的统一开关语义启用。
- 增加测试：正常抽取、audit-only 跳过、短话题弱信号、LLM 失败回退规则结果、legacy `items` 输入拒绝、空输入返回 `skipped`、`event_signal_id` 稳定生成。

第一版不要求：

- 全量 LLM 抽取。
- embedding 生成。
- 事件合并。
- 最终热点排序。
- `object_terms`、`location_hints`、`topic_terms` 入正式 schema。
- `evidence_strength` 入正式 schema。
