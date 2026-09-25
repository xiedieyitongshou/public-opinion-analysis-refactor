# 平台内热度计算说明

## 文档目标

本文定义 Day 44 的平台内相对热度计算方案。

本方案只计算事件在单个平台内的观察强度：

```text
event_id + zhihu signals -> zhihu_platform_score
event_id + weibo signals -> weibo_platform_score
```

不计算、不展示微博、知乎和官媒原始指标相加得到的跨平台总分。

## 基本原则

- `event_id` 是第 6 周事件融合后的稳定业务 ID，是热度计算的聚合键，不是公式变量。
- 知乎和微博分别计算平台内相对热度，不能直接横向比较绝对值。
- 官媒不参与公众热度计算，只提供 evidence / authority / coverage 支撑。
- 缺失字段标记为 `unknown`，不把缺失简单当成 0。
- 大数指标使用 `log(1 + count)` 压缩。
- 只有 rank 时使用反向 rank 分、percentile 或 bucket 近似。
- 最终展示优先使用 `platform_bucket`、`score_status` 和 `score_detail_json`，避免伪精确。

## 输入与输出

输入：

```text
Event(event_id)
已归属该 event_id 的 SourceSignal / Item
历史 platform_scores / event_snapshots
```

输出：

```text
PlatformScore
- platform_score_id
- event_id
- platform
- platform_score
- platform_bucket
- platform_strength
- score_status
- primary_platform_rank
- rank_delta
- snapshot_presence_count
- raw_metrics_used
- score_detail_json
- calculated_at
```

`platform_score_id` 只是某次平台分记录 ID，不是事件 ID，也不能用于跨轮次识别同一事件。

## 通用工具函数

### weighted_available

缺失字段不参与计算，剩余权重重新归一化。

```text
weighted_available(scores, weights)
= sum(score_i * weight_i for score_i is available)
  / sum(weight_i for score_i is available)
```

如果没有任何可用子分：

```text
platform_score = null
score_status = unknown
platform_bucket = unknown
```

### normalize_log

用于处理评论数、点赞数、转发数、搜索结果规模代理等大数。

```text
normalize_log(x, cap)
= min(1.0, log(1 + x) / log(1 + cap))
```

默认 cap 建议：

| 指标 | cap |
|---|---:|
| `zhihu_engagement_cap` | `10000` |
| `weibo_search_total_cap` | `100000` |
| `weibo_status_count_cap` | `50` |
| `weibo_interaction_cap` | `10000` |

cap 是归一化基准，不代表真实平台上限。

### rank_score

如果有 rank 和榜单规模 `N`：

```text
rank_score = 1 - (rank - 1) / N
```

如果只知道抓取了 TopN，可以用抓取窗口近似 `N`。

如果只有 bucket：

```text
top3 = 1.00
top10 = 0.80
top20 = 0.60
tail = 0.35
unknown = null
```

### freshness_score

```text
<= 6h   -> 1.00
<= 24h  -> 0.80
<= 72h  -> 0.50
<= 7d   -> 0.25
> 7d    -> 0.10
unknown -> null
```

## 知乎平台分

知乎平台分只解释知乎平台内观察强度。

`zhihu_hot_list` 是知乎 TopN 主信号；`zhihu_search` 只能作为讨论补强，不等于知乎 TopN 热度。

### 公式

```text
zhihu_platform_score
= weighted_available(
    rank_score              * 0.50,
    snapshot_presence_score * 0.20,
    rank_delta_score        * 0.15,
    engagement_score        * 0.15
  )
```

### 子分

`rank_score`：

```text
来自 hot_list rank、platform_rank 或 rank bucket。
```

`snapshot_presence_score`：

```text
min(1.0, snapshot_presence_count / 3)
```

`rank_delta_score`：

```text
rising  -> 1.00
stable  -> 0.60
falling -> 0.30
unknown -> null
```

`engagement_score`：

```text
normalize_log(vote_count + comment_count, zhihu_engagement_cap)
```

如果 `vote_count` 或 `comment_count` 缺失，只使用可用字段；两个都缺失时 `engagement_score = null`。

### 降级

- 只有 rank：使用 `rank_score`，`score_status = partial`。
- rank 缺失但有多快照出现：使用 `snapshot_presence_score`，`score_status = partial`。
- 只有 `zhihu_search`：不标记 `zhihu_topn`，但可以标记 `zhihu_search = true`，并进入 Day 46 的搜索补强判断。
- 核心字段全部缺失：`score_status = unknown`。

## 微博平台分

微博平台分只解释微博侧观察强度。

RSSHub 只提供话题种子和列表位置弱信号；微博 CLI 搜索和代表性微博互动样本提供补强。

### 公式

```text
weibo_platform_score
= weighted_available(
    topic_presence_score     * 0.25,
    content_activity_score   * 0.35,
    interaction_sample_score * 0.40
  )
```

### 子分

`topic_presence_score`：

```text
base = 0.50 if topic_present else null
snapshot_bonus = min(0.30, 0.10 * snapshot_presence_count)
position_bonus:
  top3  -> 0.20
  top10 -> 0.15
  top20 -> 0.10
  tail  -> 0.05
  unknown -> 0

topic_presence_score = min(1.0, base + snapshot_bonus + position_bonus)
```

注意：RSSHub `list_position` 不能解释为微博官方 rank 或真实热度值，只能作为弱排序信号。

`content_activity_score`：

```text
weighted_available(
  normalize_log(weibo_search_total_number_proxy, weibo_search_total_cap) * 0.50,
  normalize_log(matched_status_count, weibo_status_count_cap) * 0.30,
  freshness_score(latest_status_created_at) * 0.20
)
```

