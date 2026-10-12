# 官媒证据与议程

[返回项目首页](../README.md) · [数据源](data-sources.md) · [事件匹配](event-matching.md) · [分类](hotspot-classification.md)

官媒回答“有哪些报道依据、哪些媒体关注”，不产生公众热度分。本文说明独立议程发现、社区事件补证与证据采纳规则。

## 两条处理路径

| 路径 | 输入与处理 | 输出 |
| --- | --- | --- |
| 官媒自身议程 | RSS 条目 → 归一化 → 规则抽取 → 事件归并 | 官媒独立事件或与社区事件融合；没有社区覆盖时可归为 F |
| 社区事件补证 | 既有社区事件 → 本地官媒证据池 → 必要时生成 query 补查 → 候选相关性判定 | `OfficialSupportResult`、已接纳引用及覆盖信息 |

自动发现官媒事件以 RSS 为入口。定向搜索结果在当前分类阶段是已有事件的候选证据，不会因搜索命中就自动新建一个热点。独立的官媒路径服务复用抽取与归并函数，避免在 service 内层层调用 Tool Registry。

## 支撑状态

| 状态 | 当前含义 |
| --- | --- |
| `supported` | 已判与目标事件相关，并满足来源、URL、标题、发布时间等支持条件 |
| `weak_supported` | 已判相关，但来源质量或部分证据条件不完整；不是允许可疑相关性混入 |
| `not_found` | 当前可检查范围内没有接纳的支持；不等于证明官媒从未报道 |
| `not_checked` | 没有完成有效检查，例如全部查询失败 |

分类规则把前两种视为有官媒支持，同时保留支持强弱和限制。每日热点还要求官媒可靠发布时间在同一个滚动 24 小时窗口内；泛用匹配服务的弱支持不能绕过当天发现和发布的时效检查。

## 官媒误匹配的字段与判定规则改动

早期 v0.1 的“养老金事件误用安哥拉讲座作证据”由字段读取和缺失值处理共同造成。当前顺序是读取目标事件真实特征、比较候选、检查具体事件依据，再判断来源与时间。

| 问题 | 当前处理 |
| --- | --- |
| 持久化特征在嵌套字段，读取端只找顶层 | `event_document()` 优先读取 `event_detail_json.match_features`；明确空值不拿旧字段补成有值 |
| 目标动作缺失时拿候选动作和自身比较 | 缺失实体／动作记为缺失，重叠值为 0，不产生虚假的完整匹配 |
| 同动作、同日期或泛词命中被当作支持 | 明确冲突先排除，再要求足够文本相关性和具体事件锚点 |
| 高模型分掩盖对象、地点或时间冲突 | rerank 只能辅助；具体对象和硬约束不被模型高分覆盖 |
| 搜索结果未经核验直接改变类别 | 候选先落库审计，通过相关性和证据条件才进入已接纳引用 |

有效锚点包括标题强包含、事件实体、具体对象，或足够的完整关键词覆盖。共享约束有限，未覆盖的角色、否定、同名主体和事件进展仍可能产生歧义。

[2026-09-30 复评](../reports/evaluation-v0.2.md#同集对照与真实模型执行)中，三种 profile 在扩充官媒集的结果相同，不能把字段与规则修复的收益单独归因于 reranker。该报告仍有官媒正例漏检；疑难官媒相关性尚未自动进入事件合并人工队列。

## 定向搜索配置与降级

| 配置 | 默认值 | 含义 |
| --- | --- | --- |
| `OFFICIAL_SEARCH_ENABLED` | `true` | 对未获支持的社区事件补查 |
| `OFFICIAL_SEARCH_MAX_EVENTS` | `3` | 每轮最多补查事件数 |
| `OFFICIAL_SEARCH_LIMIT` | `5` | 每源保留候选上限 |
| `OFFICIAL_SEARCH_TIMEOUT_SECONDS` | `10` | HTTP 超时 |
| `OFFICIAL_SEARCH_CACHE_MINUTES` | `30` | 同事件／query 缓存时长；同轮复用 |

当前接入中新网 GET 和人民网 POST 搜索，限定官方域名，只解析返回数据，不执行搜索网页脚本。还有每源请求预算；达到预算、模型或来源失败时记录具体标记，不补造支持。分析 CLI 可用 `--no-official-search` 关闭；离线回放默认不补查。

[2026-09-29 实测](../reports/official-search-validation-2026-09-29.md)记录中新网 8 次成功、人民网 4 次 HTTP 405。这是当时的请求结果，不能证明长期可用或两个端点如今都可用。没有可用 query 时仍可使用已取得的 RSS 证据。

## 来源覆盖与去重

`OfficialSupportDetail` 保留 `official_item_count`、`source_coverage_count`、`unique_story_count`、`official_coverage_level` 和 `source_independence_flags`。

实现按内容 hash、URL、条目身份或规范标题生成 story key，区分相同记录与不同来源覆盖。这里的 `independent_story` 是基于这些键的工程代理，尚不是对通稿转载或事实独立性的语义核实。多个网站转载不能据此宣称有多个独立事实证据。

官媒覆盖只影响分类解释和官媒栏目；不增加知乎／微博的平台分，不以官方报道数量声称“全网热议”。

## 代码与验证

- 相关性与支持判定：[`official_support.py`](../backend/app/services/official_support.py)。
- query、议程与覆盖服务：[`official_paths.py`](../backend/app/services/official_paths.py)、[`official_search.py`](../backend/app/services/official_search.py)。
- 窗口规则：[`hotspot_freshness.py`](../backend/app/services/hotspot_freshness.py)。
- 历史检测和实测：[验证记录](../README.md#来源采样)。
