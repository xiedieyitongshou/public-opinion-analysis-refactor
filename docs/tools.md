# Tool Calling 设计

## 文档目标

本文定义模式 A：每日热点简报工作流中的工具目录、阶段映射、职责边界、副作用、重试策略和失败降级策略。

`docs/structured-outputs.md` 是 schema 的唯一来源。本文不重复维护详细 JSON 字段，只引用 `docs/structured-outputs.md` 中定义的 input / output schema 名称，避免工具文档和 schema 文档产生漂移。

## 工具定位

工具服务于 `docs/workflows.md` 中定义的固定工作流。

工具负责：

- 执行一个明确的业务动作。
- 接收结构化 input schema。
- 返回结构化 output schema。
- 返回统一的 `ToolResultMeta`。
- 记录可观测的执行结果。

工具不负责：

- 控制全局状态流转。
- 自行决定是否进入下一阶段。
- 自行决定是否公开发布。
- 绕过 schema validation。
- 绕过 Guardrails。
- 替代人工发布确认。

状态流转由 Orchestrator / 任务状态机根据工具结果、schema validation、Guardrails 结果和人工发布确认结果完成。

## 双链路策略

模式 A 采用双链路：

- 自动采集分析链路：每 2-3 小时运行一次，自动完成采集、清洗、事件处理、评分、走势记录和草稿快照生成。
- 人工发布确认链路：正式发布前集中处理待复核项和草稿，不阻断自动采集分析链路。

工具返回的 `review_required = true` 只表示写入异步复核队列，不表示阻塞当前自动链路。

## 通用工具结果

所有工具必须返回 `ToolResultMeta`，该结构定义在 `docs/structured-outputs.md`。

关键字段语义：

- `success`：工具是否完成业务动作。
- `retryable`：失败是否允许自动重试。
- `review_required`：是否写入异步复核队列。
- `blocks_auto_analysis`：是否阻断当前自动采集分析 run。
- `blocks_publish`：是否阻断正式发布。
- `warnings`：非阻断问题。
- `errors`：失败原因。
- `metrics`：工具执行指标。

状态机使用这些字段进行状态流转：

```text
success = true
-> 当前 task 标记为 succeeded

success = false and retryable = true
-> 当前 task 标记为 retrying

success = false and retryable = false
-> 当前 task 标记为 failed

review_required = true
-> 写入异步复核队列，自动采集分析链路继续

blocks_publish = true
-> 草稿标记为 draft_blocked_for_publish，不自动公开发布

blocks_auto_analysis = true
-> 当前自动分析 run 标记为 failed 或 blocked
```

## 工具清单

| 阶段 | 工具 | Input schema | Output schema | 是否必需 | 模式 B 复用 |
|---|---|---|---|---|---|
| 采集数据 | `fetch_source_items` | `FetchSourceItemsInput` | `FetchSourceItemsOutput` | 是 | 是 |
| 标准化内容 | `normalize_raw_items` | `NormalizeRawItemsInput` | `NormalizeRawItemsOutput` | 是 | 是 |
| 事件信号抽取 | `extract_event_signals` | `ExtractEventSignalsInput` | `ExtractEventSignalsOutput` | 是 | 是 |
| 事件合并 | `match_and_resolve_events` | `MatchAndResolveEventsInput` | `MatchAndResolveEventsOutput` | 是 | 是 |
| 热度评分 | `calculate_event_scores` | `CalculateEventScoresInput` | `CalculateEventScoresOutput` | 是 | 是 |
| 简报生成 | `generate_daily_briefing` | `GenerateDailyBriefingInput` | `GenerateDailyBriefingOutput` | 是 | 否 |
| 质量检查 | `review_briefing_quality` | `ReviewBriefingQualityInput` | `ReviewBriefingQualityOutput` | 是 | 部分复用 |
| 边界检查 | `run_briefing_guardrails` | `RunBriefingGuardrailsInput` | `RunBriefingGuardrailsOutput` | 是 | 部分复用 |
| 异步复核 | `create_human_review_task` | `CreateHumanReviewTaskInput` | `CreateHumanReviewTaskOutput` | 是 | 是 |
| 保存草稿/日报 | `save_daily_report` | `SaveDailyReportInput` | `SaveDailyReportOutput` | 是 | 否 |
| 图像渲染 | `render_briefing_image` | `RenderBriefingImageInput` | `RenderBriefingImageOutput` | 是 | 否 |
| 运行评估 | `run_evaluation_suite` | `RunEvaluationSuiteInput` | `RunEvaluationSuiteOutput` | 是 | 是 |
| 指定事件检索 | `search_existing_evidence` | `SearchExistingEvidenceInput` | `SearchExistingEvidenceOutput` | 占位 | 是 |

## 工具定义

### fetch_source_items

作用：

- 从指定数据源采集原始新闻条目或社区热榜条目。
- 只负责采集，不做业务判断。

对应阶段：

- 阶段 3：采集数据。

Schema：

