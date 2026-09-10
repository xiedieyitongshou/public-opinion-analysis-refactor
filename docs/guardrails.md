# Guardrails 设计

## 文档目标

本文定义模式 A：每日热点简报第一阶段的 Guardrails。

Guardrails 不覆盖每一个工具。第一阶段只在高风险决策点设置边界，避免把普通错误处理、schema validation 和业务规则全部混成 Guardrails。

## 基本原则

- Guardrails 只处理高风险内容和关键发布决策。
- 普通工具失败由重试、降级和日志处理。
- schema 错误由 structured output validation 处理。
- 事件不确定时默认不合并，不阻断自动采集分析链路。
- 人工确认是异步复核和发布前确认机制，不阻断每 2-3 小时一次的自动分析。
- Guardrails 可以阻断正式发布，但不应轻易阻断采集、清洗、评分和草稿生成。

## Guardrails 覆盖范围

第一阶段设置 Guardrails 的阶段：

| 阶段 | 是否需要 Guardrails | 原因 |
|---|---|---|
| 采集数据 | 否 | 只需要错误处理、频率限制和日志 |
| 标准化内容 | 否 | 只需要 schema validation 和字段缺失标记 |
| 事件信号抽取 | 轻量 | 只标记弱信号和低置信度 |
| 事件合并 | 是 | 防止误合并不同事件 |
| 热度评分 | 轻量 | 防止缺失指标被伪造成确定分数 |
| 简报生成 | 是 | 防止无来源、过度断言和低可信内容进入草稿 |
| 质量检查 | 是 | 检查重复、缺来源、弱信号误用 |
| 正式发布 | 是 | 决定草稿是否可发布 |
| 图像渲染 | 否 | 普通渲染失败降级即可 |
| Evaluation | 否 | 评估失败只记录，不阻断发布 |

## 结果等级

Guardrail 结果使用三档：

```text
pass
warn
block
```

处理方式：

- `pass`：允许进入下一阶段。
- `warn`：自动加提示或进入异步复核队列，不阻断自动分析。
- `block`：阻断正式发布；仅在系统级严重错误时阻断自动分析。

发布建议使用：

```text
ready_for_review
needs_review
blocked_for_publish
```

## 规则判定输入与默认阈值

第一阶段规则必须尽量依赖结构化字段，避免只靠自然语言判断。无法自动判定的规则应输出 `warn` 并创建异步复核任务，而不是直接伪造结论。

默认阈值：

```text
auto_merge_confidence_threshold = 0.85
candidate_merge_confidence_threshold = 0.60
low_confidence_threshold = 0.60
source_coverage_min_for_publish = 1
official_source_min_for_high_risk = 1
summary_source_match_min_confidence = 0.70
```

高风险事件类型：

```text
accident
crime
public_health
financial_risk
official_investigation
public_safety
```

常用判定字段：

- `EventResolution.confidence`：判断是否允许自动合并。
- `EventResolution.matched_by` 和 `match_features_json`：判断是否由 BM25 / n-gram 或 embedding 辅助命中，以及硬约束是否通过。
- `EventSignal.is_weak_signal`：判断弱信号是否被当作事实。
- `EventSignal.entities` 和 `Event.entities`：判断核心实体是否冲突。
- `EventSignal.event_time_hint`、`Event.first_seen_at`、`Event.last_seen_at`：判断时间窗口是否冲突。
- `EventCard.source_citations`：判断来源是否可追踪。
- `SourceCitation.source_type` 和 `SourceCitation.platform`：判断是否只有社区来源。
- `Event.event_type`：判断是否属于高风险事件。
- `CriticResult.issues`：承接重复、缺来源、过度断言和栏目失衡等质量检查结果。

## 规则类型

### event_merge

目标：

- 防止不同事件被错误合并。

规则：

| 规则 ID | 条件 | 等级 | 处理 |
|---|---|---|---|
| `event_merge.low_confidence` | `EventResolution.confidence < 0.85` | warn | 不自动合并；`0.60 <= confidence < 0.85` 写入候选复核，`confidence < 0.60` 新建低置信事件或忽略弱信号 |
| `event_merge.entity_conflict` | 核心实体冲突 | block | 禁止自动合并 |
| `event_merge.time_conflict` | 事件时间窗口冲突 | block | 禁止自动合并 |
| `event_merge.embedding_conflict` | embedding 高相似但核心实体或时间冲突 | block | 禁止自动合并，记录 `semantic_false_positive_risk` |
| `event_merge.embedding_only` | 只有 embedding 高相似，缺少实体、动作、对象或时间硬约束 | warn | 不自动合并，写入候选复核 |
| `event_merge.weak_signal_only` | 仅由弱社区信号支持 | warn | 不作为确定事件发布 |
| `event_merge.bilibili_weak_match` | B站视频标题弱匹配已有事件 | warn | 只作为传播信号候选 |

