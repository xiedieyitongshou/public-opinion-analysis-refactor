# 事件信号抽取

[返回项目首页](../README.md) · [工作流](architecture.md) · [事件匹配](event-matching.md)

当前 `extract_event_signals` 输入为 `SourceSignal[]`，输出可追溯的 `EventSignal[]`。默认规则抽取；新增独立 DeepSeek 抽取后端，使用 `EVENT_REASONING_MODE=llm` 选择，见[配置与边界](llm-reasoning.md)。已完成有限历史材料的真实 API 回放，不代表全量抽取效果。

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

## LLM 独立抽取（2026-10-29）

原按低置信度逐条 refinement 的混合设计已停用，兼容字段 `use_llm`、`EVENT_EXTRACTION_USE_LLM` 不再开启这种路径。总开关为 `EVENT_REASONING_MODE=rules|llm`；选择 llm 时所有符合来源约束的输入均由 LLM 提取，不调用词典提取器，不把旧实体和模型字段拼回输出。

新实现位于 `services/llm_event_workflow.py` 与 `services/llm_client.py`，不是旧 `DeepSeekEventExtractionClient.refine()`。输入包含来源标题及正文，响应字段有严格 Schema 和逐字原文引用校验；仅允许有证据的发生时间，缺失字段保留空。Key、响应或预算失败不回退规则结果，而是返回明确失败／部分成功。

模型不修改来源 ID、原始指标、热度公式或数据库归属。事件归并使用独立的 LLM 候选比较契约，不能把抽取结果本身视为同事件判决。默认关闭；模拟测试与真实材料回放的范围、时间格式失败等限制见[独立模式说明](llm-reasoning.md)。

## 状态与实现入口

工具区分空输入 `skipped`、正常 `succeeded`、部分输入不可用／校验或增强失败的 `partial`，以及无法得到有效结果的 `failed`。具体错误和质量标记保留在输出与任务日志。

- 抽取器、组装器和预留客户端：[`agents/event_extractor.py`](../backend/app/agents/event_extractor.py)。
- 输入输出、溯源校验：[`schemas/signals.py`](../backend/app/schemas/signals.py)。
- 相关验证：[`test_day38_event_extractor.py`](../backend/tests/test_day38_event_extractor.py)、[`test_day37_event_signal_structured_output.py`](../backend/tests/test_day37_event_signal_structured_output.py)。
