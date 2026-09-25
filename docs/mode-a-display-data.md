# 模式 A 展示数据说明

## 文档目标

本文说明模式 A 一图流热点简报 / 首页展示需要哪些数据，这些数据从哪条链路产生，以及当前阶段哪些数据已经可提供、哪些需要 Day 43-47 补齐。

模式 A 的展示核心不是“全网热度总分”，而是以稳定 `event_id` 为中心，解释一个事件：

```text
在哪些平台出现
是否有官媒支撑
属于哪类热点
平台内强度如何
趋势如何
为什么被纳入简报
有哪些可追溯来源
```

## 展示原则

- 以第 6 周生成的稳定 `event_id` 作为事件展示主键。
- 不展示微博、知乎、官媒原始指标相加得到的跨平台总分。
- 微博和知乎只做平台内强度、分桶和走势判断。
- 官媒只解释事实 / 权威支撑，不解释公众热度。
- `official_support_status = supported` 不等于“公众高度关注”。
- `zhihu_search = true` 不等于知乎 TopN。
- `weibo_cli = true` 不等于微博 TopN。
- 每条展示卡片必须保留来源引用。

## 展示对象

最终展示对象以 `EventCard` 为主，进入 `DailyBriefing.sections[].event_cards`。

核心字段：

```text
event_id
title
summary
priority_category
category_rank
platform_presence
cross_platform_match_type
official_support_status
confidence_level
primary_platform
primary_platform_rank
platform_bucket
platform_strength
rank_delta
trend_status
evidence_summary
source_citations
quality_flags
risk_notes
classification_detail
```

## 推荐栏目

模式 A 简报建议包含：

```text
今日总览
重点事件
分领域热点
分平台热点
正在升温事件
风险与不确定性提示
来源说明
```

栏目可以根据当天数据量降级。数据不足的栏目可以省略，但必须在日报元数据或 `risk_notes` 中记录原因。

## 分类展示逻辑

展示排序优先使用热点分类，而不是单一总分。

| 分类 | 展示含义 |
|---|---|
| `A_cross_platform_with_official` | 微博和知乎均有社区热点信号，且有官媒支撑 |
| `B_single_platform_with_search_and_official` | 单一社区平台上榜，另一平台搜索有高相关讨论，且有官媒支撑 |
| `C_cross_platform_without_official` | 双社区平台讨论成立，但暂无官媒支撑 |
| `D_single_platform_with_official` | 单一社区平台关注，且有官媒支撑 |
| `E_single_platform_only` | 单一平台候选，需低置信或保守展示 |
| `F_official_only` | 只有官媒报道，当前社区样本未捕捉到明显讨论 |

`F_official_only` 可展示在“高证据、低讨论”或“新闻源关注”区域，但不应挤占社区热点主列表。

## 数据生成链路

```text
采集器
-> NormalizedItem
-> SourceSignal
-> EventSignal
-> EventResolution
-> Event(event_id)
-> OfficialSupportResult
-> PlatformScore
-> HotspotClassification
-> EventSnapshot / trend_status
-> EventCard
-> DailyBriefing
```

各阶段职责：

| 阶段 | 产出 | 展示用途 |
|---|---|---|
| 第 6 周 Event Resolver | `event_id`、`EventResolution`、`match_features_json` | 确认哪些来源属于同一事件 |
| Day 43 官媒支撑识别 | `official_support_status`、`official_references`、`official_support_detail` | 判断是否有事实 / 权威支撑 |
| Day 44 平台内排序 | `platform_scores`、`platform_bucket`、`platform_strength`、`rank_delta` | 展示知乎 / 微博平台内强度 |
| Day 45 官媒补证 | 增强后的 `OfficialSupportResult` | 补充事实 / 权威依据 |
| Day 46 热点分类 | `priority_category`、`platform_presence`、`cross_platform_match_type`、`confidence_level` | 决定事件属于哪类热点 |
| Day 47 平台内热度分析 | `EventHeatAnalysis`、分平台连续观测时长与趋势 | 展示 24 小时窗口内知乎 / 微博各自的热度、升温 / 稳定 / 降温 / 未知 |
| Day 51 Briefing Agent | `EventCard`、`DailyBriefing` | 生成结构化简报草稿 |