`interaction_sample_score`：

```text
normalize_log(
  top_status_comment_count
  + top_status_repost_count
  + top_status_like_count,
  weibo_interaction_cap
)
```

如果代表性微博样本不存在，则 `interaction_sample_score = null`。

### 降级

- 只有 RSSHub 话题种子：只计算 `topic_presence_score`，`score_status = partial`。
- CLI 不可用：`content_activity_score = null`、`interaction_sample_score = null`，记录 `weibo_cli_unavailable`。
- CLI 搜索结果低相关：CLI 子分不参与评分，只写 `quality_flags`。
- RSSHub 不可用：`topic_presence_score = null`，如果 CLI 也不可用则 `score_status = unknown`。

## 多信号聚合

同一 `event_id + platform` 下可能有多个 SourceSignal / Item。

先计算单条信号的 `signal_score`，再聚合为该事件的平台分。

MVP 聚合规则：

```text
platform_score
= min(1.0, max_signal_score + 0.05 * min(3, extra_signal_count))

extra_signal_count = max(0, signal_count - 1)
```

含义：

- `max_signal_score` 表示该事件在该平台的最高观察强度。
- `extra_signal_count` 表示多条相关信号或多快照出现。
- `0.05 * min(3, extra_signal_count)` 是有限出现奖励，防止重复采集导致虚高。

## 分桶

从 `platform_score` 派生展示分桶：

```text
>= 0.85 -> top3
>= 0.70 -> top10
>= 0.50 -> top20
>  0.00 -> tail
null    -> unknown
```

如果平台存在真实 rank bucket，应优先保留真实 bucket；由 score 派生的 bucket 写入 `score_detail_json.score_bucket`。

## score_status

```text
ok       核心字段可用，平台分可信度较高
partial  只有部分字段可用，平台分只能作为弱信号
unknown  无法计算平台分
```

建议判断：

### 知乎

```text
ok:
  有 hot_list rank，且至少有 snapshot_presence 或 engagement 之一

partial:
  只有 rank，或只有 snapshot_presence，或只有 search 补强

unknown:
  无可用知乎平台信号
```

### 微博

```text
ok:
  RSSHub topic_present 且 CLI activity 或 interaction sample 至少一个可用

partial:
  只有 RSSHub topic_present，或只有 CLI 弱补强

unknown:
  RSSHub 和 CLI 均无可用信号
```

## 与分类的关系

Day 44 的平台分不直接生成 A/B/C/D/E/F 分类。

Day 46 读取 Day 44 输出，并结合 Day 45 增强后的官媒支撑结果生成：

```text
platform_presence
cross_platform_match_type
priority_category
confidence_level
evidence_summary
```

建议映射：

```text
zhihu_topn = 存在 zhihu hot_list 信号，且 score_status != unknown
weibo_topn = 存在 weibo RSSHub topic seed，且 score_status != unknown
zhihu_search = 存在高相关 zhihu_search 补强
weibo_cli = 存在高相关 weibo CLI 补强
official_source = official_support_status in supported / weak_supported
```

## 与 Day 47 的接口

Day 44 生成单轮平台内观察强度，Day 47 使用同 run 固定的 UTC `(window_end - 24h, window_end]` 分别分析知乎 / 微博。实施时在 `score_detail_json` 保存 `observation_id`、`observed_at`、`run_id`、评分配置版本和采样口径；评分必须关联真实当轮观测，重算旧 Item 不算新上榜。

Day 47 复用 `PlatformHeatScore` 作为静态热度，不累加各轮分数。连续观测时长来自主榜单逐轮 presence；搜索 / CLI-only 不产生时长。趋势为 `rising / stable / cooling / unknown`：知乎优先真实排名，微博可比较同评分配置、同子分权重与采样口径的平台分。RSSHub-only 仅给话题连续出现时间；RSS 顺序、freshness 自然衰减、重复观测 bonus 或采样规模变化不能单独触发升降温。

缺历史、来源中断、窗口外数据或不可比指标按 Day 47 fallback 输出 unknown，保留可用静态字段和原因。结果写入 `events.event_detail_json.platform_heat_analysis`，原始观测写入 `event_snapshots.metrics_json.platform_observations`；不修改 Day 46 分类，不写跨平台总分。

## 与展示的关系

EventCard 可使用：

```text
primary_platform
primary_platform_rank
platform_bucket
platform_strength
rank_delta
trend_status
classification_detail.platform_scores
```

展示应优先使用：

```text
知乎 Top10
微博话题样本出现
微博 CLI 有互动样本
score_status = partial
```

而不是展示伪精确结论：

```text
全网热度 98 分
微博真实热度 87 分
```

## 禁止项

- 禁止把知乎、微博和官媒原始指标直接相加。
- 禁止把官媒报道数量换算成公众热度。
- 禁止把 RSSHub `list_position` 写成微博官方 rank。
- 禁止把微博 CLI `total_number_proxy` 写成全站讨论量。
- 禁止把 `zhihu_search` 的互动指标写成知乎热榜热度。
- 禁止把缺失字段默认为 0 后参与评分。

## 最小测试用例

- 知乎只有 rank 时能输出 `partial` 和合理 bucket。
- 知乎有 rank、快照和互动时输出 `ok`。
- 微博只有 RSSHub topic seed 时输出 `partial`。
- 微博 RSSHub + CLI 互动样本可输出 `ok`。
- CLI 低相关时不参与微博分。
- 缺失字段不当成 0，权重重新归一化。
- 同一 `event_id + platform` 多信号使用 `max + capped bonus` 聚合。
- `platform_score_id` 不替代稳定 `event_id`。
