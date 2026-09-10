# 工作流设计

## 文档目标

本文定义模式 A：每日热点简报的端到端工作流。

v0.3 阶段只完整设计和交付模式 A。模式 B：指定事件搜索暂不展开，只要求后续能够复用模式 A 沉淀出的数据结构、工具、事件模型、评分逻辑、Guardrails 和 Evaluation。

本文同时定义各阶段的执行职责和 LLM 使用边界，因此 Day 3 不再单独维护 `docs/agents.md`。当前阶段的重点不是设计多个完全自主 agent，而是设计一个由确定性状态机驱动、局部引入 LLM 能力的工作流系统。

模式 A 采用“双链路”策略：

- 自动采集分析链路：每 2-3 小时运行一次，自动采集、清洗、合并、评分、记录走势并生成草稿快照。
- 人工发布确认链路：正式发布前集中确认和整理，人工只处理需要发布的草稿和高风险问题，不参与每次自动采集分析。

## 模式 A 目标

模式 A 回答的问题是：

```text
在默认平台和默认领域下，今天有哪些事件值得关注？
```

系统每 2-3 小时围绕固定数据源和默认领域执行一次自动采集分析，将新闻源和社区源中的条目统一为标准内容，再合并成事件、计算热度、记录走势并生成草稿快照。

正式简报发布前，人工集中处理待复核项、调整标题摘要、确认风险提示和发布状态。未人工确认的结果可以作为内部草稿或个人 dashboard 展示，但公开发布版本必须经过发布确认。

## 系统定位

模式 A 不是完全自主 agent。

系统不让 LLM 决定全局状态流转，也不让 LLM 自主决定是否发布、是否重试、是否跳过数据源或是否写入数据库。这些行为必须由确定性状态机、工具执行结果、schema validation、Guardrails 结果和人工确认结果共同控制。

更准确的定位是：

```text
workflow-driven agent system
```

也就是：

```text
固定业务工作流
+ 确定性状态机
+ Tool Calling
+ 局部 LLM 判断
+ Guardrails
+ Evaluation
```

LLM 只在局部阶段承担受约束的智能任务，例如事件信号抽取、事件合并理由生成、摘要生成、评分解释、简报生成和质量检查。

## LLM 使用边界

LLM 可以参与：

- 事件信号抽取。
- 事件合并候选解释。
- 事件摘要生成。
- 评分解释生成。
- 简报草稿生成。
- 简报质量检查。
- 高风险表述的改写建议。

LLM 不负责：

- 控制全局状态流转。
- 决定是否进入下一阶段。
- 决定是否重试工具。
- 决定是否跳过数据源。
- 决定是否写入数据库。
- 单独决定是否发布。
- 修改任务状态。
- 绕过 Guardrails。

这些决策由 Orchestrator、任务状态机、工具执行结果、schema 校验、Guardrails 和人工确认共同完成。

## 总体链路

模式 A 分为自动采集分析链路和人工发布确认链路。

自动采集分析链路如下：

```text
每 2-3 小时定时触发
-> 创建 Briefing Snapshot Run
-> Orchestrator 生成执行计划
-> Collector Agent 采集新闻源和社区源
-> Normalizer Agent 标准化内容
-> Event Resolver Agent 抽取事件信号并合并事件
   -> 规则 / BM25 / n-gram 召回
   -> embedding 语义召回或 rerank
   -> Guardrails 决定自动合并、复核或拒绝
-> Scoring Agent 计算平台热度、综合热度和走势状态
-> 保存 event_snapshots
-> Briefing Agent 生成结构化草稿快照
-> Critic Agent 检查简报质量
-> Guardrails 标记风险和发布限制
-> 保存 draft_snapshot
-> Evaluation 记录本次运行质量
```

人工发布确认链路如下：

```text
人工打开待发布草稿
-> 查看候选简报和待复核项
-> 合并、拆分或忽略可疑事件
-> 调整标题、摘要和风险提示
-> 确认发布状态
-> 保存 published_report
-> 渲染 Web 首页和一图流长图
```

该链路必须保持可追踪。每次运行都应能回溯：