## 当前已具备的数据

第 6 周完成后，已经可以提供：

```text
event_id
event_signal_id
source_signal_ids
matched_candidate_event_id
EventResolution.action
EventResolution.confidence
matched_by
match_features_json
guardrail_flags
candidate_review
source traceability
```

这些数据足以解释：

```text
为什么多个来源被认为是同一个事件
是否存在实体 / 时间冲突
是否需要人工复核
哪些来源参与了事件合并
```

但这些数据还不足以直接展示最终热点排序，因为它们不包含完整的平台内强度、官媒支撑状态和分类结果。

## Day 43 需要补的数据

Day 43 只做官媒支撑识别 service，不新增 crawler，不注册独立 Agent tool。

输出结构：

```text
OfficialSupportResult
- event_id
- official_support_status
- official_references
- official_support_detail
- quality_flags
```

`official_support_status`：

```text
not_checked
not_found
weak_supported
supported
```

`official_references` 至少包含：

```text
title
url
source_name
published_at
source_status
match_reason
match_features
```

`official_support_detail` 至少包含：

```text
official_source_count
authority_sources
freshness_bucket
match_quality
matched_item_ids
quality_flags
```

Day 43 优先从 `items` 表读取 `source_type = official_news` 的标准化条目作为 evidence pool。`events.source_citations_json` 只能作为二级补充，不作为主检索源。

## Day 44 需要补的数据

Day 44 计算的是已融合事件的分平台强度。

输入必须是：

```text
Event(event_id)
+ 已归属该 event_id 的知乎 / 微博 SourceSignal 或 Item
```

输出写入：

```text
platform_scores(event_id, platform, calculated_at)，另存来源 observed_at / observation_id
```

推荐字段：

```text
platform_score_id
event_id
platform
platform_score
platform_bucket
platform_strength
score_status
primary_platform_rank
rank_delta
snapshot_presence_count
raw_metrics_used
score_detail_json
calculated_at
```

### 知乎平台分

知乎平台分只在知乎平台内部解释。

推荐 MVP 公式：

```text
zhihu_platform_score
= rank_score * 0.50
+ presence_score * 0.20
+ rank_delta_score * 0.15
+ engagement_score * 0.15
```

字段说明：

```text
rank_score              来自 hot_list rank / bucket
presence_score          来自多快照出现次数
rank_delta_score        来自排名变化
engagement_score        来自 vote_count / comment_count 等可用互动字段
```

`zhihu_search` 的互动指标只能作为搜索补强和讨论证据，不等于知乎 TopN 热度。

### 微博平台分

微博 RSSHub 只提供话题种子，CLI 搜索 / 互动样本提供补强。

推荐 MVP 公式：

```text
weibo_platform_score
= topic_presence_score * 0.40
+ content_activity_score * 0.35
+ interaction_sample_score * 0.25
```

字段说明：

```text
topic_presence_score        RSSHub topic_present、list_position bucket、快照出现次数
content_activity_score      CLI 搜索结果规模代理、相关微博数量、发布时间新鲜度
interaction_sample_score    代表性微博评论、转发、点赞样本
```

RSSHub `list_position` 只能作为弱信号，不能解释为微博官方热度或真实 rank。

### unknown 处理

缺失字段不等同于 0。

如果某个子分缺失，则按可用权重重归一化，并将 `score_status` 标记为：

```text
ok          核心字段可用
partial     只有部分字段可用
unknown     无法计算平台分
```

### 多信号聚合

同一 `event_id + platform` 下可能有多个信号。MVP 不做简单相加。

推荐聚合：

```text
platform_score = min(1.0, max_signal_score + 0.05 * min(3, extra_signal_count))
```

含义：

