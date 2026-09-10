# 跨源热点分类与类内排序验证 v0.1

生成时间：2026-09-10

## 范围

本报告对应 Day 20：热点事件分类规则与类内排序验证。

目标不是实现正式 Collector，也不是生成跨平台统一热度分，而是基于 Day 15-19 已有真实采样结果，验证微博、知乎和官媒三类来源如何在事件层面连接、补证、分类和排序。

输入材料：

| 材料 | 作用 |
|---|---|
| `reports/weibo-rsshub-sampling-v0.1.md` | 微博热搜话题种子、RSSHub 字段边界 |
| `reports/weibo-cli-search-statuses-limited-live-v0.1.md` | 微博 CLI 搜索补强、正文 / 时间 / 转评赞 / total_number_proxy 边界 |
| `reports/weibo-to-zhihu-search-sampling-v0.1.md` | 微博 TopN 到知乎搜索补证的命中率和噪声 |
| `reports/zhihu-hotlist-search-sampling-v0.1.md` | 知乎 hot_list 与 zhihu_search 增强边界 |
| `reports/official-news-structure-probe-v0.1.md` | 官媒 RSS 证据源字段和曝光度代理边界 |

## 验证结论

- 可以把微博、知乎、官媒连接到同一个事件候选池，但连接单元必须是 `EventCandidate`，不是平台原始 item。
- 微博和知乎必须先做平台内 TopN，再做事件级匹配；不能把微博 rank、知乎 vote / comment、官媒 coverage 直接相加。
- 微博 RSSHub 只负责微博话题种子；微博 CLI 负责同一 topic 的正文、发布时间、转评赞和 `total_number_proxy` 补强。
- 知乎 `hot_list` 是知乎侧热点发现源；`zhihu_search` 是已有候选的低频讨论补证工具，不是独立热度源。
- 官媒 RSS 是事实证据源和权威支撑源；当前公开 RSS 没有稳定单篇阅读数 / 浏览数，不应把官媒当成热榜源。
- 本轮样本真实观察到 `C_cross_platform_without_official`、`E_single_platform_only`、`F_official_only`；未观察到 A/B/D，不应伪造正例。

## 连接模型

### 1. 统一事件候选

所有来源先标准化为 `SourceSignal`，再按事件合并成 `EventCandidate`。

```text
SourceSignal
  -> source_id / source_origin / source_status
  -> platform
  -> signal_role
  -> title / content_text / url / external_id
  -> published_at / fetched_at
  -> raw_metrics
  -> quality_flags

EventCandidate
  -> event_key
  -> canonical_title
  -> primary_platforms
  -> source_signals[]
  -> cross_platform_match_type
  -> official_support_status
  -> classification
  -> intra_class_sort_keys
  -> audit_flags[]
```

### 2. 事件键生成

第一阶段不依赖复杂模型，使用可审计的文本规则生成候选事件键：

```text
normalize(title/content)
  -> 去除微博话题井号、URL、标点、空格噪声
  -> 去除知乎常见提问壳：如何看待、怎样评价、为什么、是否、你怎么看
  -> 去除营销 / 榜单弱词：发布、售价、选择、信赖之选 等仅在低置信匹配中使用
  -> 保留实体、动作、结果、对象
  -> 生成 event_key_tokens
```

匹配优先级：

| 优先级 | 方法 | 说明 |
|---:|---|---|
| 1 | 平台 ID / URL 去重 | 同平台内部去重，避免同一知乎问题或同一微博重复进入候选 |
| 2 | 标题强包含 | A 标题完整包含 B 核心短语，或反向包含 |
| 3 | 实体 + 动作匹配 | 人名 / 机构 / 产品 / 地点 + 明确动作一致 |
| 4 | 关键词重合 | 只作为候选召回，必须结合时间窗口和人工审计字段 |
| 5 | 泛词匹配 | 如“苹果 华为”这类短 query，只能进入弱匹配或审计，不直接支撑分类 |

### 3. Agent 执行链路

Day20 后续实现建议采用混合匹配链路，不把相关性判断留给 LLM 主观决定：

```text
Collector Agent
-> Normalizer Agent
   -> build_event_text_for_match
   -> build_event_text_for_embedding
-> Event Resolver Agent
   -> hard_match_by_id_url
   -> bm25_ngram_retrieve_candidates
   -> embed_event_text
   -> semantic_retrieve_candidates
   -> rerank_event_matches
   -> match_event
   -> apply_merge_guardrails
   -> auto_merge / enqueue_human_review / reject
-> Classification Agent
```

当前采样基线是规则型匹配：标题 / query 强包含、question id 和 2-6 gram 关键词重合。后续正式实现中，BM25 / n-gram 负责可解释召回，embedding 负责语义召回或 rerank。