默认策略：

```text
高置信度 -> 自动合并
中置信度 -> 记录候选关系，不合并，不阻断
低置信度 -> 新建低置信度事件或忽略弱信号
实体/时间冲突 -> 禁止合并
embedding-only -> 最多进入中置信度复核
```

### source_quality

目标：

- 确保进入正式简报的内容有可追踪来源。

规则：

| 规则 ID | 条件 | 等级 | 处理 |
|---|---|---|---|
| `source.missing_url` | 事件卡片没有任何来源链接 | block | 阻断该事件进入正式发布 |
| `source.single_community_source` | 只有单一社区来源支撑事实表述 | block | 改为讨论热度或移出正式发布 |
| `source.low_coverage` | 来源覆盖不足但非事实断言 | warn | 添加覆盖不足提示 |
| `source.official_absent` | 高风险事件缺少新闻/官媒来源 | warn | 进入复核；如果同时存在事实断言或未证实指控，由 `factuality.overclaim` 或 `factuality.unverified_accusation` 升级为 block |

### factuality

目标：

- 防止系统把未经证实的讨论写成事实。

规则：

| 规则 ID | 条件 | 等级 | 处理 |
|---|---|---|---|
| `factuality.overclaim` | 将社区讨论写成确定事实 | block | 阻断正式发布 |
| `factuality.unverified_accusation` | 涉及未证实指控且无权威来源 | block | 阻断正式发布 |
| `factuality.missing_uncertainty` | 高风险判断缺少不确定性表达 | warn | 自动加提示或进入复核 |
| `factuality.source_mismatch` | 摘要内容和来源标题明显不匹配 | warn | 进入复核；如果摘要新增来源不存在的关键事实，升级为 `factuality.overclaim` |

### briefing_quality

目标：

- 控制每日简报可读性和结构质量。

规则：

| 规则 ID | 条件 | 等级 | 处理 |
|---|---|---|---|
| `briefing.duplicate_event` | 简报中出现重复事件 | warn | 自动去重或进入复核 |
| `briefing.section_imbalance` | 栏目严重失衡 | warn | 调整栏目或记录提示 |
| `briefing.no_source_citation` | 事件卡片缺少来源引用 | block | 阻断该事件进入正式发布 |
| `briefing.empty_report` | 草稿没有有效事件 | block | 阻断正式发布 |

### publish_gate

目标：

- 控制草稿能否成为正式公开版本。

规则：

| 规则 ID | 条件 | 等级 | 处理 |
|---|---|---|---|
| `publish.guardrail_failed` | Guardrails 未成功运行 | block | 草稿标记为 `draft_blocked_for_publish` |
| `publish.critical_issue` | 存在 block 级问题 | block | 阻断正式发布 |
| `publish.needs_review` | 存在关键 warn | warn | 草稿标记为 `draft_needs_review` |
| `publish.ready` | 无 block 且无关键 warn | pass | 草稿标记为 `draft_ready_for_review` |

## 不属于 Guardrails 的内容

以下问题不作为 Guardrails 处理：

- 单个数据源请求失败。
- 普通网络超时。
- 字段缺失。
- 图片渲染失败。
- Evaluation 运行失败。
- 数据库写入异常。

这些问题应由工具重试、降级、日志、状态机或运维告警处理。

## 与异步复核的关系

Guardrails 触发 `warn` 时，系统可以：

- 自动添加风险提示。
- 降低事件展示优先级。
- 将事件移出正式发布候选。
- 创建异步复核任务。

Guardrails 触发 `block` 时，系统可以：

- 阻断单个事件进入正式发布。
- 将草稿标记为 `draft_blocked_for_publish`。
- 要求人工发布确认。

Guardrails 不应因为普通事件合并不确定而阻断每 2-3 小时一次的自动采集分析链路。

## 第一阶段结论

v0.3 的 Guardrails 只覆盖关键风险点：

- 事件合并。
- 来源引用。
- 事实表述。
- 社区弱信号。
- 简报质量。
- 正式发布。

其余工具使用 schema validation、错误处理、日志和 Evaluation 即可。
