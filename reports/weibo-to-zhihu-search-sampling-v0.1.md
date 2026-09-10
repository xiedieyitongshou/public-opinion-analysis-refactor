# 微博热榜到知乎搜索增强采样 v0.1

生成时间：`2026-09-08T09:24:58.976629+00:00`

## 范围

Day 18 组合路径测试：先用 RSSHub `/weibo/search/hot` 获取微博热榜话题，
再用微博话题标题调用知乎官方 `zhihu_search` API，验证这些微博热榜话题在知乎是否存在可用讨论信号。

本次实验只是 source feasibility test，不等于正式接入决策。是否在后续 collector 中使用，
取决于微博 RSSHub 稳定性、知乎搜索命中率、相关性过滤后的保留率和互动字段完整率。

## 方法

- RSSHub base URL：`http://localhost:1200`
- RSSHub route：`/weibo/search/hot`
- 微博抓取上限：`20`
- 跳过微博热榜前 `1` 条：第 1 条通常可能是置顶 / 宣传位，不作为自然热度样本。
- 进入知乎搜索的话题数：`10`，本次为微博 rank 2 起的连续话题。
- 每个微博话题的 `zhihu_search` 返回上限：`3`
- 原始本地样本：`D:\desktop\public-opinion-analysis-refactor\notes\source-probe-raw\weibo-to-zhihu-search-sampling.json`
- 相关性过滤：保留标题 / 内容强包含、query 强包含或关键词重合度高的结果。

## 结果汇总

| Stage | Count / rate | 说明 |
|---|---:|---|
| RSSHub 微博热榜返回 item | 20 | 基础热搜列表可解析 |
| 跳过微博热榜 item | 1 | 跳过 rank 1 |
| 调用知乎搜索的话题 | 10 | 使用微博 title 作为 query |
| 知乎返回结果 | 30 | 所有 `zhihu_search` 返回条目 |
| 高相关保留结果 | 7 | 可作为知乎讨论补充信号 |
| 低相关拒绝结果 | 23 | 只进入审计，不参与评分 |
| 有知乎讨论命中的微博话题 | 4/10 | 至少 1 条高相关知乎结果 |
| 知乎搜索错误 | 0 | API 或网络失败 |

## 微博 RSSHub 获得的数据

| 字段 | 完整率 | 是否可用于热度 |
|---|---:|---|
| `title` | 100% | 可用于话题发现和知乎 query |
| `url` / `link` | 100% | 可用于来源引用 / 人工核验 |
| `description` | 100% | 本次通常等于标题，信息增量弱 |
| `rank` | 100% | 由 RSS item 顺序派生，可做弱 attention signal |
| `published_at` / `pubDate` | 0% | 本次不可用 |
| `hot_value` | 0% | 本次不可用 |

## 知乎搜索补充获得的数据

| 字段 | 完整率 | 是否可用于热度 |
|---|---:|---|
| `title` | 100% | 用于相关性判断 |
| `url` | 100% | 可用于来源引用 / 去重 |
| `content_type` | 100% | 可用于区分问题 / 回答 / 内容类型 |
| `content_id` | 100% | 可用于平台内去重 |
| `comment_count` | 100% | 可作为讨论强度 raw feature |
| `vote_up_count` | 100% | 可作为互动强度 raw feature |
| `edit_time` | 100% | 可作为 freshness raw feature |
| `ranking_score` | 100% | 可作为知乎搜索排序 raw feature |

## 话题级命中结果

