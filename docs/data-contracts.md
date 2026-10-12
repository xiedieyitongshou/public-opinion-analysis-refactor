# 结构化数据契约

[返回项目首页](../README.md) · [工作流](architecture.md) · [工具参考](tool-reference.md)

文档负责解释数据语义，**实际字段、枚举、默认值和校验以 Pydantic Schema 与 SQLAlchemy 模型为准**。这里用代码导航替代早期整页 JSON 草案，避免文档和接口维护两套不同定义。

## 数据流与层级

```text
FetchSourceItemsOutput
  ├─ 每源观测：状态、时间、完整性、采样口径
  └─ 条目 → NormalizedItem → Item / SourceCitation → SourceSignal
                                                     ↓
                                                EventSignal
                                                     ↓
                                  EventResolution → 稳定 Event
                                                     ↓
                             PlatformHeatScore / PlatformHeatObservation
                                      ↓                         ↓
                         HotspotClassificationAssembly    EventHeatAnalysis
                                      └───────────┬─────────────┘
                                             EventCard
                                                ↓
                                  DailyBriefing.sections[]
                                                ↓
                                 DailyReport 固定版本与邮件回执
```

运行身份、来源身份、业务事件身份和展示版本身份各有用途，不能互相替换。父话题关系是事件归属约束，不只是日志标签。

## Schema 导航

| 文件 | 主要契约 |
| --- | --- |
| [`schemas/collectors.py`](../backend/app/schemas/collectors.py) | 采集配置、每源结果、`FetchSourceItemsInput/Output`、归一化工具输入输出 |
| [`schemas/normalized.py`](../backend/app/schemas/normalized.py) | `NormalizedItem`、来源状态／路径／角色和字段映射 |
| [`schemas/signals.py`](../backend/app/schemas/signals.py) | `SourceSignal`、`SearchEnrichmentRelation`、`EventSignal`、抽取与溯源校验 |
| [`schemas/matching.py`](../backend/app/schemas/matching.py) | 匹配配置、`MatchFeatures`、候选、`EventResolution` 和归并输入输出 |
| [`schemas/analysis.py`](../backend/app/schemas/analysis.py) | 九步分析、来源适配与分类 Tool 的输入输出 |
| [`schemas/platform_heat.py`](../backend/app/schemas/platform_heat.py) | 单平台评分输入、子分、聚合结果、评分 Tool |
| [`schemas/platform_trend.py`](../backend/app/schemas/platform_trend.py) | 真实观测、趋势配置、`PlatformTrendResult`、`EventHeatAnalysis` |
| [`schemas/official_support.py`](../backend/app/schemas/official_support.py) | 官媒引用、支持配置、详情与结果 |
| [`schemas/official_paths.py`](../backend/app/schemas/official_paths.py) | 官媒 query、议程排序和路径结果 |
| [`schemas/classification.py`](../backend/app/schemas/classification.py) | 分类输入、字典序排序键、分类结果与组装 |
| [`schemas/display.py`](../backend/app/schemas/display.py) | `SourceCitation`、`EvidenceSummary`、`EventCard`、`BriefingSection`、`DailyBriefing`；Mode B 预留对象 |
| [`schemas/briefing.py`](../backend/app/schemas/briefing.py) | 生成、质量检查、保存日报 Tool 的输入输出 |
| [`schemas/human_review.py`](../backend/app/schemas/human_review.py) | 复核任务、人工决定与结果 |
| [`schemas/guardrails.py`](../backend/app/schemas/guardrails.py) | 规则输入、逐条检查、汇总结果与违规记录 |
| [`evaluation/contracts.py`](../backend/app/evaluation/contracts.py) | 评估用例、运行输入和输出 |

## 身份与时间

| 字段 | 约定 |
| --- | --- |
| `run_id` | 一轮分析；同轮重试不重新采集 |
| `observation_id`、`observed_at` | 外部来源真实观测；不能用评分或审核时间替代 |
| `Item.id`、`source_citation.item_id` | 数据库条目与引用，追踪采集证据 |
| `source_signal_id`、`event_signal_id` | 来源／抽取信号身份，供过程追溯 |
| `event_fingerprint`、`Event.event_id` | 前者辅助匹配，后者作为稳定业务事件身份 |
| `window_start`、`window_end` | 同轮分析与日报固定的 UTC 24 小时窗口，开左闭右 |
| `DailyReport.id` | 已保存的日报版本，发送按此版本绑定 |

社区按观测时间进入关注窗口，官媒按可靠发布时间进入热点窗口。`fetched_at` 不替代缺失的官媒 `published_at`；`calculated_at` 不形成新上榜观测。

## 状态不能混用

| 状态组 | 用途 |
| --- | --- |
| 来源 `source_status` | 选型／质量层级，例如 `use`、`fallback` |
| 采集与业务 `status` | 本轮 `succeeded/partial/failed/skipped` |
| `score_status` | 平台分 `ok/partial/unknown` |
| `current_topn_present` | 当前在榜 true、明确离榜 false、未知 null |
| `trend_status` | rising、stable、cooling、unknown |
| `official_support_status` | supported、weak_supported、not_found、not_checked |
| `confidence_level` | 特定结果的证据／判断置信度，不是事实真伪 |

结果有分数不表示来源完整，成功执行 Tool 不表示业务全部成功。缺失值保留 `null` 和质量原因；搜索 `audit_only` 不能贡献分类。

## 数据库模型导航

| 文件 | 保存对象 |
| --- | --- |
| [`models/business.py`](../backend/app/models/business.py) | `Source`、`Item`、`Event`、`PlatformScore`、`EventSnapshot`、`DailyReport` |
| [`models/agent_runtime.py`](../backend/app/models/agent_runtime.py) | 任务、Tool 调用、人工复核、Guardrail、采集验证与评估记录 |
| [`models/briefing.py`](../backend/app/models/briefing.py) | 分析轮次、报告修订、请求与额度、租约、收件人、一次性导入标记、每日任务、固定投递名单与回执 |

分类写入 `Event.event_detail_json.classification_detail`，趋势写入 `platform_heat_analysis`，原始观测保存在 `EventSnapshot.metrics_json.platform_observations`。固定日报另存快照，后续分析不回写已保存内容。

收件人用数据库名单；旧 `EMAIL_RECIPIENTS` 仅首次初始化导入。`DailyEmailAudience` 固定当日目标，`EmailDelivery` 以版本和地址记录发送结果。删除联系人保留历史回执。

## 获取真实接口定义

工具模型可调用 `model_json_schema()`；`ToolDefinition.input_schema/output_schema` 直接由注册模型生成。HTTP 接口通过启动后的 FastAPI `/docs` 查看。邮箱写入模型等局部 HTTP Schema 直接定义在相应 API 文件中。

新增字段时先修改 Schema／模型与业务实现，再更新对应主题文档和验证。没有投入生产的草案名称不作为已经存在的 API；图片渲染、Mode B 和 LLM refinement 的状态见[工具参考](tool-reference.md)与[抽取说明](event-extraction.md)。
