# 搜索增强边界与初筛规则

## 文档目标

本文对应 Day 23：定义 `SourceSignal`、`EventSignal`、`EventCandidate` 与搜索增强边界。

Day23 不重新验证数据源是否可访问。Day16-21 已经完成 smoke test 和字段审计；Day23 的目标是把已验证的数据链路工程化为可校验 schema 和确定性初筛规则。

## 主链路

```text
NormalizedItem
-> SourceSignal
-> EventSignal
-> EventCandidate
-> Event
```

职责划分：

| 层级 | 作用 |
|---|---|
| `NormalizedItem` | 多来源字段结构标准化 |
| `SourceSignal` | 来源级信号，保存平台内特征、raw metrics、质量标记和搜索增强关系 |
| `EventSignal` | 事件级信号，抽取标题、实体、关键词、动作和匹配文本 |
| `EventCandidate` | 合并前候选事件，承接主发现来源和增强来源 |

## 知乎链路

```text
zhihu hot_list
-> attention_signal
-> EventCandidate
-> zhihu_search enrichment
-> SearchEnrichmentRelation
```

规则：

- `hot_list` 是模式 A 热点发现源，可以创建 `EventCandidate`。
- `zhihu_search` 是搜索增强源，只能挂到已有 `hot_list` 候选或模式 B 用户指定事件。
- `zhihu_search` 不作为模式 A 常规定时采集的新热点发现入口。
- 同一个 `hot_list` 候选触发的高相关 `zhihu_search` 结果可以作为该候选的讨论和互动补充。
- 低相关、历史污染、同名无关结果只进入审计日志，不参与分类支撑。

高相关条件至少满足一项：

- 知乎 question id 一致。
- 搜索结果标题与候选标题强包含。
- 核心实体、动作词或对象高度重合。
- 关键词 / n-gram 重合度达到阈值。
- 时间窗口没有明显冲突。

## 微博链路

```text
weibo RSSHub hot search
-> topic_discovery_signal
-> EventCandidate
-> weibo CLI search/statuses/limited enrichment
-> SearchEnrichmentRelation
```

规则：

- RSSHub `/weibo/search/hot` 是微博侧话题种子来源，可以创建弱 `EventCandidate`。
- RSSHub item 顺序只能作为 `list_position`，不能解释为微博真实热度。
- 微博 CLI 只对 RSSHub TopN、知乎 TopN 或用户指定事件做搜索补强。
- 微博 CLI 搜索结果不能递归扩展成新的热点采集链路。
- CLI 不可用时保留 RSSHub topic seed，并降低微博侧置信度，不阻塞知乎和官媒链路。

高相关条件至少满足一项：

- 微博正文强包含 RSSHub 话题标题。
- 微博 hashtag 与 RSSHub 话题标题一致。
- 正文同时包含核心实体和关键动作词。
- 与知乎 / 官媒候选标题共享核心实体和事件对象。
- 发布时间处于当前事件窗口。

拒绝或审计条件：

- 只命中泛词，不命中核心实体。
- 明显是历史旧闻、同名无关事件或时间窗口冲突。
- 营销、抽奖、广告、搬运号内容占主导。
- 搜索结果来自高度单一账号且缺少自然讨论迹象。
- 低相关结果只保留 `SearchEnrichmentRelation` 审计记录。

## 决策输出

搜索增强初筛输出 `SearchEnrichmentRelation`：

```text
accepted   -> 可进入 SourceSignal，并允许参与分类支撑
audit_only -> 只保留审计，不参与分类支撑
rejected   -> 明确拒绝，不参与分类支撑
```

字段要求：

- `matched_by` 记录命中原因，例如 `same_zhihu_question_id`、`title_containment`、`hashtag_match`、`keyword_overlap_high`。
- `rejected_by` 记录拒绝原因，例如 `keyword_overlap_low`、`historical_content_pollution`、`time_window_conflict`。
- `quality_flags` 记录字段缺失、低相关、历史污染、CLI 不可用等可审计标记。
- `audit_only = true` 的信号必须设置 `contributes_to_classification = false`。

## 非目标

- 不在 Day23 实现正式 collector。
- 不引入 LLM 判断搜索结果相关性。
- 不引入 embedding 自动合并。
- 不把搜索增强结果作为模式 A 的递归热点发现入口。
- 不把微博 RSSHub 顺序、微博 CLI 搜索结果数、知乎互动数合成为跨平台总分。
