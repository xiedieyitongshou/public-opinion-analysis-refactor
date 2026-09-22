# Day 45 官媒处理任务整理

## 文档目标

本文整理 Day 45 围绕官媒信息处理需要明确的三个任务。

Day 45 不改变现有 ABCDEF 分类定义，也不把官媒报道数量解释为公众热度。它的目标是把官媒信息处理拆成两条清晰路径，并明确已有事件抽取工具和官媒检索能力如何接入。

## 任务一：重新编排官媒信息的两条处理路径

Day 45 需要明确两条官媒路径。

### 路径 A：社区事件定向补证

用途：

- 给微博 / 知乎已经发现的社区事件主动寻找官媒依据。
- 提高 `official_support_status` 的命中率。
- 为 A / B / D 类事件提供官方证据支撑。

流程：

```text
微博 / 知乎社区事件
-> 生成 official_query
-> 检索已有官媒 evidence pool
-> 如不足，触发低频官媒定向检索
-> 检索结果标准化为 Item
-> match_official_support
-> OfficialSupportResult
```

边界：

- 这是社区热点的官方证据补强。
- 官媒命中只能解释为事实 / 权威 / 覆盖支撑。
- 不能把官媒命中写成公众热度。
- 不改变原有 ABCDEF 分类规则。

### 路径 B：官媒内部议程聚合

用途：

- 看官媒自己在报道什么。
- 形成 `F_official_only` 或“高证据、低讨论”候选。
- 保留官方报道事件，但不让它挤占社区热点主列表。

流程：

```text
官媒 RSS / 官媒定向检索结果
-> normalize_raw_items
-> SourceSignal
-> extract_event_signals
-> match_and_resolve_events
-> Event
-> F_official_only / 高证据低讨论候选
```

边界：

- `F_official_only` 表示当前样本中只有官媒证据，没有明显社区 TopN / 搜索补强。
- `F_official_only` 可以进入单独展示区域。
- `F_official_only` 不应仅凭官媒覆盖挤占社区热点主列表。
- 官媒内部聚合不生成公众热度分。

## 任务二：接入已有事件抽取工具

`extract_event_signals` 已经是注册工具，Day 45 应复用它，而不是重新实现事件抽取。

### 工具接入原则

当官媒检索结果需要进入事件池或形成 `F_official_only` 时，应走完整事件链路：

```text
Official Item
-> SourceSignal
-> extract_event_signals
-> EventSignal
-> match_and_resolve_events
-> Event
```

当官媒检索结果只用于当前社区事件的证据引用时，可以先走较轻路径：

```text
Official Item
-> match_official_support
-> official_references
```

### 复用边界

- Agent 工作流中可以直接调用 `extract_event_signals` tool。
- service 内部的去重、聚类和相似判断不建议通过 Tool Registry 再调用 tool。
- service 内部可复用底层规则抽取、事件匹配和 `match_features` 思路，避免 tool 调 tool 带来日志、上下文和副作用复杂度。

## 任务三：调研官媒官网的 query 能力

Day 45 做官媒定向检索前，需要先确认选定官媒是否有稳定搜索入口。

### 需要确认的能力

对每个官媒 source 记录：

```text
source_id
source_name
search_capability
search_url_template
query_param_name
result_fields
requires_js
requires_login
rate_limit
source_status
notes
```

`search_capability` 建议枚举：

```text
search_api
site_search_page
rss_only
no_search
postpone
```

### MVP 接入边界

MVP 只接入：

```text
search_api
site_search_page 且字段稳定、无需登录、无需验证码、无需复杂 JS 渲染
```

暂不接入：

```text
需要登录的搜索
需要验证码的搜索
需要浏览器模拟的搜索
高频全文抓取
绕过访问限制的页面抓取
```

如果某个官媒没有稳定搜索入口：

```text
search_capability = rss_only / no_search
quality_flags += official_search_unavailable
```

此时继续使用已有 RSS evidence pool，不阻塞社区事件分析链路。

## 与去重和覆盖强度的关系

Day 45 后续实现官媒覆盖统计时，应区分：

```text
exact_duplicate        # URL、标题、guid 或 content_hash 完全重复
same_story_cluster     # 同一事件 / 同一通稿 / 同一事实材料
independent_story      # 独立报道角度或独立事实材料
```

重复报道不应直接删除。多家官媒转载同一材料可以提高：

```text
source_coverage_count
official_coverage_level
```

但不应提高：

```text
unique_story_count
事实独立性
公众热度
```

## 结论

Day 45 的核心不是“新增官媒爬虫”，而是：

```text
1. 明确社区事件定向补证与官媒内部聚合两条路径。
2. 在需要进入事件池时复用已注册的 extract_event_signals 工具。
3. 在做定向检索前确认官媒 source 是否具备稳定 query 能力。
```

