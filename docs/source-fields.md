# 来源字段映射

[返回项目首页](../README.md) · [数据源](data-sources.md) · [数据契约](data-contracts.md)

Collector 将不同来源映射为统一条目，Normalizer 再清洗为 `NormalizedItem`。归一化统一结构与追溯方式，不把知乎投票数、微博互动数和官媒报道数换成共同单位。

## 通用字段

| 字段 | 语义 |
| --- | --- |
| `source_id`、`source_name`、`source_type`、`platform` | 来源身份与平台 |
| `source_status` | 来源选型／质量层级，区别于本轮采集结果 `status` |
| `source_origin` | 获取路径：`official_rss`、`official_api`、`rsshub`、`weibo_cli`、`mock` |
| `signal_role` | 发现、关注、讨论、证据或搜索增强角色 |
| `title`、`url`、`summary`、内容字段 | 供抽取、匹配和引用的文字与原始链接 |
| `published_at`、`fetched_at` | 发布时间与采集时间，不能相互伪造 |
| `raw_metrics` | 保留平台原始或明确标注的代理指标，可为空对象 |
| `normalized` | 父子关系、采样补充、来源特征与归一化元数据 |
| `quality_flags` | 缺失、低相关、历史污染、降级和不可比原因 |

精确枚举、可选性和校验见 [`schemas/normalized.py`](../backend/app/schemas/normalized.py)，来源引用和父子关系见 [`schemas/signals.py`](../backend/app/schemas/signals.py)。`source_status=postpone` 的来源不能注册进正式采集器。

## 按来源映射

| 来源 | 主要角色 | 可用字段与解释 |
| --- | --- | --- |
| 知乎热榜 | `attention_signal` | 标题、问题 URL、真实 `rank`、响应声明的 `total` 等；缺热值不补造 |
| 知乎搜索 | `search_enrichment_signal` | 问题／回答身份、`comment_count`、`vote_count`／`vote_up_count`、`ranking_score`、编辑时间；通过初筛与父事件关联后才贡献 |
| 微博 RSSHub | `topic_discovery_signal` | 标题、搜索 URL、`list_position`、`topic_present`；RSS 位置带 `not_heat_metric` 等限制，不是官方榜位 |
| 微博 CLI | RSS 父条目的搜索增强 | 逐条 status ID、正文、时间、转评赞及相关性关系；正式输出汇总接纳样本的 `matched_status_count`、`top_status_*`、最新可解析时间 |
| 官媒 RSS | `evidence_signal`／事件发现 | 标题、URL、摘要、可靠发布时间、媒体名称／频道；不能从报道数量推导公众热度 |

微博 CLI 当前不单独写成一个可自动发现热点的 `NormalizedItem`；样本和逐条判定保存在父条目 `raw_payload.cli_enrichment`。概念上的 CLI 来源角色不应被误读为当前独立 Collector。

## 榜单观测与采样口径

条目之外，`CrawlValidationResult` 还传递 `run_id`、`observation_id`、`observed_at`、`list_complete`、`topn_scope`、请求范围与采样元数据。只有条目列表而没有这些上下文，不能可靠判断离榜与连续趋势。

`list_complete` 针对当前可获取的范围；成功取得合法短榜／空榜可判断缺席。异常、数量已知不齐或范围不可比保留未知。后续归一化与事件构建必须把观测上下文一起交给评分服务。

## 仅供审计的字段

- 微博查询总命中保存在 `normalized.cli_query_total_number_proxy`。正式 Collector 的 `raw_metrics.weibo_search_total_number_proxy` 为 `null`，不将未核验总量用于评分。
- 低相关、冲突和 `audit_only` 搜索结果保留追溯，但不作为分类或热度支持。
- 缺失的发布时间不会用采集时间伪造；官媒没有可靠时间时，不进入当天热点发现。
- `event_signal_id` 是抽取信号 ID；稳定事件聚合和展示必须使用 `Event.event_id`。

来源字段如何传到最终卡片，见[工作流](architecture.md#三个模块的数据交接)；热度具体输入与公式见[平台热度](platform-heat.md)。