```text
max_signal_score      代表该事件在该平台的最高观察强度
presence_bonus        代表多条相关信号或多快照出现
cap                   防止重复采集导致虚高
```

## Day 46 需要补的数据

Day 46 不重新计算平台热度，而是读取 Day 44 的 `platform_scores` 和 Day 45 增强后的 `OfficialSupportResult`。与 Day 47 共用当前 run 固定的 UTC `(window_start, window_end]`，其中 `window_start = window_end - 24h`；来源按真实观测时间入窗，窗口外历史只供审计。

输出：

```text
priority_category
category_rank
platform_presence
cross_platform_match_type
official_support_status
confidence_level
evidence_summary
classification_detail_json
```

`platform_presence` 判断建议：

```text
zhihu_topn     存在知乎 hot_list 来源，且 score_status != unknown
zhihu_search   存在高相关知乎 search 补强
weibo_topn     存在微博 RSSHub topic seed，且 score_status != unknown
weibo_cli      存在高相关微博 CLI 补强
official_source official_support_status in supported / weak_supported
```

`cross_platform_match_type` 判断建议：

```text
natural_topn_overlap       zhihu_topn && weibo_topn
search_supported           单平台 TopN + 另一平台高相关 search / CLI 补强
weak_search_supported      搜索补强存在但字段或匹配弱
single_platform_only       只有单平台 TopN
official_only              只有官媒支撑，无社区 TopN / search support
none                       无足够平台证据
```

## Day 47 需要补的数据

Day 47 在同一滚动 24 小时窗口内按 `event_id + platform` 分析，输出 `EventHeatAnalysis.platforms.zhihu / weibo`。Schema 与算法以实施计划 Day 47 为准，展示所需最小字段：

```text
run_id / window_start / window_end
current_topn_present
first_topn_seen_at / last_topn_seen_at
snapshot_presence_count
continuous_topn_minutes / duration_status
latest_platform_heat
trend_status / trend_basis
rank_delta / score_delta
comparison_observation_ids
quality_flags / limitations
```

`continuous_topn_minutes` 是当前连续 true 观测段的末次与首次时间差，不能用出现次数乘间隔，也不能直接取所有上榜记录的首末跨度。失败、未知、明确缺席或超过配置 `max_gap_minutes` 的间隔切断连续段；时间不向窗口边界外推。展示“24h 窗口内连续观测上榜约 X 分钟”；微博写“话题样本连续出现约 X 分钟”，不声称微博官方榜单持续时间。

趋势仅使用 `rising / stable / cooling / unknown`。知乎优先比较最近相邻观测的真实 rank；微博只有同口径可比平台分才判断升降温，RSSHub 顺序不作为 rank。stable 表示相对稳定，不代表高热度；爆发、二次波动、长尾不在本期范围。

热度沿用 Day 44 最新有效 `PlatformHeatScore`，两个平台分别展示，不累计 24 小时分数。榜单以 Day 46 分类分组，同平台、同分类内按可用状态、真实 rank 或同口径平台分、持续时长、稳定事件 ID 排序；官媒只提供证据解释。

| 情况 | 展示 fallback |
|---|---|
| 只有一个 true 观测 | 保留静态热度；时长下界 0、partial，显示“仅一次观测，持续时间待确认”；趋势 unknown |
| 来源失败 / 不完整 / 超长间隔 | 不把未知解释为下榜或降温；连续段中断；新 true 从新段开始，当前未知时长为 null |
| 无窗口内主榜单证据、观测过期、search / CLI-only、官媒-only | 持续时长 null、趋势 unknown，说明原因；不能把旧窗口热度当当前值 |
| 完整同口径榜单明确未命中 | 当前时长 0；无可比指标时趋势仍 unknown，缺失分数不补 0 |
| RSSHub-only / 分数不可比 | 连续话题观测时长可展示，趋势 unknown；不根据顺序、时间衰减或采样变化推断降温 |
| 单平台故障 | 保留另一平台结果；失效平台 unknown，整体 partial |

