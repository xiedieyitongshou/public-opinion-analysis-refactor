# 三模块热点分析链路与数据接口

本文对应当前代码中的 `HotspotAnalysisAgent`，不按历史周报推断实现状态。
HTTP 应用入口仍为 `backend/app/main.py`；一轮分析的命令行入口为
`python -m app.analysis_cli`，核心编排位于 `backend/app/agents/analysis.py`。

## 执行顺序

```text
模块一：数据采集
  fetch_source_items
    ↓ FetchSourceItemsOutput：本轮条目 + 每源完整观测元数据
模块二：归一化与事件构建
  normalize_raw_items
  prepare_source_signals       新增的接口适配 tool
  extract_event_signals
  match_and_resolve_events     persist=true，召回、归并决策、事件与条目关联落库
  create_human_review_task     无待复核项时 skipped
    ↓ 稳定事件 ID + 原始条目 hash 到事件 ID 的映射
模块三：热度、分类与趋势
  calculate_event_scores      本轮真实观测模式
  classify_events             新增的官媒支撑与分类组装 tool
  analyze_event_heat          读取已保存观测，分析同一窗口
```

`hotspot_analysis` 是独立 Plan，沿用 `Plan → AgentTaskRunner → ToolRegistry`。
`PlanStep.input_bindings` 明确绑定前序输出，例如
`{"items": "collect.items"}`；引用必须声明在 `depends_on` 中。
实际解析后的输入保存在任务与工具日志中。

原有日报 Plan 和占位工具继续保留；运行当前三模块时使用上述新 Plan。

## 两个交接接口

| 位置 | 当前传递对象 | 接口责任 |
|---|---|---|
| 采集 → 事件构建 | `FetchSourceItemsOutput`、`NormalizeRawItemsOutput.items` | 采集输出同时保留每源原始观测；Normalizer 清洗条目，不吞掉观测上下文 |
| 归一化 → 抽取 | `PrepareSourceSignalsOutput` | 保存/更新 Item，把真实数据库主键写入 `source_citation.item_id`，生成 `SourceSignal` 与 matcher 所需引用 |
| 事件构建 → 分析 | `MatchAndResolveEventsOutput.event_ids`、`event_ids_by_content_hash`，加原始采集输出 | 将采集结果关联到稳定事件，并保留最近 24 小时事件以确认下榜或来源不可用 |
| 评分 → 分类/趋势 | `PlatformScore`、`EventSnapshot.metrics_json.platform_observations` | 评分和观测经同一写入入口保存，具有可回溯的观测 ID 和采集时间 |

数据模型位于 `backend/app/schemas/analysis.py`；主要适配与持久化逻辑位于
`backend/app/services/analysis_interfaces.py`。各阶段已有算法继续复用。

## 身份与时间约定

- `run_id` 标识一次采集轮次，所有来源结果必须属于该轮次。
- `observed_at`、`observation_id` 来自采集结果；重评分不制造新观测。
- `window_end` 未指定时在采集完成后取本轮最后一次来源观测时间；指定时必须带时区，且不能早于本轮观测。分类与趋势均使用 UTC `(window_end - 24h, window_end]`。
- `Item.id` 是数据库主键；`SourceSignal.item_id` 使用其字符串表示，并通过 citation 回溯。Normalizer 的内容 hash 与 Collector 原始 hash 通过 `normalized.collector_content_hash` 对应。
- `Event.id` 用于数据库外键，`Event.event_id` 是跨阶段使用的稳定业务 ID。
- `prepare_source_signals` 接管新链路的 Item 写入，采集阶段不提前保存另一份 Item。原有独立采集入口保存的条目也会按身份复用。
- `match_and_resolve_events(persist=true)` 按条处理，在数据库召回候选，允许后续信号复用同轮刚创建的事件。只有通过规则且无需复核的 create/merge 会关联条目。

同一 `run_id` 重跑时，`HotspotAnalysisAgent` 从任务日志复用首次采集响应，保留新一次执行日志，但不重新请求来源。采集参数和窗口不能改变；新一轮真实采集使用新的 `run_id`。事件、评分与快照按已有身份更新。

## 分析阶段约定