- 使用了哪些数据源。
- 调用了哪些工具。
- 每个阶段的输入和输出。
- 哪些内容被合并为同一事件。
- 每个评分的依据。
- 哪些 Guardrails 被触发。
- 哪些内容进入异步复核队列。
- 草稿为什么被标记为可发布、需确认或不可发布。
- 正式发布版本由谁确认或为何被阻断。

## 工作流阶段

### 阶段 1：触发自动采集分析任务

目标：

- 每 2-3 小时启动一次自动采集分析任务。
- 记录本次运行的配置、时间窗口和任务 ID。

输入：

- 默认平台配置。
- 默认领域配置。
- 可选关注关键词。
- 时间窗口，默认近 24 小时。
- 最大事件数量。

输出：

- `daily_briefing_run_id`
- `snapshot_run_id`
- `run_config`
- 初始任务状态。

状态：

```text
pending -> running
```

失败降级：

- 配置缺失时阻断运行。
- 部分可选配置缺失时使用默认值。

### 阶段 2：生成自动分析执行计划

目标：

- Orchestrator 将“生成热点草稿快照”拆解为可执行步骤。
- 每个步骤绑定 agent、tool 或确定性处理逻辑。

输入：

- `daily_briefing_run_id`
- `run_config`

输出：

- `Plan`
- `PlanStep[]`
- 对应的 `agent_tasks`

建议步骤：

```text
fetch_official_news
fetch_community_hotlists
normalize_items
extract_event_signals
resolve_events
calculate_scores
generate_briefing
critic_review
run_guardrails
save_draft_snapshot
run_evaluation
```

状态：

```text
pending -> running -> succeeded
```

失败降级：

- 计划生成失败时，整个 run 标记为 `failed`。
- 非关键步骤缺失时，可以生成降级计划，但必须记录原因。

### 阶段 3：采集数据

目标：

- 从第一阶段确认的数据源中采集新闻条目和社区热榜条目。
- 将采集行为统一包装为 tool call。

输入：

- 数据源列表。
- 时间窗口。
- 平台配置。
- 采集限制配置。

输出：

- `RawItem[]`
- tool call 日志。
- 数据源成功率。

第一阶段数据源：

```text
人民网
中国新闻网
新华网
央视网
微博热搜
知乎热榜
B站热门 / 排行榜
```

状态：

```text
pending -> running -> succeeded / retrying / failed
```

失败降级：

- 单个数据源失败不阻断整个简报生成。
- 新闻源失败时降低事实来源覆盖评分。
- 社区源失败时降低对应平台热度覆盖评分。
- 连续失败的数据源应进入降级列表，并在 Evaluation 中记录。

### 阶段 4：标准化内容

目标：

- 将不同来源的原始字段统一映射为 `NormalizedItem`。
- 标记缺失字段和异常字段。
- 生成去重指纹。

输入：

- `RawItem[]`
- 数据源字段映射规则。

输出：

- `NormalizedItem[]`
- `quality_flags`
- `content_hash`

标准化核心字段：

```text
source_id
source_name
source_type
platform
channel
rank
title
url
published_at
fetched_at
author
summary
content_text
raw_metrics
raw_payload
quality_flags
```

状态：

```text
running -> succeeded / failed
```

失败降级：

- 缺少 `title` 或 `url` 的条目直接丢弃或进入异常队列。
- 缺少 `published_at` 的条目允许保留，但必须标记 `missing_published_at`。
- 缺少互动指标的社区条目允许保留，但热度评分时按 `unknown` 处理。

### 阶段 5：事件信号抽取

目标：

- 从 `NormalizedItem` 中抽取事件候选信号。
- 识别关键词、核心实体、事件类型、动作词和时间信息。

输入：

- `NormalizedItem[]`

输出：

- `EventSignal[]`

建议字段：

```text
item_id
title
keywords
entities
event_type
action_terms
event_time_hint
confidence
source_citation
```

状态：

```text
running -> succeeded / failed
```

失败降级：

- 低置信度信号不直接进入自动合并，也不阻断自动链路。
- B站标题默认作为弱信号处理，除非标题包含明确实体、动作和事件对象。
- 单一社区来源产生的事件信号不能直接生成确定事实。