- Input：`FetchSourceItemsInput`
- Output：`FetchSourceItemsOutput`

副作用：

- 可写入原始采集缓存。
- 必须写入 tool call 日志。

重试策略：

- 允许重试。
- 单个数据源失败不阻断整条工作流。

失败降级：

- 失败数据源标记为 unavailable。
- 继续处理其他数据源。
- 全部核心数据源失败时，设置 `blocks_auto_analysis = true`。

### normalize_raw_items

作用：

- 将 `RawItem` 标准化为 `NormalizedItem`。
- 标记字段缺失和异常。
- 生成 `content_hash` 用于去重。

对应阶段：

- 阶段 4：标准化内容。

Schema：

- Input：`NormalizeRawItemsInput`
- Output：`NormalizeRawItemsOutput`

副作用：

- 可写入 `items` 表。
- 可写入异常条目记录。

重试策略：

- 通常不重试。
- 字段解析规则错误应通过代码修复。

失败降级：

- 缺少 `title` 或 `url` 的条目丢弃。
- 缺少发布时间、摘要或指标的条目保留，并写入 `quality_flags`。

### extract_event_signals

作用：

- 从 `NormalizedItem` 中抽取 `EventSignal`。
- 识别关键词、实体、事件类型、动作词和时间线索。

对应阶段：

- 阶段 5：事件信号抽取。

Schema：

- Input：`ExtractEventSignalsInput`
- Output：`ExtractEventSignalsOutput`

副作用：

- 可写入事件信号中间表或日志。

重试策略：

- LLM 调用失败允许有限重试。
- schema validation 失败允许一次修复重试。

失败降级：

- LLM 不可用时使用规则抽取。
- B站标题默认标记为弱信号，除非包含明确实体、动作和事件对象。
- 低置信度信号不阻断自动链路。

### match_and_resolve_events

作用：

- 将 `EventSignal` 匹配到已有 `Event`，或创建新事件。
- 输出 `EventResolution`。
- 中低置信度结果写入异步复核队列，不阻断自动链路。

对应阶段：

- 阶段 6：事件合并。

Schema：

- Input：`MatchAndResolveEventsInput`
- Output：`MatchAndResolveEventsOutput`

副作用：

- 可创建或更新 `events`。
- 可创建候选合并关系。
- 可写入异步复核队列。

重试策略：

- 不建议自动重试。
- 判断不确定时不自动合并。

失败降级：

- 核心实体冲突时禁止自动合并。
- 时间窗口冲突时禁止自动合并。
- 中置信度不自动合并，自动链路继续。
- 事件写入完全失败时，设置 `blocks_auto_analysis = true`。

### calculate_event_scores

作用：

- 计算事件的平台热度、综合热度和走势状态。
- 保存评分明细和 `event_snapshots`。

对应阶段：

- 阶段 7：热度评分与走势判断。

Schema：

- Input：`CalculateEventScoresInput`
- Output：`CalculateEventScoresOutput`

副作用：

- 写入 `platform_scores`。
- 写入 `event_snapshots`。

重试策略：

- 通常不重试。
- 输入数据缺失时降级计算。

失败降级：

- 缺失指标标记为 `unknown`。
- B站视频传播信号默认降权。
- 历史快照不足时走势标记为 `new` 或 `unknown`。

### generate_daily_briefing

作用：

- 基于评分后的事件生成结构化每日简报草稿。
- 只生成草稿，不负责正式发布。

对应阶段：

- 阶段 8：生成结构化简报。

Schema：

- Input：`GenerateDailyBriefingInput`
- Output：`GenerateDailyBriefingOutput`

副作用：

- 无直接发布副作用。
- 可保存草稿中间结果。

重试策略：

- LLM 调用失败允许有限重试。
- schema validation 失败允许一次修复重试。

失败降级：

- 栏目数据不足时允许省略栏目并记录原因。
- 无来源事件不能进入正式发布版本。

### review_briefing_quality

作用：

- 检查简报草稿质量。
- 发现重复、来源缺失、事实过度断言、栏目失衡和弱信号误用。

对应阶段：

- 阶段 9：质量检查。

Schema：

- Input：`ReviewBriefingQualityInput`
- Output：`ReviewBriefingQualityOutput`

副作用：

- 可写入质量检查日志。

重试策略：

- LLM 调用失败允许有限重试。

失败降级：

- 检查失败不阻断自动草稿保存。
- 检查失败会设置 `blocks_publish = true` 或使草稿进入 `draft_needs_review`。

### run_briefing_guardrails

作用：

- 对简报草稿执行发布前边界检查。
- 输出草稿发布建议和风险标记。

对应阶段：

- 阶段 10：Guardrails 决策。

Schema：

- Input：`RunBriefingGuardrailsInput`
- Output：`RunBriefingGuardrailsOutput`

副作用：

- 写入 `guardrail_violations`。

重试策略：

- 不建议自动重试。

失败降级：

- Guardrails 运行失败不阻断自动采集分析链路。
- 草稿必须标记为 `draft_needs_review` 或 `draft_blocked_for_publish`。

