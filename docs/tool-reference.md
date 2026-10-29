# 工具与代码入口

[返回项目首页](../README.md) · [工作流](architecture.md) · [数据契约](data-contracts.md)

默认注册表位于 [`tools/default_tools.py`](../backend/app/tools/default_tools.py)，共 16 个 Tool。工具内的 Collector、matcher、BM25、embedding、rerank 等属于业务实现，不全部是独立注册工具。

## 实际注册目录

| Tool | 输入 → 输出 Schema | 运行位置与副作用 |
| --- | --- | --- |
| `fetch_source_items` | `FetchSourceItemsInput` → `FetchSourceItemsOutput` | 分析第 1 步；外部采集、来源状态和日志 |
| `normalize_raw_items` | `NormalizeRawItemsInput` → `NormalizeRawItemsOutput` | 第 2 步；确定性清洗 |
| `prepare_source_signals` | `PrepareSourceSignalsInput` → `PrepareSourceSignalsOutput` | 第 3 步；条目持久化与引用 |
| `extract_event_signals` | `ExtractEventSignalsInput` → `ExtractEventSignalsOutput` | 第 4 步；原规则或独立 LLM 抽取，见[模式说明](llm-reasoning.md) |
| `match_and_resolve_events` | `MatchAndResolveEventsInput` → `MatchAndResolveEventsOutput` | 第 5 步；事件和条目关联落库 |
| `create_human_review_task` | `CreateHumanReviewTaskInput` → `CreateHumanReviewTaskOutput` | 第 6 步；复核入队，无候选则跳过 |
| `calculate_event_scores` | `CalculateEventScoresInput` → `CalculateEventScoresOutput` | 第 7 步；平台分与真实观测写入 |
| `classify_events` | `ClassifyEventsInput` → `ClassifyEventsOutput` | 第 8 步；官媒限额补查、分类持久化 |
| `analyze_event_heat` | `AnalyzeEventHeatInput` → `AnalyzeEventHeatOutput` | 第 9 步；趋势持久化，主链路复用已有观测 |
| `generate_daily_briefing` | `GenerateBriefingInput` → `GenerateBriefingOutput` | 日报第 1 步；组装已完成分析的结果 |
| `review_briefing_quality` | `ReviewBriefingInput` → `ReviewBriefingOutput` | 日报第 2 步；规则质量检查 |
| `save_daily_report` | `SaveBriefingInput` → `SaveBriefingOutput` | 日报第 3 步；固定版本落库 |
| `run_briefing_guardrails` | `RunBriefingGuardrailsInput` → `RunBriefingGuardrailsOutput` | 独立规则检查；日报质量服务也直接复用规则注册表 |
| `run_evaluation_suite` | `RunEvaluationInput` → `RunEvaluationOutput` | 独立子进程离线评估，可保存评估摘要；不加入每轮采集 |
| `render_briefing_image` | `PlaceholderInput` → `PlaceholderOutput` | **占位**，返回 `not_implemented` |
| `search_existing_evidence` | `SearchExistingEvidenceInput` → `SearchExistingEvidenceOutput` | **Mode B 占位**，返回 `not_implemented` |

邮件发送由 API／定时服务调用，不在上述九步分析 Tool 中；收件管理也是独立 HTTP 接口。

## 注册、校验与状态

[`ToolDefinition`](../backend/app/tools/runtime.py)包含名称、描述、Pydantic 输入／输出模型、handler、副作用与重试声明。`input_schema`／`output_schema` 从模型生成 JSON Schema。

`ToolRegistry.call()` 校验参数、执行 handler、校验输出并记录 `AgentToolCall`。上下文提供任务 ID、trace、actor 和数据库 Session。当前外层 `ToolResult` 包含 `tool_name`、`status`、`output`、`error_message`、`attempts` 和调用 ID；不应使用早期设计中未实际接入的通用 `ToolResultMeta` 替代它。

外层 `status=succeeded` 表示调用与 Schema 校验完成；业务输出中的 `partial/failed/skipped` 由 Runner 进一步解释。占位工具返回合法 `not_implemented` 也不等于业务已实现。

[`AgentTaskRunner`](../backend/app/agents/runner.py)按 Plan 的依赖推进任务，解析显式输出绑定。业务失败是否继续由步骤设置控制，异常和依赖失败会阻断后续任务；复核采用异步队列，不要求每轮等待人工。

## 副作用与重试

读取、计算和写入应分清：采集可能调用付费服务，事件归并会更新关联，评分会写观测，保存日报会产生固定版本。注册的 `retryable/max_retries` 只定义工具异常时的重试，不替代来源预算、任务超时或邮件去重。

同 `run_id` 回放复用首次采集，重算不制造新观测；业务对象按稳定身份更新。邮件另以“版本＋收件人”去重，SMTP 未知结果不自动重试。接口语义分别见[工作流](architecture.md)和[MVP 使用指南](user-guide.md)。

## HTTP 与管理入口

| 代码 | 职责 |
| --- | --- |
| [`api/briefing.py`](../backend/app/api/briefing.py) | 任务、简报、确认、导出、发送、用量和投递查询 |
| [`api/email_recipients.py`](../backend/app/api/email_recipients.py) | 收件人增删改、启停与最近回执 |
| [`api/ops.py`](../backend/app/api/ops.py) | Agent／Tool／Guardrail 运维与人工复核 |
| [`api/auth.py`](../backend/app/api/auth.py) | 管理员 Bearer 令牌、会话与同源写入保护 |
| [`api/health.py`](../backend/app/api/health.py) | 健康与就绪状态 |
| [`web/`](../backend/app/web) | 同后端部署的原生管理界面 |

启动后可在 `/docs` 查看实际 FastAPI 接口契约，管理数据接口仍要求管理员身份。运维健康检查不能替代对上游来源和业务结果的验收。
