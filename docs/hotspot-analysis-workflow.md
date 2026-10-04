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

成功取得合法知乎列表或微博 RSS channel 后，按当前可获取的榜单判断完整性；
有效短榜和空榜可以证明缺席。知乎若有 total 且明显少返回、响应结构异常或条目
缺少身份字段，不能据此确认离榜。`list_complete` 指当前 limit/skip_top 范围，
不表示覆盖平台全部讨论。本轮在榜／离榜可在无采样间隔配置时确认，连续时长和
趋势仍需配置；失败、不可比范围和确实残缺的榜单保留未知。

当前在榜状态与过去 24 小时关注分开：完整榜单中缺席会令 `current_topn_present=false`，
但不删除窗口内已接纳的 A–F 来源证据。最后有效发现证据离开窗口后，事件退出
本轮 `event_ids`、分类和分析结果，历史数据库记录保留。展示应使用本轮结果和窗口，
不能直接把所有 `lifecycle_status=active` 历史事件当作当前榜单。

`classify_events` 查询窗口内官媒证据池，复用官媒匹配、覆盖统计和分类组装 service。
官媒条目也走统一事件构建链路，独立官媒事件保留官媒议程排序信息。
`prepare_source_signals` 在提取前按发布时间过滤官媒条目：仅接纳
`(window_end - 24h, window_end]` 内的报道；过期、无有效发布时间或未来发布时间的
Item 保留审计数据，记录 `normalized.hotspot_freshness` 和 `excluded_count`，不建立
当前热点。分类、官媒支撑和搜索缓存复用同一窗口，旧引用不能恢复当天官媒关注；
官媒议程排序也按该窗口计算。社区热榜使用观测时间，旧问题重新上榜不受发布时间限制。
对未获支持的社区事件，默认生成 query 并补查官媒搜索。搜索候选先落库，再通过相关性判定；不直接增加热度或自动成为引用。全部查询失败为 `not_checked`，部分失败、模型缺失或预算耗尽会保留质量标记并返回 `partial`。范围与实测结果见 [匹配更新说明](matching-upgrade.md)。

搜索增强沿用已实现 Collector 的目标和相关性约束，可通过
`collection.per_source_params` 提供候选或 query；未提供目标时按原规则跳过。
本次没有增加自动生成知乎搜索候选的额外采集轮次。

自动热点分析的归并步骤以 `discovery_mode=automatic` 运行。热榜信号先于搜索增强
信号解析；通过初筛的知乎回答仍须找到本轮同平台、同来源的热榜父话题，并通过
事件匹配及冲突 guardrail，才能关联父事件。父话题不存在、事件不匹配或需复核时，
回答保留为未关联 Item 并生成复核任务，不会自动创建新热点。独立调用归并工具的
`targeted` 模式保留用户指定事件的原有处理方式。

微博 CLI 仍嵌在 RSSHub 热搜条目内，不另建事件信号。每条返回帖子记录与 RSSHub
父话题的 `SearchEnrichmentRelation`；只有相关、没有明确事实冲突且未判定为超过
72 小时的历史内容的帖子参与 `matched_status_count` 与最高互动量计算；最新帖子
时间仅取可解析的时间戳。查询级 `total_number_proxy` 涵盖未返回的命中，无法逐条
核验，因此仅保留在审计字段
`normalized.cli_query_total_number_proxy`，不用于热度评分；逐条判定及通过数量
保存在 `raw_payload.cli_enrichment.samples[].relation` 与
`normalized.cli_relevance_counts`。

## 失败、复核和结果

- Runner 区分业务 `failed/partial/skipped`；占位工具返回 `not_implemented` 时任务为 `blocked`。
- 来源失败若仍有结构化采集结果，继续为已跟踪事件保存失败观测，避免沿用旧热度作为当前热度。工具异常或数据接口校验失败会中止后续依赖步骤。
- 待复核条目保存在 Item 中，但不自动关联候选事件；复核任务包含具体 Item ID 与来源引用。人工确认 create/merge 后才保存关联。
- 无数据可完成流程并返回 `skipped`；某平台失败、历史不足或采样间隔未配置时，结果保留 `partial/unknown`。
- 汇总返回 `HotspotAnalysisOutput`：来源状态、逐工具任务状态、事件 ID、分类、分平台趋势、质量标记与错误信息。结果尚不是日报或最终首页展示响应。

## 运行与离线验证

在 `backend` 目录使用已安装项目依赖的 Python：

```powershell
python -m app.analysis_cli --sources zhihu_hot_list --limit 5 --interval-minutes 120
```

这会执行真实采集；采样间隔参数只是声明调度间隔，本命令运行一轮。
知乎、微博及官媒仍依赖各自凭据、服务和网络条件。默认使用规则抽取，LLM refinement 适配器仍处于原有预留状态。

已有一轮 `FetchSourceItemsOutput` JSON 时，可在显式指定的独立数据库回放：

```powershell
python -m app.analysis_cli --replay sample-round.json --database sqlite:///./data/replay.db --interval-minutes 120 --matching-profile rules
```

回放文件须包含完整轮次和来源观测信息，使用分析模式标志；不要将不完整历史采样伪装成完整观测。
回放默认关闭官媒联网补查。准备好本地模型后可改为 `--matching-profile hybrid_rerank`；需要在回放时补查网络，必须显式加 `--official-search`。真实轮次也可用 `--no-official-search` 关闭补查。

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

上述为当时记录。2026-10-03 已落实旧官媒入池时效策略及有效榜单规则，见
[evaluation-v0.1 对应问题及修复](../reports/evaluation-v0.1.md#本轮问题定位)；本次未重采该真实轮次。

同日 19:45（北京时间）单独复测微博 CLI：服务已恢复为 `formal_active`，真实搜索成功，项目客户端保留的 3 条样本均有正文、发布时间和转评赞字段。
该复测只更新 CLI 可用性结论，首次整轮分析的记录和其余待修复问题仍见上述报告。

## 2026-09-29 用例评估

Day48–49 的第一版离线评估已运行：134 条带预期用例，覆盖模块规则与现有九步编排；另外回放了上一轮完整真实采集响应。入口为 `python -m app.evaluation`，原有 `run_evaluation_suite` Tool 已接入同一执行器。

数据来源、待人工复核的标签、复现命令和指标边界见 [用例说明](evaluation-cases-v0.2.md)，当前问题与逐项结果见 [评估报告](../reports/evaluation-v0.1.md)。本轮没有再次请求知乎 API 或微博 CLI，没有调整业务规则。报告中的自动合并漏召回、官媒误匹配及采集完整性标记丢失，需与“接口可以运行”分开看待。

## 2026-09-30 匹配改进与复评

已统一事件／证据比较字段，修复空动作自匹配、知乎父问题身份、榜单完整性与未知搜索质量；接入真实 BM25、BGE embedding、BGE cross-encoder 及官媒定向查询。九步编排和原有 Tool 继续复用，具体实现与运行参数见 [匹配更新说明](matching-upgrade.md)。

237 项回归测试通过。扩充到 208 条用例后进行三组真实模型对照，完整配置 193 条通过、15 条预期不符、0 条执行错误；原 134 条由 118 条通过提高到 132 条，未出现原通过用例回退。样本内无错误自动合并或官媒假阳性，但仍有漏匹配和待确定的旧新闻入池策略。见 [复评报告](../reports/evaluation-v0.2.md)、[用例 v0.3](evaluation-cases-v0.3.md)。
