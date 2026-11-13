# 平台热度、趋势与入榜离榜

[返回项目首页](../README.md) · [来源与指标](data-sources.md) · [分类](hotspot-classification.md)

评分对象是 `event_id + platform`；知乎与微博分别计算，官媒只提供证据，不生成跨平台总分。本文说明单轮评分与多轮趋势，公式以 [`platform_heat.py`](../backend/app/services/platform_heat.py) 为准。

## 输入与输出

输入来自已接纳到事件的条目／信号和真实来源观测。单条计算 `PlatformHeatSignalScore`，同事件同平台聚合为 `PlatformHeatScore`，再保存到数据库 `PlatformScore` 与观测快照。

主要输出是 `platform_score`、`platform_bucket`、`platform_strength`、`score_status`、原始指标、子分和质量标记。缺失字段使用 `null`；有部分指标不等于拥有完整平台热度。

## 通用函数

```text
weighted_available = Σ(可用子分 × 权重) / Σ(可用子分的权重)
normalize_log(x, cap) = min(1, log(1+x) / log(1+cap))
rank_score(rank, window=20) = clamp(1 - (rank-1)/window, 0, 1)
```

没有可用子分时输出 `null/unknown`。缺失指标保留缺失，普通子分按可用权重重新归一化。微博 RSS-only 使用下文独立的在榜参考分。`cap` 是项目压缩基准，当前互动 cap 为 10000，微博有效样本数 cap 为 50。

## 知乎单轮评分

| 子分 | 权重 | 输入 |
| --- | ---: | --- |
| 榜位 | 0.50 | 真实热榜 rank，搜索增强不补造 rank |
| 重复出现 | 0.20 | `min(1, snapshot_presence_count / 3)` |
| 排名变化 | 0.15 | rising=1、stable=0.60、falling=0.30；历史不足时缺失 |
| 互动 | 0.15 | 可用赞同数与评论数之和，做 log 压缩 |

有真实 rank，且存在互动或多轮历史时可为 `ok`；只有部分依据时为 `partial`。搜索互动只解释讨论增强，不冒充知乎热榜热值。

## 微博单轮评分

当前代码权重如下；早期设计的 0.25／0.35／0.40 已不作为现行说明：

| 子分 | 权重 | 输入 |
| --- | ---: | --- |
| 话题出现 | 0.40 | RSS 话题在榜、出现次数、列表位置弱信号 |
| 内容活跃 | 0.35 | 已接纳 CLI 样本数及最新时间 |
| 互动样本 | 0.25 | 已接纳样本的 `top_status_comment_count`、`top_status_repost_count`、`top_status_like_count` |

话题子分由 0.50 基础值、最多 0.30 的出现奖励和最多 0.20 的位置奖励组成。RSS 顺序可作为该静态弱信号的一部分，但不能用于声称官方排名或判断排名变化。

内容子分的通用计算器仍兼容“搜索总量代理 0.50、有效样本数 0.30、时效 0.20”的接口。**正式 Collector 将未核验的搜索总量代理设为 `null`**，仅在审计字段保留 query 总命中，因此该项不参与当前采集链路评分，其余可用子项重新分配权重。

CLI 时效子分仅使用已接纳帖子的 `latest_status_created_at`，按时间分段计算，6 小时内 1.0、24 小时内 0.8、72 小时内 0.5、7 天内 0.25，之后 0.10。正式 CLI 采集另有历史污染过滤。帖子时间缺失时该子项保留缺失。

CLI 互动字段来自过滤后的样本汇总，各 `top_status_*` 是各指标最大值，可能来自不同帖子。RSS 主榜话题至少有一条已接纳样本及可用互动计数时，使用完整公式，最低分为 0.30，`score_basis=cli_samples`。真实互动为零仍属于可用观测。

### CLI 不可用时的在榜参考分

CLI 失败、未接入、零条有效样本或缺少有效互动计数时，RSS 话题采用独立的数值型参考分。

```text
rss_reference_score = 0.20 × (0.60 + 0.40 × min(N, 5) / 5) × 2^(-Δt / 2)
N = 近24小时内、当前分析范围允许的真实在榜轮次数
Δt = 本次简报截止时间距最后一次在榜观测的小时数
```

参考分上限为 0.20，每两小时减半。刚刚出现一次为 0.136、三次为 0.168、五次为 0.200。最后出现于四小时前、共三次的参考分为 0.042，五小时前为约 0.030。列表位置和 RSS 发布时间均不参与该公式。

计数按采集轮次去重，同轮多个条目和同轮回放只计一次。RSS 成功而 CLI 失败仍可以建立在榜观测，RSS 失败则保留上次实际发现时间。缺少可靠观测时间的旧信号使用 0.0 参考分并记录 `missing_rss_observation_time`。

降级结果使用 `score_status=partial`、`score_basis=rss_presence_only` 和 `fallback_reason`。原因包括 `cli_unavailable`、`cli_no_relevant_samples`、`cli_metrics_missing`。`raw_metrics_used` 保存最后在榜时间、有效次数及估计时点，原始互动缺失继续保持缺失。

采集时的参考分写入原观测的 `PlatformScore.score_detail_json`。简报按固定截止时间计算 `estimated_platform_heat`，展示“在榜参考分”“CLI 增强不可用／有效样本不足”“本窗口在榜次数”和“最后在榜时间”。该字段与实际观测热度分别保存，排序优先使用本次参考分。

