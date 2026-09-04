# Evaluation 设计

## 文档目标

本文定义模式 A：每日热点简报第一阶段的 Evaluation 指标。

第一阶段 Evaluation 的目标不是构建完整评测体系，而是让系统在自动运行后能回答：

```text
数据有没有采到？
字段是否够用？
工具是否稳定？
简报是否有来源？
Guardrails 是否触发？
存储是否可控？
```

## 设计原则

- 指标必须能从系统日志、数据库和工具结果中自动计算。
- 第一阶段不依赖大量人工标注。
- 指标数量保持有限，后续根据真实运行结果扩展。
- Evaluation 不阻断自动采集分析链路。
- Evaluation 失败必须记录，但不影响已经通过发布确认的正式日报。

Day 7 的 Evaluation 定义以“可自动计算”为边界。事件合并准确率需要人工标注样本，因此 Day 7 只定义占位口径，不纳入第一阶段默认启用指标；第一阶段用 `candidate_merge_rate` 和 `async_review_task_count` 先观测事件合并质量风险。

## 指标分组

第一阶段指标分为 5 类：

- 工具稳定性。
- 数据源质量。
- 事件处理质量。
- 简报与 Guardrails 质量。
- 存储与生命周期质量。

## 工具稳定性指标

### tool_success_rate

定义：

```text
成功工具调用次数 / 总工具调用次数
```

数据来源：

- `agent_tool_calls`
- `ToolResultMeta.success`

建议阈值：

```text
>= 0.95 pass
0.85 - 0.95 warn
< 0.85 fail
```

### tool_retry_rate

定义：

```text
发生重试的工具调用次数 / 总工具调用次数
```

用途：

- 判断数据源或工具是否不稳定。

建议阈值：

```text
<= 0.10 pass
0.10 - 0.25 warn
> 0.25 fail
```

### avg_tool_latency_ms

定义：

```text
工具调用平均耗时
```

用途：

- 发现采集、LLM 调用或渲染阶段的性能问题。

## 数据源质量指标

### source_success_rate

定义：

```text
成功采集的数据源数量 / 本轮计划采集的数据源数量
```

数据来源：

- `fetch_source_items`
- `source_results`
- `SourceFetchResult.success`

建议阈值：

```text
>= 0.80 pass
0.60 - 0.80 warn
< 0.60 fail
```

### item_count_by_source

定义：

```text
每个数据源本轮采集到的有效条目数
```

用途：

- 识别某个数据源是否长期无数据或结构变化。

数据来源：

- `SourceFetchResult.item_count`

### field_completeness_rate

定义：

```text
关键字段完整的 NormalizedItem 数量 / NormalizedItem 总数
```

关键字段：

```text
title
url
source_id
source_type
platform
fetched_at
content_hash
```

建议阈值：

```text
>= 0.95 pass
0.85 - 0.95 warn
< 0.85 fail
```

### source_coverage_rate

定义：

```text
简报事件中包含至少一个可追踪来源的事件数 / 简报事件总数
```

正式发布要求：

```text
必须为 1.0
```

## 事件处理质量指标

### event_signal_count

定义：

```text
本轮抽取出的 EventSignal 数量
```

用途：

- 判断事件抽取是否异常偏少或偏多。

### event_created_count

定义：

```text
本轮新建事件数量
```

用途：

- 识别事件拆分是否过细。

### event_updated_count

定义：

```text
本轮更新已有事件数量
```

用途：

- 观察事件持续追踪效果。

### candidate_merge_rate

定义：

```text
候选合并数量 / 事件合并判断总数
```

用途：

- 评估事件合并不确定性的规模。
- 该指标过高说明合并规则或事件抽取质量需要优化。

建议阈值：

```text
<= 0.20 pass
0.20 - 0.40 warn
> 0.40 fail
```

说明：

- 这是第一阶段自动指标，用来替代人工标注不足时的事件合并准确率。
- 候选合并数量来自 `EventResolution.action = candidate_review`。
- 事件合并判断总数来自 `EventResolution` 总数，不包含 `skip` 且无可比事件的结果。

### event_merge_accuracy_offline

定义：

```text
人工标注为正确的合并判断数量 / 已人工标注的合并判断数量
```

启用阶段：

```text
第 6 周 Evaluation Runner 和第一批 evaluation cases 建立后启用
```

第一阶段处理：

- Day 7 只定义该指标口径和数据需求。
- 不纳入 v0.3 第一阶段默认启用指标。
- 不作为自动发布门槛。