| Weibo rank | Weibo topic | Query | Zhihu returned | Retained | Rejected | Quality flags |
|---:|---|---|---:|---:|---:|---|
| 2 | `梅姨年龄籍贯曝光` | `梅姨年龄籍贯曝光` | 3 | 0 | 3 | no_zhihu_discussion_found, zhihu_search_noise_detected |
| 3 | `太子奶创始人李途纯去世` | `太子奶创始人李途纯去世` | 3 | 3 | 0 | none |
| 4 | `史上最牛烧砖工` | `史上最牛烧砖工` | 3 | 1 | 2 | zhihu_search_noise_detected |
| 5 | `苹果 华为` | `苹果 华为` | 3 | 1 | 2 | zhihu_search_noise_detected |
| 6 | `为什么子女买房会把父母安排在次卧` | `子女买房会把父母安排在次卧` | 3 | 2 | 1 | zhihu_search_noise_detected |
| 7 | `网红宣传捐款百万实际只捐1元` | `网红宣传捐款百万实际只捐1元` | 3 | 0 | 3 | no_zhihu_discussion_found, zhihu_search_noise_detected |
| 8 | `华为乾崑200万用户的信赖之选` | `华为乾崑200万用户的信赖之选` | 3 | 0 | 3 | no_zhihu_discussion_found, zhihu_search_noise_detected |
| 9 | `旅行青蛙即将停运` | `旅行青蛙即将停运` | 3 | 0 | 3 | no_zhihu_discussion_found, zhihu_search_noise_detected |
| 10 | `吃播圈催吐导泄都造成血钾暴跌` | `吃播圈催吐导泄都造成血钾暴跌` | 3 | 0 | 3 | no_zhihu_discussion_found, zhihu_search_noise_detected |
| 11 | `栾念在西藏撒谎了` | `栾念在西藏撒谎了` | 3 | 0 | 3 | no_zhihu_discussion_found, zhihu_search_noise_detected |

## 高相关知乎结果样例

| Weibo rank | Weibo topic | Zhihu title | Comments | Upvotes | Ranking score | Relation reasons |
|---:|---|---|---:|---:|---:|---|
| 3 | `太子奶创始人李途纯去世` | `太子奶创始人李途纯去世,曾以8888万夺央视「标王」,被拘禁15个月后获无罪释放,如何评价他的一生? - 知乎` | 0 | 1 | 2.4684181 | topic_title_contained, query_contained, keyword_overlap_high |
| 3 | `太子奶创始人李途纯去世` | `太子奶创始人李途纯去世,曾以8888万夺央视「标王」,被拘禁15个月后获无罪释放,如何评价他的一生? - 知乎` | 0 | 0 | 1.9666171 | topic_title_contained, query_contained, keyword_overlap_high |
| 3 | `太子奶创始人李途纯去世` | `太子奶创始人李途纯去世,曾以8888万元夺央视“标王” - 知乎` | 0 | 0 | 1.9129844 | topic_title_contained, query_contained, keyword_overlap_high |
| 4 | `史上最牛烧砖工` | `合肥与中科大:一个穷省和一所“落难”大学,把彼此宠上巅峰 - 知乎` | 1 | 10 | 1.4651988 | topic_title_contained, query_contained, keyword_overlap_high |
| 5 | `苹果 华为` | `苹果、华为、小米、OV,到底谁家的Care+最厚道? - 知乎` | 1 | 14 | 2.322819 | topic_title_contained, query_contained, keyword_overlap_high |
| 6 | `为什么子女买房会把父母安排在次卧` | `为什么子女买的房子,一般都会把父母安排在次卧? - 知乎` | 69 | 434 | 1.2310618 | keyword_overlap_high |
| 6 | `为什么子女买房会把父母安排在次卧` | `知乎热榜日报 \| 2026年4月13日 - 知乎` | 0 | 9 | 1.226297 | keyword_overlap_high |

## 相关性过滤观察

- 相关性原因统计：`{'topic_title_contained': 5, 'query_contained': 5, 'keyword_overlap_high': 7}`
- 高相关样例表只展示知乎标题；部分命中可能来自 `ContentText`，后续 collector
  需要保存 relation reasons 以便审计。