当前分析持久化于 `events.event_detail_json.platform_heat_analysis`，分类仍位于 `classification_detail`；双方覆盖各自对象并保留其他 key。原始观测写入 `event_snapshots.metrics_json.platform_observations`，时间列为 `snapshot_at`，按观测去重。展示前校验分类与分析的 run / 窗口相同，否则分析按 unknown 处理。

`EventCard` 计划新增可选 `platform_heat_analysis: EventHeatAnalysis`。兼容顶层 `trend_status` 只取明确指定 `primary_platform` 的趋势，无法映射则 unknown；知乎升温、微博降温可同时显示。升温栏目必须注明平台和窗口，不生成一个综合趋势。

## EventCard 组装规则

`EventCard` 应从以下来源组装：

| 字段 | 来源 |
|---|---|
| `event_id` | 第 6 周 Event Resolver |
| `title` | Event.title 或主信号标题 |
| `summary` | Briefing Agent 基于证据生成 |
| `priority_category` | Day 46 classification |
| `category_rank` | Day 46 classification |
| `platform_presence` | Day 46 classification |
| `cross_platform_match_type` | Day 46 classification |
| `official_support_status` | Day 43 / Day 45 official support |
| `confidence_level` | Day 46 classification |
| `primary_platform` | Day 44 platform_scores |
| `primary_platform_rank` | Day 44 platform_scores |
| `platform_bucket` | Day 44 platform_scores |
| `platform_strength` | Day 44 platform_scores |
| `rank_delta` | Day 47 主平台真实排名变化，无可比 rank 时 null |
| `trend_status` | Day 47 明确指定主平台的趋势，否则 unknown |
| `platform_heat_analysis` | Day 47 EventHeatAnalysis，包含两平台各自时长、热度和趋势 |
| `evidence_summary` | Day 46 / Day 51 |
| `source_citations` | NormalizedItem.source_citation + official_references |
| `risk_notes` | Guardrails / Critic / classification limitations |
| `classification_detail` | Day 43-46 detail JSON |

## EvidenceSummary 生成规则

`EvidenceSummary` 应避免事实过度断言。

建议结构：

```text
lead                    一句解释为什么纳入简报
platform_evidence       知乎 / 微博平台证据
official_evidence       官媒支撑证据
discussion_evidence     搜索补强、评论、互动样本
limitations             缺失字段、弱匹配、来源不可用
conservative_language_required
```

示例：

```text
lead: 该事件同时出现在知乎热榜和微博话题样本中，并有官媒报道支撑。
platform_evidence: ["知乎 Top10", "微博 RSSHub 话题种子出现，CLI 有相关微博样本"]
official_evidence: ["人民网报道与事件实体和时间窗口匹配"]
limitations: ["微博 CLI 样本有限，不能代表全站讨论量"]
```

## 风险提示

以下情况必须进入 `risk_notes` 或 `limitations`：

```text
official_support_not_found
single_community_source
weak_signal_only
low_confidence_match
embedding_only_without_hard_constraint
weibo_signal_unavailable
zhihu_credentials_missing
missing_source_url
stale_official_reference
```

## 不展示或慎展示的内容

MVP 不展示：

```text
全网热度总分
微博真实全站讨论量
官媒报道数换算出的公众热度
知乎 search 互动数换算出的知乎热榜热度
RSSHub list_position 换算出的微博官方热度
```

MVP 可以展示：

```text
知乎平台内强度
微博平台内弱强度
官媒支撑状态
跨平台匹配类型
趋势状态
来源引用
风险提示
```

## 最小可交付口径

Day 51 生成日报时，单张卡片至少需要：

```text
event_id
title
summary
priority_category
platform_presence
official_support_status
confidence_level
trend_status
evidence_summary
source_citations
publish_eligibility
```

如果 Day 44、Day 46 或 Day 47 数据不足，对缺失字段分别降级，保留其他可用字段：

```text
platform_bucket = unknown
platform_strength = unknown
trend_status = unknown
publish_eligibility = needs_review
```

但不能省略 `source_citations`。