embedding 使用边界：

- embedding 可以补充同义改写、长短标题不一致和跨平台表达差异。
- embedding 不单独决定自动合并。
- 短话题、泛词话题和营销词话题必须同时满足实体、动作、对象或时间窗口硬约束。
- embedding 高相似但硬约束不足时进入人工复核；实体或时间冲突时拒绝合并并记录 `semantic_false_positive_risk`。

### 4. 时间窗口

| 来源组合 | 默认窗口 | 处理规则 |
|---|---:|---|
| 微博 RSSHub topic 与微博 CLI status | 24h | CLI status 的 `created_at` 代表样本微博发布时间，不代表热搜上榜时间 |
| 微博 topic 与知乎 search | 7d | 如果知乎结果 `edit_time` 明显陈旧或只命中历史泛内容，降为审计 |
| 知乎 hot_list 与微博 CLI search | 7d | 同上，微博搜索结果要保留最新样本时间和相关性原因 |
| 社区候选与官媒 RSS | 7d | 官媒 `published_at` 缺失时只能 `weak_supported`，不能强支撑时效 |
| 官媒内部多源覆盖 | 7d | 用于 `source_coverage_count` 和 `authority_weight_sum` |

## 平台内 TopN

### 微博平台内 TopN

微博侧 TopN 来源为 RSSHub `/weibo/search/hot` 话题种子，默认跳过 rank 1。

可用排序字段：

```text
list_position / rank
snapshot_presence_count
rank_delta
rsshub_fetched_at
cli_search_total_number_proxy
cli_time_sample_latest_created_at
cli_hot_sample_interaction_bucket
```

边界：

- `rank` 是 RSS item 顺序派生，不是官方结构化热度值。
- `total_number_proxy` 是搜索接口规模代理，不是全站发帖总量。
- CLI 转评赞是代表性微博样本指标，不是话题总互动。

### 知乎平台内 TopN

知乎侧 TopN 来源为官方 `hot_list`。

可用排序字段：

```text
hot_list_rank
snapshot_presence_count
rank_delta
fetched_at
retained_zhihu_search_count
retained_search_comment_bucket
retained_search_vote_bucket
ranking_score_bucket
```

边界：

- `hot_list` 未观察到热度值和发布时间，必须允许 `hot_value = null`、`published_at = null`。
- `zhihu_search` 的 comment / vote / ranking_score 只增强已有候选，不作为独立热榜发现源。

## 跨平台连接方法

### natural_topn_overlap

适用场景：同一事件同时出现在微博平台内 TopN 和知乎平台内 TopN。

判定规则：

```text
if weibo_topn_event_key matches zhihu_hot_list_event_key
and match_confidence >= high
then cross_platform_match_type = natural_topn_overlap
```

最低要求：

- 标题强包含，或实体 + 动作 + 对象三者一致。
- 泛词 topic 不能单独触发自然重合。
- 两侧采集时间应处于同一观察窗口。

本轮结果：

- Day 18 微博 TopN 与 Day 17 知乎 TopN 采样时间和话题不同，未观察到自然 TopN 重合样例。

### search_supported

适用场景：事件只在一个平台 TopN 中出现，但通过另一个平台搜索找到高相关讨论样本。

判定规则：

```text
if primary_topn_event exists
and retained_search_result_count >= 1
and search_result_relation in [same_question_id, title_contained, query_contained, entity_action_match]
then cross_platform_match_type = search_supported
```

降级规则：

- 只有关键词弱重合，且 query 是泛词，标记为 `weak_search_supported` 或仅进入审计。
- 搜索返回但无高相关保留结果，标记 `no_cross_platform_support_found`。
- 搜索工具失败时写 `zhihu_search_unavailable` 或 `weibo_cli_unavailable`，不阻塞主流程。

本轮微博到知乎搜索结果：

| 指标 | 结果 |
|---|---:|
| 微博话题搜索数 | 10 |
| 知乎返回结果 | 30 |
| 高相关保留 | 7 |
| 低相关拒绝 | 23 |
| 有知乎讨论命中的微博话题 | 4/10 |
| 搜索错误 | 0 |

### official_support

适用场景：社区候选匹配官媒 RSS 或官媒曝光度代理字段。

判定规则：

| 状态 | 条件 |
|---|---|
| `supported` | 官媒标题 / 摘要与事件键强匹配，且有有效 `published_at`，来源状态为 `use` |
| `weak_supported` | 官媒弱匹配、`published_at` 缺失、来源状态为 `fallback`，或只命中同类背景报道 |
| `not_found` | 当前官媒样本窗口内未找到相关报道 |

本轮结果：