### 阶段 6：事件合并

目标：

- 将多个 `EventSignal` 合并为统一的 `Event`。
- 避免误合并不同事件。

输入：

- `EventSignal[]`
- 现有 `events`
- 时间窗口配置。

输出：

- 新建或更新后的 `Event[]`
- 合并置信度。
- 合并理由。
- 候选合并关系。
- 异步复核任务。

合并依据：

- 平台 ID、URL、知乎 question id、微博 mid 等硬匹配。
- 标题相似度。
- 关键词重合度。
- BM25 / n-gram 召回分。
- embedding cosine similarity。
- 核心实体重合度。
- 动作和事件对象重合度。
- 时间窗口。
- 来源类型。
- 平台覆盖。

事件合并采用混合匹配链路，具体设计见 `docs/event-matching.md`：

```text
hard_match_by_id_url
-> bm25_ngram_retrieve_candidates
-> embed_event_text
-> semantic_retrieve_candidates
-> rerank_event_matches
-> match_event
-> apply_merge_guardrails
-> auto_merge / candidate_review / reject
```

执行边界：

- 规则强匹配和 ID / URL 命中优先于 embedding。
- BM25 / n-gram 用于可解释召回，embedding 用于同义改写和跨平台表达差异的二阶段召回或重排。
- embedding 高相似不能单独触发自动合并，必须同时满足实体、动作、对象或时间窗口中的硬约束。
- Agent 负责编排工具和解释结果，不直接凭语言判断写库合并。

状态：

```text
running -> succeeded / failed
```

失败降级：

- 高置信度匹配自动合并。
- 中置信度匹配不自动合并，记录候选关系并进入异步复核队列。
- 核心实体冲突时禁止自动合并，记录原因，不阻断整条自动链路。
- 时间窗口冲突时禁止自动合并，记录原因，不阻断整条自动链路。
- embedding 服务不可用时降级为规则 + BM25 / n-gram。
- BM25 / n-gram 无召回时允许尝试 embedding 召回，但命中结果最多进入中置信复核，除非其它硬约束也成立。
- B站弱匹配默认不自动合并，只作为候选传播信号。

### 阶段 7：热度评分与走势判断

目标：

- 计算每个事件在不同平台上的热度。
- 计算综合热度。
- 保存快照，用于后续走势判断。

输入：

- `Event[]`
- 关联 `NormalizedItem[]`
- 历史 `event_snapshots`

输出：

- `platform_scores`
- `event_snapshots`
- `trend_status`
- `score_detail_json`

评分组成：

```text
official_score
community_score
platform_score
coverage_score
velocity_score
controversy_score
total_score
```

第一阶段走势状态建议先控制为：

```text
new
rising
stable_high
cooling
unknown
```

状态：

```text
running -> succeeded / failed
```

失败降级：

- 历史快照不足时，趋势状态标记为 `unknown` 或 `new`。
- 平台指标缺失时，该平台评分标记为 `unknown`。
- B站作为视频传播信号默认降权。
- 综合评分必须保留解释明细，不输出绝对化判断。

### 阶段 8：生成结构化简报

目标：

- Briefing Agent 基于评分后的事件生成结构化草稿快照。
- 输出必须是结构化结果，不能只生成自由文本。

输入：

- `Event[]`
- `platform_scores`
- `event_snapshots`
- `source_citations`
- 简报栏目配置。

输出：

- `DailyBriefing`
- `BriefingSection[]`
- `EventCard[]`
- `draft_snapshot`

建议栏目：

```text
今日总览
重点事件
分领域热点
分平台热点
正在升温事件
风险与不确定性提示
来源说明
```

状态：

```text
running -> succeeded / failed
```

失败降级：

- 来源不足的事件不能进入正式发布版本，只能进入草稿候选或风险提示。
- 栏目数据不足时允许省略栏目，但必须记录原因。
- 简报草稿必须保留来源引用。

### 阶段 9：质量检查

目标：

- Critic Agent 检查简报草稿是否存在重复、缺来源、事实过度断言或栏目失衡。

输入：

- `DailyBriefing`
- `EventCard[]`
- `source_citations`

输出：