- 只有高相关保留结果可以参与后续事件评分。
- 低相关结果只保留在审计日志中，避免微博短标题误命中知乎历史内容。
- 未命中知乎讨论的话题仍可保留微博 `rank` / `topic_present`，但必须标记 `no_zhihu_discussion_found`。

## 与单独微博 RSSHub 方案对比

单独微博 RSSHub 多数只能提供话题标题、链接和派生 rank，不能提供阅读量、发帖数、点赞、
评论、转发或稳定 `hot_value`。组合路径可以用知乎 `zhihu_search` 补到部分讨论和互动指标，
但这些指标代表“知乎上的相关讨论”，不是微博自身热度。

## 为什么不采用 RSSHub 作为正式微博热度源

本次实验说明，RSSHub 微博热搜适合作为当前微博热点获取链路的话题种子；它不适合单独作为微博真实热度数据源。

主要原因：

- 字段不足：RSSHub `/weibo/search/hot` 本次只稳定提供 `title`、`url`、`description` 和 `guid`；其中 `description` 通常等于 `title`，缺少真正的话题背景。
- 热度指标缺失：本次没有观察到微博侧阅读量、发帖数、讨论量、点赞、评论、转发或稳定 `hot_value`。
- 时间字段缺失：本次没有观察到 item 级 `pubDate`，只能用采集时刻 `fetched_at` 记录快照时间。
- rank 不是官方字段：报告中的 `rank` 由 RSS item 顺序派生，不是微博结构化排名字段。
- 稳定性不足：RSSHub 是第三方转换层，公共实例不可作为生产依赖；自建实例也依赖 RSSHub 路由实现和微博上游页面 / 接口稳定性。
- 语义不足：微博热榜 title 多数是短话题词，不是完整事件描述，直接进入事件抽取会增加歧义、误合并和误评分风险。
- 跨平台补充效果有限：本次微博 rank 2-11 调用知乎搜索，只有 `4/10` 个话题命中高相关知乎讨论，高相关保留结果为 `7/30`，低相关拒绝结果为 `23/30`。

因此，RSSHub + CLI 组合可进入 `source_status = use`，但不承担微博真实全量热度主评分。

## 是否保留 RSSHub

保留。RSSHub 是当前微博热点获取链路的话题种子来源，CLI 是同一链路下的内容和互动补强来源。

推荐状态：

```text
weibo_rsshub_hot_search.source_status = use
weibo_rsshub_hot_search.signal_role = topic_discovery_signal
weibo_rsshub_hot_search.score_contribution = weak_attention_only
```

保留理由：

- 它可以低成本发现微博正在出现的话题，尤其是官媒和知乎未必及时覆盖的话题。
- 它可以作为低成本微博话题种子输入，供后续微博 CLI 搜索补强。
- 它适合做微博热点话题发现、采样、debug 和跨源候选生成。

限制条件：

- 默认不进入核心热度评分，只提供 `topic_present`、派生 `rank`、快照出现次数和 `rank_delta`。
- 不命中知乎、官媒或其它证据源时，不自动生成高置信事件。
- 在日报输出中只能描述为“微博热榜出现相关话题”，不能描述为“微博高热度”或“全网热议”。
- 如果连续多轮 RSSHub 失败，直接跳过微博 RSSHub，不阻塞知乎和官媒链路。

重新评估条件：

- 多轮采样后 RSSHub 成功率持续低。
- CLI 补强长期不可用或相关性保留率持续过低。
- RSSHub 维护成本高于它提供的话题发现价值。
- 已验证有更稳定、低风险且字段更完整的微博热点获取方式。

## 转向微博 CLI 补强

微博侧统一收敛为低成本组合链路：

```text
RSSHub 热搜话题种子
-> 微博 CLI search/statuses/limited 搜索相关微博
-> 可选 CLI 评论 / 转发补强代表性微博
```

目标字段：

```text
topic
list_position
status_id / mid
status_text
created_at
comments_count
reposts_count
attitudes_count
total_number proxy
matched_status_count
```