### create_human_review_task

作用：

- 为候选合并、弱信号、高风险表述或发布决策创建异步复核任务。
- 复核任务不阻断每 2-3 小时一次的自动采集分析链路。

对应阶段：

- 阶段 6、9、10。

Schema：

- Input：`CreateHumanReviewTaskInput`
- Output：`CreateHumanReviewTaskOutput`

副作用：

- 写入 `human_review_tasks` 或等价复核队列表。

重试策略：

- 写入失败可重试。

失败降级：

- 复核任务创建失败时记录 warning。
- 只有涉及正式发布阻断的问题才设置 `blocks_publish = true`。

### save_daily_report

作用：

- 保存自动草稿快照、待复核版本或已发布版本。

对应阶段：

- 阶段 11：保存草稿快照。
- 阶段 12：人工发布确认。

Schema：

- Input：`SaveDailyReportInput`
- Output：`SaveDailyReportOutput`

副作用：

- 写入 `daily_reports`。
- 可能更新发布状态。

重试策略：

- 数据库写入失败允许重试。

失败降级：

- 保存失败时 run 标记为 failed。
- 草稿保存成功后，自动采集分析链路结束，不等待人工确认。

### render_briefing_image

作用：

- 将已发布的结构化简报渲染为一图流长图。

对应阶段：

- 阶段 13：渲染展示。

Schema：

- Input：`RenderBriefingImageInput`
- Output：`RenderBriefingImageOutput`

副作用：

- 写入图片文件或静态资源记录。

重试策略：

- 渲染失败允许重试。

失败降级：

- 长图失败不影响 Web 首页展示。

### run_evaluation_suite

作用：

- 对本次工作流运行质量进行评估。

对应阶段：

- 阶段 14：运行评估。

Schema：

- Input：`RunEvaluationSuiteInput`
- Output：`RunEvaluationSuiteOutput`

副作用：

- 写入 `evaluation_runs`。

重试策略：

- 可重试。

失败降级：

- Evaluation 失败不阻断简报发布。
- 失败必须记录到运行日志。

### search_existing_evidence

作用：

- 为后续模式 B 预留。
- 在已有 `items`、`events`、`platform_scores` 和 `event_snapshots` 中检索事件证据。

对应阶段：

- v0.3 只注册占位，不进入模式 A 主链路。

Schema：

- Input：`SearchExistingEvidenceInput`
- Output：`SearchExistingEvidenceOutput`

副作用：

- v0.3 无副作用。
- 后续可记录查询日志。

重试策略：

- v0.3 不适用。

失败降级：

- 返回未实现或证据不足说明，不编造来源。

## 工具重试与副作用矩阵

| 工具 | 可重试 | 有副作用 | 失败是否阻断自动分析 | 是否可能阻断正式发布 |
|---|---|---|---|---|
| `fetch_source_items` | 是 | 可选 | 仅全部核心源失败时 | 否 |
| `normalize_raw_items` | 否 | 是 | 仅全部标准化失败时 | 否 |
| `extract_event_signals` | 是 | 可选 | 仅全部抽取失败时 | 否 |
| `match_and_resolve_events` | 否 | 是 | 仅事件写入完全失败时 | 可能 |
| `calculate_event_scores` | 否 | 是 | 是 | 可能 |
| `generate_daily_briefing` | 是 | 可选 | 是 | 是 |
| `review_briefing_quality` | 是 | 可选 | 否 | 是 |
| `run_briefing_guardrails` | 否 | 是 | 否 | 是 |
| `create_human_review_task` | 是 | 是 | 否 | 可能 |
| `save_daily_report` | 是 | 是 | 是 | 是 |
| `render_briefing_image` | 是 | 是 | 否 | 否 |
| `run_evaluation_suite` | 是 | 是 | 否 | 否 |
| `search_existing_evidence` | 否 | 否 | 否 | 否 |

## 与 Structured Output 的关系

`docs/structured-outputs.md` 是唯一 schema source of truth。

本文只说明：

- 工具做什么。
- 工具在哪个阶段调用。
- 工具有什么副作用。
- 工具失败如何处理。
- 工具是否可重试。
- 工具是否可被模式 B 复用。

本文不维护详细字段定义。所有 input / output 字段、枚举、状态和校验规则都以 `docs/structured-outputs.md` 为准。

## 第一阶段结论

模式 A 的工具设计应坚持：

- 工具执行业务动作，状态机控制状态流转。
- 采集、标准化、评分、保存、渲染和评估尽量确定性实现。
- LLM 只用于事件抽取、合并解释、简报生成和质量检查等局部任务。
- 所有工具调用必须可观测、可重试策略明确、可记录失败原因。
- 自动采集分析链路不等待人工确认。
- 人工确认作为发布前集中整理和异步复核机制。
- 不确定事件默认不合并、不强行写成事实、不阻断自动链路。
- 模式 B 不在 v0.3 展开，但保留 `search_existing_evidence` 占位工具。
