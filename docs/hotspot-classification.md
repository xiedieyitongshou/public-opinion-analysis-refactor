# 热点分类与组装

[返回项目首页](../README.md) · [产品展示](product-design.md) · [平台热度](platform-heat.md)

分类回答“这个事件有哪些已接纳来源、有没有官媒支撑”。本文说明判定规则、来源状态和展示组装；分类不重新抽取事件或计算热度。

## 输入与窗口

组装器读取稳定 `Event.event_id`、已接纳的来源信号、本平台分数、事件归并记录和 `OfficialSupportResult`。所有结果使用同轮固定 UTC `(window_end - 24h, window_end]`。

社区来源按真实观测时间入窗，官媒按可靠报道时间入窗；重评分的 `calculated_at` 不能让旧信号重新成为当前关注。只有已确认的条目关联参与组装，待人工审核的合并候选不提前算作另一平台来源。

当前离榜不删除窗口内已经接纳的覆盖证据。因此分类里的 `zhihu_topn=true` 可以表示过去 24 小时曾在榜，当前是否仍在榜要看趋势里的 `current_topn_present`。

## 平台覆盖与匹配方式

| 字段 | 含义 |
| --- | --- |
| `zhihu_topn` | 当前窗口有已接纳的知乎热榜证据 |
| `zhihu_search` | 已接纳的知乎搜索增强，不能替代热榜 |
| `weibo_topn` | 当前窗口有已接纳的微博 RSS 热榜话题 |
| `weibo_cli` | 已接纳的微博 CLI 增强，不代表独立上榜 |
| `official_source` | 当前事件有已接纳的官媒来源证据 |
| `mock` | 演示／测试来源标记，不冒充真实来源 |

`natural_topn_overlap` 表示两平台热榜证据属于同一事件；`search_supported`／`weak_search_supported` 表示单一热榜与另一平台的已接纳搜索证据；`single_platform_only` 表示单平台热榜；`official_only` 表示官媒来源。无足够依据时保留 `none`／`unknown`。

分类服务不重新拿标题比一次两平台热榜，覆盖依据来自事件融合和来源关联。知乎“热榜＋回答”仍只算知乎；微博“RSS＋CLI”仍只算微博。

## A–F 规则

| 类别 | 当前规则 | 展示含义 |
| --- | --- | --- |
| A `cross_platform_with_official` | 知乎和微博均有热榜证据，官媒 `supported` 或 `weak_supported` | 双社区关注，且有官媒支撑 |
| B `single_platform_with_search_and_official` | 单社区热榜、另一社区搜索支持、官媒有支持 | 单社区上榜，另一个社区有讨论补证 |
| C `cross_platform_without_official` | 跨社区覆盖成立，官媒没有被接纳为支持 | 社区共同讨论，当前没有已接纳官媒依据 |
| D `single_platform_with_official` | 单社区热榜、官媒有支持、无跨社区支持 | 单平台关注并有官媒报道 |
| E `single_platform_only` | 单社区热榜，无官媒和跨社区支持 | 可独立展示的单平台热点 |
| F `official_only` | 仅官媒来源，没有社区覆盖 | 官方报道事件，当前社区样本未捕捉到对应关注 |
| `unknown` | 输入不足，不能满足上述分类 | 保留缺失原因 |

枚举在代码中带完整前缀，例如 `A_cross_platform_with_official`。C 类的“无支持”同时可能来自 `not_found` 或 `not_checked`，必须展示原始官媒状态，不能把未检查写成确定未报道。

B 类及跨平台搜索枚举保留在契约中，但当前自动任务不从一个平台触发另一个平台搜索。不能把本平台增强算成 B，也不为填充类别额外制造跨平台证据。

## 分类置信度

| 类别 | 当前默认规则 |
| --- | --- |
| A | `high` |
| B | 搜索质量为同身份／标题时 `high`，其余为 `medium` |
| C、D | `medium` |
| E | 多次有效出现或有已知搜索支持时 `medium`，否则 `low` |
| F | 官媒 `supported` 时 `medium`，否则 `low` |
| unknown | `unknown` |

关键来源不可用标记会使置信度降为 `low`。搜索质量 `unknown` 不作为支持。分类置信度只是证据完整与分类稳定程度，不代表事实真伪，也不是事件匹配分数。

已接纳的 E 类可能置信度低，仍可进入简报；它与“尚未确认能否归并的候选”不同。后者在确认前不贡献来源覆盖。

## 组装结果与存储

[`classification_assembly.py`](../backend/app/services/classification_assembly.py)派生覆盖和搜索质量，调用 [`classify_hotspot`](../backend/app/services/event_classification.py)，再构造证据解释。

| 输出 | 用途 |
| --- | --- |
| `HotspotClassification` | 类别、置信度、覆盖、支持状态、原因、限制和排序键 |
| `EvidenceSummary` | `lead`、平台／官媒／讨论依据、限制及保守表述要求 |
| `classification_detail_json` | 轮次、窗口、输入来源、平台分、匹配特征、缺失／拒绝原因、质量标记 |

详情保存在 `events.event_detail_json.classification_detail`，同轮重算覆盖该对象并保留其他 key，不把不同轮次结果追加混在一起。趋势使用另一个 `platform_heat_analysis` 对象；展示只接受同轮同窗口结果。字段入口见[数据契约](data-contracts.md)。

## 排序与展示边界

底层 `HotspotSortKey` 提供类别、官媒支撑、匹配方式、榜位分桶、出现次数、排名变化、搜索质量、时效、来源健康和噪声的字典序键，不相加成跨平台分数。

实际 MVP 日报使用 [`briefing.platform_sort_key`](../backend/app/services/briefing.py)：平台栏目先比较在榜／未知／离榜，知乎可比较真实 rank，再比较本平台分数。官媒栏目比较引用来源数与最新报道时间。A–F 栏目内目前按标题和事件 ID 稳定排列，不是全网热度排名。

卡片保留来源引用与限制。同事件可出现在多个平台栏目，但同一栏目不能重复；官媒报道不能描述为“公众热议”，RSS 位置不能冒充官方热度。发布保护见[Guardrails](guardrails.md)。