工程判断：

- RSSHub 负责发现微博热搜话题，但只输出弱 `topic_present` / `list_position` 信号。
- 微博 CLI 负责对已知 topic 做搜索补强，获取代表性微博的正文、发布时间和转评赞字段。
- CLI 搜索仍是 query-driven，不代表热搜话题下全量微博，也不代表真实全站热度。
- 搜索结果必须经过相关性过滤，低相关微博只进入审计日志。
- CLI 未登录、额度不足或命令不可用时，微博链路降级为 RSSHub-only 或跳过，不阻塞知乎和官媒链路。

建议：

- 保留 RSSHub 作为当前微博热点话题种子。
- 新增或继续维护微博 CLI smoke test，重点验证 `search/statuses/limited`、评论和转发补强字段。
- MVP 中微博侧输出必须标记为 RSSHub + CLI 样本信号，不能表述为微博真实全量热度。

## 与官媒 RSS 对比

组合路径多了：

- 微博热榜提供社区即时话题发现，能覆盖官媒未报道或尚未报道的话题。
- 知乎搜索补充提供 `comment_count`、`vote_up_count`、`ranking_score`、`edit_time` 等可量化讨论特征。
- 微博 rank 与知乎互动字段结合后，可以比单独 RSSHub 更适合做社区热度试算。

组合路径少了或更弱：

- 仅 RSSHub 路径仍没有阅读量、发帖数、点赞、评论、转发和稳定 `hot_value`；需要微博 CLI 补充代表性微博的转评赞样本。
- 知乎搜索命中的是跨平台相关讨论，不等于微博话题本身的热度。
- 微博短标题可能导致知乎搜索噪音，必须依赖相关性过滤。
- 官媒 RSS 更适合事实确认、发布时间、事件时间线和权威引用；组合路径不能替代事实来源。

## Schema 影响

- 微博 RSSHub 条目映射为 `attention_signal`，保留 `list_position`、`topic_present`、`fetched_at`。
- 知乎搜索结果作为微博话题候选下的 `discussion_signal` / enrichment signal。
- 事件评分中可使用知乎 `comment_count`、`vote_up_count`、`ranking_score` 和 `edit_time`，但必须标明来源是知乎讨论补充。
- 未命中知乎讨论时，不补造讨论热度；只保留微博弱话题发现信号并降低置信度。
- 微博 rank、知乎互动数、官媒报道数量仍然不能直接相加，必须先按来源归一化。

## 决策建议

- 本组合路径可作为当前微博热点获取方式进入平台内 TopN 和分类证据链，但不能作为微博真实全量热度进入核心评分。
- Day 33 实现微博 RSSHub + CLI collector；RSSHub 负责话题种子，CLI 负责搜索和互动补强。
- 默认跳过微博热榜 rank 1。
- 默认配置建议：微博 rank 2-11、每个微博话题 `zhihu_search` 返回 3 条。
- 多轮采样后的 RSSHub 成功率、CLI 相关性拒绝率和额度消耗用于调整调用预算、置信度和降级策略，不再决定是否作为当前微博获取方式接入。
- 微博侧正式实现维护 RSSHub 话题种子 + 微博 CLI 搜索补强链路；评论 / 转发补强作为后续可选扩展。

## 后续匹配设计补充

本报告中的相关性过滤是规则型基线，主要依赖标题 / query 强包含和 2-6 gram 关键词重合，未使用 embedding。

后续正式事件匹配采用 `docs/event-matching.md` 中的混合链路：

```text
规则强匹配
-> BM25 / n-gram 可解释召回
-> embedding 语义召回或 rerank
-> Guardrails 判断自动合并、人工复核或拒绝
```

微博短话题容易语义漂移，embedding 高相似不能单独证明同一事件；必须结合实体、动作、对象、时间窗口或来源证据。中置信度结果进入人工复核，低相关搜索结果继续只写审计日志。