- 官媒 RSS 样本与微博 / 知乎社区样本没有同事件重合。
- 中国新闻网 RSS 当前性最好，但样本主要是新闻活动类内容；微博 / 知乎样本偏社区议题、商业产品、娱乐和争议。
- 因此本轮不能给 A/B/D 生成真实正例，只能验证 `not_found` 和 `official_only` 路径。

## 热点分类规则

分类先看来源覆盖，再看搜索补证和官媒支撑。

| 分类 | 条件 | 解释口径 |
|---|---|---|
| `A_cross_platform_with_official` | 微博 TopN 与知乎 TopN 自然重合，且官媒 `supported` / `weak_supported` | 多社区平台共同关注，且有官方报道支撑 |
| `B_single_platform_with_search_and_official` | 单一平台 TopN，另一个社区平台搜索高相关命中，且官媒 `supported` / `weak_supported` | 一个平台上榜，另一个平台有讨论补证，且有官方报道 |
| `C_cross_platform_without_official` | 两个社区平台有自然重合或高相关搜索补证，但官媒 `not_found` | 社区跨平台讨论成立，但缺少官媒事实支撑 |
| `D_single_platform_with_official` | 单一平台 TopN，无高相关社区搜索补证，但官媒 `supported` / `weak_supported` | 一个社区平台关注，且有官方报道 |
| `E_single_platform_only` | 单一平台 TopN，无官媒支撑，无高相关跨平台搜索补证 | 平台内候选，只能低置信展示或进入观察池 |
| `F_official_only` | 只在官媒 RSS 出现，未匹配社区 TopN / 搜索补证 | 官方报道事件，社区关注未被当前样本捕捉 |

关键边界：

- `search_supported` 不等于自然双平台上榜；它是弱于 `natural_topn_overlap` 的跨平台补证。
- B 与 C 的区别是官媒是否支撑；B 有官媒，C 无官媒。
- D 与 F 的区别是 D 至少有一个社区平台 TopN，F 只有官媒。
- E 不能写成“全网热议”，只能写成“某平台出现相关热点候选”。

## 类内排序规则

类内排序使用字典序 sort keys，不生成跨平台总分。

通用 sort keys：

```text
official_support_rank      # supported > weak_supported > not_found
match_strength_rank        # natural_topn_overlap > search_supported > weak_search_supported > none
primary_rank_bucket        # top3 > top10 > top20 > tail
snapshot_presence_count    # 多快照出现次数，desc
rank_delta_direction       # rising > stable > falling > unknown
search_hit_quality_rank    # same_id/title_contained > entity_action_match > keyword_overlap > none
freshness_bucket           # <=24h > <=72h > <=7d > stale > unknown
source_health_rank         # use > fallback > unavailable
```

分类内建议：

| 分类 | 排序优先级 |
|---|---|
| A | 官媒支撑强弱、自然重合质量、两平台 rank bucket、时效性 |
| B | 官媒支撑强弱、搜索命中质量、主平台 rank bucket、搜索互动样本完整度 |
| C | 自然重合优先于搜索补证、搜索命中质量、主平台 rank bucket、噪声 flags 少者优先 |
| D | 官媒支撑强弱、主平台 rank bucket、官媒发布时间新鲜度、来源权重 |
| E | 主平台 rank bucket、平台内出现次数、CLI / search 补强可用性、噪声 flags 少者优先 |
| F | 官媒来源权重、报道时间新鲜度、source_coverage_count、字段完整度 |

禁止项：

- 禁止把 `weibo_rank + zhihu_vote_count + official_source_count` 合成总分。
- 禁止把 `zhihu_search` 的互动数描述为知乎热榜热度。
- 禁止把微博 CLI `total_number_proxy` 描述为全站讨论量。
- 禁止把 RSSHub item 顺序描述为微博官方热度值。

## 样本分类探针

本节只使用已真实采样的数据，不补造 A/B/D 正例。