RSS-only 的多条来源采用最大参考分，重复信号不增加聚合奖励。存在有效 CLI 评分时，聚合使用 CLI 评分，RSS-only 信号不叠加奖励。微博评分版本为 `weibo-cli-rss-fallback-v2`，新旧版本以及降级／完整评分分别检查可比性。参考分衰减及 CLI 恢复均不能独立生成升降温结论。

## 同平台聚合与显示

```text
platform_score = min(1, max_signal_score + 0.05 × min(3, extra_signal_count))
extra_signal_count = 同事件同平台可用信号数 - 1
```

这是单轮可用信号的有限奖励，不把各轮分数累计成热度。重复出现历史由真实观测管理。

| 结果 | 解释 |
| --- | --- |
| `ok` | 当前平台的核心评分条件满足，仍受采样范围限制 |
| `partial` | 只有部分字段，例如微博仅 RSS 或知乎只有榜位 |
| `unknown` | 没有可用分数 |

无可用 rank 时，分数按 0.85／0.70／0.50 分成 `top3/top10/top20/tail`；这些是项目分桶标签，不是平台真实名次。微博兼容字段可能保留 RSS 位置，但趋势和界面不能把它当官方 rank。`PlatformHeatObservation.rank` 只允许知乎真实热榜。

## 滚动窗口与入榜离榜

分类、趋势和日报共用 UTC `(window_end - 24h, window_end]`。主来源每轮提供 `observation_id`、`observed_at`、`list_complete`、`topn_scope` 和采样签名。

| 本轮观测 | `current_topn_present` | 含义 |
| --- | --- | --- |
| 有效主榜单明确发现事件 | `true` | 当前在榜 |
| 成功、完整、范围可比的主榜单缺席 | `false` | 当前可获取榜单中明确离榜 |
| 失败、残缺、范围不可比、过期或缺少有效观测 | `null` | 无法确认，不把失败当作离榜 |

成功取得的合法短榜和空榜视为当前可观测范围的完整响应；“少于配置上限”不自动等于缺失。已知 total 与返回数量矛盾、结构异常或身份缺失则不能证明缺席。

当前离榜不会抹去窗口内的 `last_topn_seen_at`、出现次数或分类来源。最后有效发现证据过期后，事件不再进入当前结果，历史数据保留。官媒另外使用可靠发布时间，不能靠重新抓取旧报道续期。

2026-11-03 显示补充：每个平台标注“最后采集”时间，在榜／离榜指该次观测，不是打开网页时的实时状态。`last_collection_at` 是最近一次主榜单检查时间，包含未在榜的观测；`last_observed_at` 是最近一次取得热度值的时间，两者分别保存和展示。来源失败时标为“最后采集尝试”；没有时间证据则显示未记录。离榜后的旧名次写作“最近在榜第…名”，保留热度采集时间和“非当前值”。

“趋势待确认”附具体限制原因，包括只有一次在榜观测、最近已离榜、来源失败、榜单残缺、观测过期、采集间隔过长、范围／评分口径不可比，以及缺少 CLI 互动指标。旧简报预览从本期保存的原始采集记录补齐最后检查时点，不把报道发布时间、版本生成时间或最后一次热度值时间充作最新采集时间；不重写原简报，不重新采集或调用模型。

## 连续上榜与趋势

只连接相邻的有效 `true` 观测。`false`、`unknown`、范围变化或超长间隔切断连续段；单点只能提供 0 分钟的观测下界，不说明从何时真正开始上榜。

`expected_interval_minutes` 用于判断历史是否连续，默认最大间隔为它的两倍。没有采样间隔配置仍可输出本轮明确在榜／离榜和静态分，但连续时长与趋势保留未知。

| 平台 | 可用比较依据 | 不足时的结果 |
| --- | --- | --- |
| 知乎 | 相邻有效观测的真实榜位优先；变化阈值默认 1 位 | 缺历史或不可比时 `unknown` |
| 微博 | 同评分版本、同有效子分、同采样签名的平台分，默认变化阈值 0.05 | RSS-only、CLI 失效或口径变化时 `unknown` |

输出 `rising / stable / cooling / unknown`，保留比较的观测 ID 和依据。RSS 顺序、freshness 自然衰减或重复出现奖励不能单独造成升降温结论。

CLI 请求条数、实际排序（2026-10-25 起为 `sort=hot`，此前为 `time`）、话题覆盖和处理版本进入签名。新旧口径不直接比较，需要积累可比观测。本次只调整排序，暂时保留接纳条数在静态热度中的现有用途；趋势仍排除该条数。知乎搜索和 CLI-only 不生成独立主榜单上榜时长。

## 持久化与调用

主九步链路的评分步骤写入观测，随后趋势步骤只读；同轮重跑复用真实观测，不因审核、重评分或新日报版本制造新时间点。

| 位置 | 保存内容 |
| --- | --- |
| `PlatformScore.score_detail_json` | `observation_id`、`observed_at`、配置版本、子分和采样口径 |
| `EventSnapshot.metrics_json.platform_observations` | 原始平台观测序列 |
| `Event.event_detail_json.platform_heat_analysis` | 同轮同窗口的分析结果 |

旧记录只有 `calculated_at` 时不能据此构造采集观测。展示使用 `current_event_heat_analysis` 校验分类与趋势窗口；过期结果降级，不能直接读历史最高分。

代码入口：[评分](../backend/app/services/platform_heat.py)、[趋势计算](../backend/app/services/platform_trend.py)、[观测组装](../backend/app/services/platform_trend_assembly.py)、[趋势 Schema](../backend/app/schemas/platform_trend.py)。回归覆盖在 `test_day44_platform_heat.py`、`test_day47_platform_trend.py` 和 `test_hotspot_analysis.py`。