`calculate_event_scores` 接收 `collection` 时调用观测写入服务，保存带
`observation_id/observed_at` 的分数。新 Plan 不调用旧的“只对 Item 静态重评分”路径。
`analyze_event_heat` 随后只读取这些观测，不重复执行评分写入。

知乎真实排名从统一的平台指标 adapter 读取，兼容 Collector 的 `raw_metrics.rank`。
微博 RSS 顺序不作为官方排名；有意跳过置顶时，完整性根据实际采样范围判断。
来源有效配置进入采样签名，历史与当前采样口径不一致时沿用现有 unknown 降级。

`classify_events` 查询窗口内官媒证据池，复用官媒匹配、覆盖统计和分类组装 service。
官媒条目也走统一事件构建链路，独立官媒事件保留官媒议程排序信息。
没有运行官媒来源且没有有效证据池时保留 `not_checked`。本链路没有新增官媒定向网络搜索客户端。

搜索增强沿用已实现 Collector 的目标和相关性约束，可通过
`collection.per_source_params` 提供候选或 query；未提供目标时按原规则跳过。
本次没有增加自动生成知乎搜索候选的额外采集轮次。

## 失败、复核和结果

- Runner 区分业务 `failed/partial/skipped`；占位工具返回 `not_implemented` 时任务为 `blocked`。
- 来源失败若仍有结构化采集结果，继续为已跟踪事件保存失败观测，避免沿用旧热度作为当前热度。工具异常或数据接口校验失败会中止后续依赖步骤。
- 待复核条目保存在 Item 中，但不自动关联候选事件；复核任务包含具体 Item ID 与来源引用。人工确认 create/merge 后才保存关联。
- 无数据可完成流程并返回 `skipped`；某平台失败、历史不足或采样间隔未配置时，结果保留 `partial/unknown`。
- 汇总返回 `HotspotAnalysisOutput`：来源状态、逐工具任务状态、事件 ID、分类、分平台趋势与错误信息。结果尚不是日报或最终首页展示响应。

## 运行与离线验证

在 `backend` 目录使用已安装项目依赖的 Python：

```powershell
python -m app.analysis_cli --sources zhihu_hot_list --limit 5 --interval-minutes 120
```

这会执行真实采集；采样间隔参数只是声明调度间隔，本命令运行一轮。
知乎、微博及官媒仍依赖各自凭据、服务和网络条件。默认使用规则抽取，LLM refinement 适配器仍处于原有预留状态。

已有一轮 `FetchSourceItemsOutput` JSON 时，可在显式指定的独立数据库回放：

```powershell
python -m app.analysis_cli --replay sample-round.json --database sqlite:///./data/replay.db --interval-minutes 120
```

回放文件须包含完整轮次和来源观测信息，使用分析模式标志；不要将不完整历史采样伪装成完整观测。

离线验收使用真实 Collector、Normalizer、抽取、匹配、Guardrails、评分、分类、趋势及 SQLite，仅替换来源响应：

```powershell
python -m pytest tests/test_hotspot_analysis.py -q
```

覆盖两轮排名变化、事件/条目复用、同轮重跑、来源故障、待复核与人工关联、官媒独立事件/社区补证、微博跳过置顶和空数据。测试通过表示接口及运行链路可用，不代表真实来源的持续可用性或分类准确率评测已经完成。

## 2026-09-28 真实数据联调

已通过真实入口完成一轮 5 个主来源的采集与分析，共 24 条，9 个 tool 均执行到；另行验证一次知乎搜索，返回 3 条。
完整记录见 [真实数据联调报告](../reports/live-analysis-validation-2026-09-28.md)。

本轮发现微博 CLI 体验服务到期、人民网与新华网当前 RSS 返回陈旧内容，以及官媒支撑 matcher 的无关内容误匹配。
整体返回 `partial`，不能将工具执行成功等同于业务结果验收通过。陈旧新闻入池与官媒匹配问题尚待修复；单轮数据也不足以验证上升/下降趋势。

同日 19:45（北京时间）单独复测微博 CLI：服务已恢复为 `formal_active`，真实搜索成功，项目客户端保留的 3 条样本均有正文、发布时间和转评赞字段。
该复测只更新 CLI 可用性结论，首次整轮分析的记录和其余待修复问题仍见上述报告。