数据需求：

- `evaluation_cases` 中人工标注的 should_merge / should_not_merge 样例。
- `EventResolution.action`、`EventResolution.confidence` 和 `reason`。

### async_review_task_count

定义：

```text
本轮新增异步复核任务数量
```

用途：

- 衡量人工成本。

第一阶段目标：

```text
低频、可集中处理，不阻断自动采集分析
```

## 简报与 Guardrails 指标

### briefing_duplication_rate

定义：

```text
疑似重复事件卡片数量 / 简报事件卡片总数
```

建议阈值：

```text
0 pass
0 - 0.10 warn
> 0.10 fail
```

### guardrail_violation_count

定义：

```text
本轮 Guardrail violation 数量
```

分级统计：

```text
warn_count
block_count
```

用途：

- 观察简报质量和发布风险。

建议阈值：

```text
block_count = 0 pass
block_count > 0 fail
warn_count <= 3 pass
warn_count > 3 warn
```

### publish_block_rate

定义：

```text
被阻断正式发布的草稿数量 / 生成草稿数量
```

用途：

- 判断草稿质量是否足够进入人工发布确认。

### draft_ready_rate

定义：

```text
draft_ready_for_review 数量 / 草稿总数
```

用途：

- 衡量自动草稿的可发布准备度。

## 存储与生命周期指标

### database_size_bytes

定义：

```text
当前数据库文件大小
```

建议阈值：

```text
< 20G ok
20G - 30G warning
30G - 35G high_warning
35G - 38G critical
> 38G emergency
```

### cleanup_success_rate

定义：

```text
成功清理任务数量 / 清理任务总数
```

用途：

- 判断数据生命周期策略是否实际生效。

### snapshot_compression_count

定义：

```text
本轮压缩的高频 event_snapshots 数量
```

用途：

- 判断快照压缩任务是否工作。

### archived_event_count

定义：

```text
本轮从 active / cooling 转为 archived 的事件数量
```

用途：

- 判断事件生命周期是否能控制计算范围。

### active_event_count

定义：

```text
当前 active 事件数量
```

用途：

- 控制热度重算规模。

## 第一阶段指标清单

v0.3 第一阶段默认启用：

```text
tool_success_rate
tool_retry_rate
source_success_rate
item_count_by_source
field_completeness_rate
source_coverage_rate
event_signal_count
event_created_count
event_updated_count
candidate_merge_rate
async_review_task_count
briefing_duplication_rate
guardrail_violation_count
publish_block_rate
draft_ready_rate
database_size_bytes
cleanup_success_rate
snapshot_compression_count
archived_event_count
active_event_count
```

其中最核心的 MVP 指标是：

```text
tool_success_rate
source_success_rate
field_completeness_rate
source_coverage_rate
guardrail_violation_count
database_size_bytes
```

Day 7 只需要完成这些指标的定义、数据来源和阈值口径；第 6 周再实现完整 Evaluation Runner 和离线标注类指标。

## 后续扩展指标

以下指标后续再做，不作为第一阶段硬要求：

- 事件合并准确率，即 `event_merge_accuracy_offline`。
- 事件漏合并率。
- 事件误合并率。
- 摘要事实一致性。
- 热度排序人工相关性。
- 用户点击率。
- 用户订阅主题命中率。
- 模式 B 查询命中率。

这些指标通常需要人工标注、用户行为数据或更长时间运行样本，不适合在 v0.3 初期重压。

## Evaluation 输出

每次 Evaluation 输出 `EvaluationRun`，结构见 `docs/structured-outputs.md`。

建议输出摘要：

```text
run_id
evaluation_run_id
created_at
metrics
warn_metrics
fail_metrics
summary
```

## 与 Guardrails 的区别

Guardrails 负责边界和发布风险控制：

```text
这条内容能不能发布？
这个事件能不能自动合并？
这个社区信号能不能写成事实？
```

Evaluation 负责运行质量评估：

```text
工具是否稳定？
数据是否完整？
来源是否充足？
人工成本是否过高？
存储是否可控？
```

Evaluation 不替代 Guardrails，Guardrails 也不替代 Evaluation。

## 第一阶段结论

v0.3 Evaluation 应保持有限、自动化、可计算。

第一阶段先评估系统是否能稳定运行、稳定采集、稳定生成带来源的草稿，并控制存储增长。事件合并准确率和摘要事实一致性等更复杂指标，等真实数据和人工样本积累后再加入。