| 事件候选 | 主来源 | 补证 | 官媒支撑 | 分类 | 说明 |
|---|---|---|---|---|---|
| `太子奶创始人李途纯去世` | 微博 RSSHub rank 3 | 知乎搜索保留 3 条；微博 CLI 返回 10 条，`total_number_proxy=957` | `not_found` | `C_cross_platform_without_official` | 微博 TopN + 知乎高相关搜索补证成立，但本轮官媒 RSS 样本未命中 |
| `为什么子女买房会把父母安排在次卧` | 微博 RSSHub rank 6 | 知乎搜索保留 2 条 | `not_found` | `C_cross_platform_without_official` | 跨社区讨论存在，事实证据弱，应作为讨论型事件处理 |
| `史上最牛烧砖工` | 微博 RSSHub rank 4 | 知乎搜索保留 1 条、拒绝 2 条 | `not_found` | `C_cross_platform_without_official` | 保留结果少且有噪声，类内排序应低于强匹配 C 类 |
| `网红宣传捐款百万实际只捐1元` | 微博 RSSHub rank 7 | 知乎搜索保留 0 条；微博 CLI 返回 10 条，`total_number_proxy=81161` | `not_found` | `E_single_platform_only` | 微博侧有 topic 和 CLI 样本，但知乎搜索未保留，不能算跨平台 |
| `吃播圈催吐导泄都造成血钾暴跌` | 微博 RSSHub rank 10 | 知乎搜索保留 0 条；微博 CLI 返回 10 条，`total_number_proxy=5543` | `not_found` | `E_single_platform_only` | 微博单平台观察候选，CLI 可增强微博侧证据但不构成知乎支撑 |
| `全国各地古镇相似度高达99%` | 知乎 hot_list rank 1 | 知乎 search 保留 3 条 | `not_found` | `E_single_platform_only` | 知乎平台内热点候选，search 只是知乎内部增强，不算跨平台 |
| `工业互联网+绿色低碳融合创新发展论坛在沈阳举办` | 中国新闻网 RSS | 无社区命中 | `supported` | `F_official_only` | 官媒报道事件，当前社区样本未捕捉到讨论 |
| `微视频｜“新”在中国` | 新华网 RSS fallback | 无社区命中 | `weak_supported` | `F_official_only` | 官媒 fallback 且缺少 `published_at`，只能弱支撑 |

## 字段边界和降级规则

微博 RSSHub：

```text
source_status = use
source_origin = rsshub
signal_role = attention_signal / topic_discovery_signal
default_route = /weibo/search/hot
default_topn = rank 2-11
quality_flags = missing_pub_date, missing_hot_value, rsshub_rank_derived
failure = skip_weibo_rsshub, do_not_block_zhihu_or_official
```

微博 CLI：

```text
source_status = use
source_origin = weibo_cli
signal_role = attention_signal / discussion_sample_signal
default_budget = per_topic sort=time count<=10, optional sort=hot count<=10-20
raw_metrics = comments_count, reposts_count, attitudes_count, total_number_proxy
quality_flags = search_result_total_proxy, query_driven_sample, low_related_status_rejected
failure = weibo_cli_unavailable
```

知乎：

```text
hot_list.source_status = use
hot_list.signal_role = discussion_focus_signal / attention_signal
zhihu_search.signal_role = discussion_enrichment_signal
default_hot_list_limit = 20 or 30
default_search_count = 3 or 5 for TopK candidates
quality_flags = missing_hot_value, missing_published_at, zhihu_search_noise_detected
failure = keep_hot_list_signal, skip_search_metrics
```

官媒：

```text
source_status = use / fallback by source
signal_role = evidence_signal
raw_metrics = null by default
support_fields = published_at, source_name, source_authority_weight, source_coverage_count
quality_flags = missing_published_at, missing_author, no_raw_metrics, stale_feed_candidate
failure = official_support_status_not_found_or_unknown
```

## Day 22 / Day 23 输入

Day 22 需要固化的字段：

```text
NormalizedItem.source_origin
NormalizedItem.signal_role
NormalizedItem.score_contribution_role
NormalizedItem.raw_metrics
NormalizedItem.quality_flags
NormalizedItem.event_text_for_match
NormalizedItem.event_text_for_embedding
SourceSignal.parent_event_key
SourceSignal.match_relation
SourceSignal.match_confidence
SourceSignal.audit_only
EventResolution.matched_by
EventResolution.match_features_json
```

Day 23 需要固化的结构：

```text
EventCandidate
EventSignal
CrossPlatformMatch
OfficialSupport
ClassificationResult
IntraClassSortKey
SearchEnrichmentAudit
EventMatchRetrieval
RerankEventMatches
```

建议新增枚举：

```text
cross_platform_match_type = natural_topn_overlap | search_supported | weak_search_supported | none | weibo_cli_unavailable | zhihu_search_unavailable
official_support_status = supported | weak_supported | not_found | unknown
classification = A_cross_platform_with_official | B_single_platform_with_search_and_official | C_cross_platform_without_official | D_single_platform_with_official | E_single_platform_only | F_official_only
match_confidence = high | medium | low | rejected
```

## 决策

- Day20 规则可以支撑微博、知乎和官媒的数据连接设计。
- 混合匹配链路已沉淀为 `docs/event-matching.md`：规则和 BM25 / n-gram 做可解释召回，embedding 做语义召回或 rerank，中置信进入人工复核。
- 第 4 周进入 schema 设计时，应把“连接证据”和“热度排序”分开建模。
- 第 5 周 Collector 实现时，默认只产出 raw signal 和分类证据链，不产出跨平台统一热度分。
- A/B/D 的真实样例需要后续多轮同窗口采集验证；当前不能用本轮样本证明这些类型的命中率。