- `CriticResult`
- 问题列表。
- 修正建议。

检查项：

- 是否存在重复事件。
- 是否存在无来源摘要。
- 是否把单一社区来源写成确定事实。
- 是否存在低置信度事件未提示。
- 是否存在栏目严重失衡。
- 是否存在 B站弱信号被写成事实来源。

状态：

```text
running -> succeeded / failed
```

失败降级：

- block 级问题标记为发布阻断，不阻断自动草稿保存。
- warn 级问题自动附加提示或进入异步复核队列。
- 无法完成检查时，草稿保存为 `draft_needs_review`，不能自动进入正式发布。

### 阶段 10：Guardrails 决策

目标：

- 将规则检查结果转化为草稿发布状态和风险标记。

输入：

- `DailyBriefing`
- `CriticResult`
- `Event[]`
- `source_citations`

输出：

- `GuardrailResult`
- `guardrail_violations`
- 草稿发布建议。

草稿发布建议：

```text
ready_for_review
needs_review
blocked_for_publish
```

基础规则：

- 没有来源链接的内容不能进入正式简报。
- 单一社区来源不能写成确定事实。
- 低置信度事件合并不能自动合并，可进入异步复核队列。
- 核心实体冲突时禁止自动合并。
- 时间窗口冲突时禁止自动合并。
- 每条事件卡片必须保留来源引用。

状态：

```text
running -> succeeded / failed
```

失败降级：

- Guardrails 运行失败时，草稿保存为 `draft_needs_review`。
- Guardrails 不阻断自动采集分析链路，但会阻断正式自动发布。

### 阶段 11：保存草稿快照

目标：

- 保存自动生成的草稿快照。
- 保留发布建议、风险标记和待复核项。

输入：

- `DailyBriefing`
- `GuardrailResult`

输出：

- `daily_reports`
- 草稿状态。

草稿状态：

```text
draft
draft_ready_for_review
draft_needs_review
draft_blocked_for_publish
```

失败降级：

- 保存失败时，整体 run 标记为 `failed`。
- 草稿保存成功后，自动链路结束，不等待人工确认。

### 阶段 12：人工发布确认

目标：

- 在正式发布前集中确认和整理草稿。
- 将自动草稿转化为正式发布版本。

输入：

- `draft_snapshot`
- `review_queue`
- `GuardrailResult`
- `CriticResult`

输出：

- `published_report`
- 人工处理记录。
- 最终发布状态。

发布状态：

```text
published
published_with_warning
blocked
```

人工可以执行：

- 合并候选事件。
- 拆分误合并事件。
- 忽略低价值事件。
- 修改事件标题。
- 修改摘要。
- 添加风险提示。
- 批准发布。
- 阻断发布。

失败降级：

- 人工未确认时，不生成公开发布版本。
- 草稿仍可保留为内部预览或个人 dashboard 数据。

### 阶段 13：渲染展示

目标：

- 将已发布简报渲染为 Web 首页和一图流长图。

输入：

- 已发布的 `daily_report`

输出：

- 首页数据。
- 长图渲染结果。
- 可选 PNG 文件。

状态：

```text
running -> succeeded / failed
```

失败降级：

- 长图渲染失败不影响 Web 首页展示。
- 首页渲染失败时保留 API 数据，供排障使用。

### 阶段 14：运行评估

目标：

- 对本次自动采集分析链路和发布链路进行基础评估。
- 形成可观察、可比较的质量指标。

输入：

- `agent_tasks`
- `agent_tool_calls`
- `events`
- `daily_report`
- `guardrail_violations`

输出：

- `evaluation_run`
- 指标结果。

第一阶段指标：

- 工具调用成功率。
- 数据源采集成功率。
- 字段完整率。
- 候选合并比例。
- 异步复核任务数量。
- 日报重复率。
- 来源覆盖率。
- Guardrail violation 数量。

状态：

```text
running -> succeeded / failed
```

失败降级：

- Evaluation 失败不阻断简报发布。
- Evaluation 失败必须记录错误，避免系统只展示结果不展示质量。

## 状态流转

### Run 状态

自动采集分析运行建议使用以下状态：

