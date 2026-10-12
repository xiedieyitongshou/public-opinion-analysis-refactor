# 事件信号抽取

[返回项目首页](../README.md) · [工作流](architecture.md) · [事件匹配](event-matching.md)

当前 `extract_event_signals` 已执行规则抽取，输入为 `SourceSignal[]`，输出可追溯的 `EventSignal[]`。LLM refinement 仅预留适配器，真实请求尚未实现。

## 抽取解决什么问题

将热榜话题、知乎问题／回答和官媒标题转成可比较的事件特征，供下一步候选召回与归并使用。抽取本身不决定最终事件身份，不写跨平台分类，也不计算热度。

```text
SourceSignal
→ 标题与来源字段处理
→ 关键词、实体、动作、类型和时间提示
→ 置信度与弱信号标记
→ EventSignalAssembler
→ 严格校验后的 EventSignal
```

`audit_only` 来源被排除，原始来源引用继续保留。规则抽取不是通用命名实体识别，主体改写、角色、省略、否定和事件进展仍可能漏抽或误抽。

## 规则与输出

| 输出 | 来源与意义 |
| --- | --- |
| `title`、`keywords` | 标题、平台字段和词片段，过滤泛词并限制数量 |
| `entities` | 已覆盖的机构、地名、具体主体等规则；不是完整 NER 模型 |
| `action_terms`、`event_type` | 动作词及事件类型规则 |
| `event_time_hint` | 当前主要沿用发布时间，只是时间提示，不代表已核实事发时刻 |
| `event_text_for_match` | 供字面检索与可解释匹配的文本 |
| `semantic_fingerprint.event_text_for_embedding` | 供后续 embedding 使用的文本；本步骤不生成向量 |
| `confidence`、`is_weak_signal` | 特征完整程度和弱话题标记，供匹配护栏使用 |
| `source_signal_ids`、来源角色与质量标记 | 追溯到上游条目，不由模型改写来源身份 |

`event_signal_id` 由轮次和来源信号生成，是抽取记录身份。跨轮次追踪必须使用下一步归并生成或复用的 `Event.event_id`。

## LLM refinement 的预留设计

代码已有受限 `EventExtractionRefinement`、逐条选择条件和失败回退。允许增强主题、关键词、实体、动作、事件类型、弱信号与有限置信度增量，不允许直接改写事件 ID、来源、原始指标或作出合并决定。

启用条件要求全局 `EVENT_EXTRACTION_USE_LLM` 和工具输入 `use_llm` 同时为真，然后按低置信、字段不足、弱信号或高价值来源逐条选择；默认均关闭。输入只组装精简的标题、平台、发布时间、规则结果和质量标记。

**目前 `DeepSeekEventExtractionClient.refine()` 直接抛出未实现错误。** 仅填写 API Key 或开启开关无法获得真正的 LLM 抽取。还需实现请求、提示词、响应解析、输入预算和验证；失败时保留规则结果并标记降级。`analysis_cli` 当前没有 `--use-llm` 参数。

设计选择是先保留可审计规则，再对确有收益的困难样本增加可控增强，而不是用 LLM 接管身份和流程。后续效果必须独立评估，现有匹配评估不能充当 LLM 抽取准确率。

## 状态与实现入口

工具区分空输入 `skipped`、正常 `succeeded`、部分输入不可用／校验或增强失败的 `partial`，以及无法得到有效结果的 `failed`。具体错误和质量标记保留在输出与任务日志。

- 抽取器、组装器和预留客户端：[`agents/event_extractor.py`](../backend/app/agents/event_extractor.py)。
- 输入输出、溯源校验：[`schemas/signals.py`](../backend/app/schemas/signals.py)。
- 相关验证：[`test_day38_event_extractor.py`](../backend/tests/test_day38_event_extractor.py)、[`test_day37_event_signal_structured_output.py`](../backend/tests/test_day37_event_signal_structured_output.py)。