```text
pending
running
succeeded
failed
```

典型流转：

```text
pending
-> running
-> succeeded
```

草稿发布状态：

```text
draft
draft_ready_for_review
draft_needs_review
draft_blocked_for_publish
published
published_with_warning
blocked
```

人工发布确认流转：

```text
draft_ready_for_review
-> published
```

需要整理的发布流转：

```text
draft_needs_review
-> manual_review
-> published / published_with_warning / blocked
```

### Agent Task 状态

单个 agent task 建议使用以下状态：

```text
pending
running
succeeded
failed
retrying
blocked
```

## 异步复核与人工发布确认

人工确认不阻断每 2-3 小时一次的自动采集分析链路。系统应优先采用保守降级策略继续运行，将不确定项写入异步复核队列。

以下情况进入异步复核队列：

- 事件合并置信度处于中间区间。
- 事件核心实体冲突但相似度较高。
- 同一事件来自多个社区源但缺少新闻源确认。
- B站条目与已有事件弱匹配，但热度很高。
- 简报草稿中存在高风险事实表述。
- Critic Agent 输出关键 warn。
- Guardrails 输出 `needs_review`。

以下情况阻断正式发布，但不阻断自动采集分析：

- 草稿中存在无来源的事实表述。
- 单一社区来源被写成确定事实。
- 高风险指控缺少权威来源。
- Guardrails 运行失败。
- 草稿质量检查失败。

人工发布确认可以执行：

- 合并到已有事件。
- 拆分误合并事件。
- 创建新事件。
- 忽略条目。
- 修改事件标题或摘要。
- 添加不确定性提示。
- 批准发布。
- 阻断发布。

## 失败与降级策略

系统必须允许部分失败，而不是因为单个数据源失败导致整条链路不可用。

推荐策略：

- 单个新闻源失败：继续运行，但降低来源覆盖评分。
- 单个社区源失败：继续运行，但标记平台覆盖不足。
- 标准化失败：丢弃问题条目或进入异常队列。
- 事件合并低置信度：不自动合并，进入异步复核队列，不阻断自动链路。
- 热度评分缺字段：该字段标记 unknown，不伪造数值。
- Briefing 生成失败：保留事件列表，不生成本次草稿快照。
- Critic 或 Guardrails 失败：保存草稿为 `draft_needs_review` 或 `draft_blocked_for_publish`，不自动公开发布。
- 长图渲染失败：保留 Web 首页和结构化数据。
- Evaluation 失败：不阻断发布，但必须记录。

## 可观测性要求

每次工作流运行必须记录：

- run ID。
- run 配置。
- plan 和 plan steps。
- agent task 状态。
- tool call 输入输出摘要。
- 数据源采集成功率。
- 标准化条目数量。
- 创建和更新的事件数量。
- 异步复核任务数量。
- 评分结果摘要。
- Guardrail violations。
- 草稿状态。
- 最终发布状态。
- Evaluation 指标。

这些记录后续用于 Agent Ops 面板展示。

## 模式 A 与后续模式 B 的关系

v0.3 不展开模式 B 工作流。

模式 B 后续应复用模式 A 产生的能力：

- `items`
- `events`
- `platform_scores`
- `event_snapshots`
- `source_citations`
- `search_existing_evidence`
- 事件合并规则。
- 热度评分逻辑。
- Guardrails。
- Evaluation。

只有当模式 A 的数据采集、事件合并、评分和来源证据足够稳定后，才适合扩展指定事件搜索。

## 第一阶段结论

模式 A 的工作流设计应坚持以下原则：

- 先稳定跑通每日简报链路。
- 先统一数据结构，再扩展平台数量。
- 先做可解释相对热度，不做绝对热度判断。
- 先做规则版走势状态，不做复杂趋势预测。
- 社区源主要作为热度信号，不作为单独事实来源。
- B站默认作为视频传播补充信号，并在评分和事件合并中降权。
- 自动采集分析链路每 2-3 小时运行一次，不等待人工确认。
- 人工只在正式发布前集中确认和整理。
- Guardrails 失败时不阻断自动采集分析，但阻断正式发布。
- Evaluation 不阻断发布，但必须记录质量。
