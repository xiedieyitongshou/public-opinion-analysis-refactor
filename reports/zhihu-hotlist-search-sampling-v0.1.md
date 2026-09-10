# 知乎热榜搜索增强采样 v0.1

生成时间：`2026-09-08T02:54:36.370492+00:00`

## 范围

Day 17 针对知乎官方 `hot_list` 与低频 `zhihu_search` 增强做 smoke test。

本次运行使用知乎数据开放平台 JSON API 和本地凭据。原始样本仅本地保留，不适合提交到公开仓库。

## 方法

- `hot_list` 请求上限：`10`
- `zhihu_search` 增强候选：热榜 top `5` 条目
- 每个候选的 `zhihu_search` 返回数量：`3`
- 搜索 query 策略：移除通用提问表述和标点，并截取较短的核心短语。
- 相关性过滤：保留 question id 匹配、标题 / query 强包含或关键词重合度较高的结果。
- 原始本地样本：`D:\desktop\public-opinion-analysis-refactor\notes\source-probe-raw\zhihu-hotlist-search-sampling.json`
- 补充采样：`2026-09-09T12:02:28.958441+00:00` 对最新 `hot_list` top `5` 条目分别调用 `zhihu_search Count=5` 和 `Count=10`，用于验证搜索结果是否随机、`Count=10` 是否包含 `Count=5`。
- 补充采样原始本地样本：`D:\desktop\public-opinion-analysis-refactor\notes\source-probe-raw\zhihu-hotlist-search-count5-count10-sampling.json`

## 结果汇总

| API | 调用次数 | 条目 / 结果数 | 保留 | 拒绝 | 主要作用 |
|---|---:|---:|---:|---:|---|
| `hot_list` | 1 | 10 | 10 | 0 | 模式 A 的知乎热点候选发现 |
| `zhihu_search` | 5 | 15 | 15 | 0 | 对已有候选做低频互动 / 证据增强 |

## hot_list 已观察数据

- 返回条目数：`10`
- 返回 total：`10`
- 稳定观察到的 item 字段：
- `Title` 在 `10/10` 个 items 中存在
- `Url` 在 `10/10` 个 items 中存在
- `ThumbnailUrl` 在 `10/10` 个 items 中存在
- `Summary` 在 `10/10` 个 items 中存在
- 项目可派生字段：从数组顺序生成 `rank`，从 Collector 时间生成 `fetched_at`。
- 缺失字段：未观察到 `hot_value`，未观察到 item 级 `published_at`，未观察到 author / comment / vote 指标。

## zhihu_search 已观察数据

- 增强候选数：`5`
- 搜索错误数：`0`
- 高相关保留结果：`15`
- 低相关拒绝结果：`0`
- 返回搜索结果中稳定观察到的字段：
- `title` 在 `15/15` 个 results 中存在
- `content_type` 在 `15/15` 个 results 中存在
- `content_id` 在 `15/15` 个 results 中存在
- `url` 在 `15/15` 个 results 中存在
- `comment_count` 在 `15/15` 个 results 中存在
- `vote_up_count` 在 `15/15` 个 results 中存在
- `edit_time` 在 `15/15` 个 results 中存在
- `ranking_score` 在 `15/15` 个 results 中存在
- `authority_level` 在 `15/15` 个 results 中存在
- 可用的 raw signal feature：
- `CommentCount`：`15/15` 个 results
- `VoteUpCount`：`15/15` 个 results
- `RankingScore`：`15/15` 个 results
- `EditTime`：`15/15` 个 results

## 候选级增强结果

| Rank | Query | 保留 | 拒绝 | Quality flags |
|---:|---|---:|---:|---|
| 1 | `全国各地古镇相似度高达99连特色小吃都一模一样会出现这一` | 3 | 0 | none |
| 2 | `小米澎程N70系列增程SUV发布售价2099万元起怎样看` | 3 | 0 | none |
| 3 | `国产偶像剧都喜欢把男女主的工作背景设定在广告新闻公关等传` | 3 | 0 | none |
| 4 | `美网女单第四轮郑钦文20斯瓦泰克挺进8强本场比赛` | 3 | 0 | none |
| 5 | `毛阿敏要在镜头面前把许晴逼到崩溃` | 3 | 0 | none |

## search count 5 / 10 重叠验证

补充采样对最新知乎热榜 top `5` 条目分别调用 `zhihu_search Count=5` 和 `Count=10`。本轮调用消耗 `hot_list` quota `1` 次、`zhihu_search` quota `10` 次。

本轮 `Count=10` 对 5 个 query 均实际返回 `10` 条；`Count=5` 的全部返回结果都被 `Count=10` 包含，且完全等于 `Count=10` 的前 5 条。因此可以验证：在本轮样本中，`hot10` 能包住 `hot5`。

| Rank | Query | Returned 5 | Returned 10 | Count5 in Count10 | Count5 is Count10 prefix | Retained 5 | Retained 10 | Min comments | Max comments | Min votes | Max votes | Count10 relatedness reasons |
|---:|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---|
| 1 | `高考数学132分的学生在西电数学开学考只考12分33分竟` | `5` | `10` | `5/5` | `true` | `3` | `3` | `0` | `131` | `0` | `850` | `same_question_id=3, candidate_title_contained=3, query_contained=3, keyword_overlap_high=3` |
| 2 | `8月新能源车零售1005万辆同比下降101燃油车零售54` | `5` | `10` | `5/5` | `true` | `4` | `5` | `0` | `1` | `0` | `9` | `same_question_id=3, candidate_title_contained=4, query_contained=4, keyword_overlap_high=5` |
| 3 | `网友称欧洲西瓜硬到要用锯子切为啥西瓜看起来这么硬跟我们种` | `5` | `10` | `5/5` | `true` | `3` | `3` | `0` | `582` | `0` | `2848` | `same_question_id=3, candidate_title_contained=3, query_contained=3, keyword_overlap_high=3` |
| 4 | `星宇股份已获港股上市备案却三周仍无聆讯日程受此次裁员风波` | `5` | `10` | `5/5` | `true` | `3` | `3` | `0` | `77` | `0` | `922` | `same_question_id=3, candidate_title_contained=3, query_contained=3, keyword_overlap_high=3` |
| 5 | `南阳老头乐被禁止上路怎样看待这一规定该如何平衡老年人出行` | `5` | `10` | `5/5` | `true` | `3` | `3` | `0` | `335` | `0` | `778` | `same_question_id=3, candidate_title_contained=3, query_contained=3, keyword_overlap_high=3` |

指标对比：

| Rank | Query mode | Returned | Min comments | Max comments | Sum comments | Min votes | Max votes | Sum votes | Min ranking_score | Max ranking_score |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | count 5 | `5` | `0` | `131` | `203` | `0` | `850` | `1106` | `1.90920` | `2.36938` |
| 1 | count 10 | `10` | `0` | `131` | `217` | `0` | `850` | `1125` | `0.615267` | `2.36938` |
| 2 | count 5 | `5` | `0` | `1` | `1` | `0` | `7` | `11` | `1.27368` | `1.73731` |
| 2 | count 10 | `10` | `0` | `1` | `2` | `0` | `9` | `20` | `1.07341` | `1.73731` |
| 3 | count 5 | `5` | `0` | `92` | `93` | `0` | `290` | `297` | `0.634424` | `1.96556` |
| 3 | count 10 | `10` | `0` | `582` | `682` | `0` | `2848` | `3186` | `0.407109` | `1.96556` |
| 4 | count 5 | `5` | `8` | `69` | `114` | `41` | `578` | `900` | `1.57790` | `2.33976` |
| 4 | count 10 | `10` | `0` | `77` | `198` | `0` | `922` | `1859` | `1.47153` | `2.33976` |
| 5 | count 5 | `5` | `0` | `6` | `6` | `0` | `3` | `9` | `1.29267` | `1.84759` |
| 5 | count 10 | `10` | `0` | `335` | `348` | `0` | `778` | `795` | `1.06834` | `1.84759` |

观察：

- 本轮 `Count=5` 的 `5/5` 条均被 `Count=10` 包含，且完全等于 `Count=10` 的前 5 条；因此 `zhihu_search` 结果不像随机抽样，更像按知乎搜索排序稳定返回。
- 本轮 `Count=10` 均能返回额外第 6-10 条，但新增结果主要增加召回宽度，不等同于稳定增加高相关互动信号；5 个 query 中只有 Rank 2 的保留结果从 `4` 增至 `5`。
- `ranking_score` 与返回顺序高度一致，本轮每个 query 的 `Count=10` 返回结果中 `ranking_score` 基本随 `search_rank` 递减；它更像知乎搜索排序分，而不是热榜问题本身的热度值。
- `comment_count` 和 `vote_up_count` 与搜索排序相关但不是严格排序依据。Rank 3 和 Rank 5 的 `Count=10` 后半段出现高互动历史 / 低相关内容，不能直接参与该热榜问题评分。
- 工程上可以把 `Count=5` 作为更保守的低频增强默认值；当需要更宽召回时再使用 `Count=10`，但必须继续执行相关性过滤。

## 相关性过滤说明

- 已观察到的相关性原因：`{'same_question_id': 15, 'candidate_title_contained': 15, 'keyword_overlap_high': 15, 'query_contained': 6}`
- 被保留的结果可以为已有 `hot_list` 事件候选提供知乎侧互动特征。
- 被拒绝的结果只应保留在审计日志中，不能参与事件评分。
- 如果某个候选没有任何保留的搜索结果，该事件仍应保留 `hot_list` rank 信号，并跳过 search 派生的互动指标。

## 与官媒 RSS 对比

对比对象为 Day 15 官媒 RSS 探测中的 人民网、中国新闻网、新华网。

知乎多了：

- 通过 `hot_list` rank 提供社区侧热点发现能力。
- 通过 `zhihu_search` 提供讨论 / 互动指标：comments、upvotes、ranking score、edit time、content type、content id，以及存在时的 author metadata。
- 问题 / 社区 URL 可以作为稳定的平台 ID，用于事件匹配。
- 更适合捕捉公众注意力和讨论焦点，尤其是平台、消费、娱乐和争议类话题。

知乎少了或更弱的地方：

- 未观察到 `hot_list` 发布时间；RSS 新闻源通常提供 `published_at`，但当前新华网 fallback endpoint 例外。
- 未观察到 `hot_list` 平台热度值；必须使用 rank，而不是真实 heat metric。
- 作为事实证据的权威性弱于官媒 RSS；知乎不应被视为主要事实来源。
- query-driven 的 `zhihu_search` 可能引入历史内容污染和同关键词噪音，因此需要相关性闸门。

官媒 RSS 多了：

- 更强的事实引用和 authority / evidence 作用。
- 人民网和中国新闻网提供更稳定的发布时间和新闻式摘要。
- 更适合事件确认、时间线构建和来源覆盖指标。

官媒 RSS 少了：

- 已观察 RSS 样本中没有平台互动指标。
- 没有原生 rank 或热榜位置。
- 对社区先发话题，在官方媒体报道前覆盖较弱。

## Schema 影响

- `hot_list` 应映射为候选级 `discussion_focus_signal` / `attention_signal`，包含 `rank`、`total`、`fetched_at`，并允许 `published_at` / `hot_value` 为空。
- `zhihu_search` 应映射为挂在已有事件候选下的子级 / 增强型 `SourceSignal`，不能作为独立的模式 A 发现源。
- `quality_flags` 必须包含 `no_high_related_search_result`、`search_noise_detected`、`missing_edit_time` 以及缺失指标类 flag。
- 评分只应在相关性过滤后使用被保留的 `zhihu_search` 指标；否则只使用 `hot_list` rank 信号。

## 决策

- 保持 `hot_list` 为 `source_status = use`，用于模式 A 的知乎热点发现。
- 保持 `zhihu_search` 为 Top K `hot_list` 候选的低频增强工具，并作为未来模式 B 的证据工具。
- 不要把 `zhihu_search` 指标直接转换为独立的最终知乎热度分；应保留为 raw engagement features，供后续归一化使用。

## 后续匹配设计补充

本报告中的 `zhihu_search` 相关性过滤是规则型基线，主要依赖 question id、标题 / query 强包含和 2-6 gram 关键词重合，未使用 embedding。

后续正式事件匹配采用 `docs/event-matching.md` 中的混合链路：

```text
ID / URL / question_id 硬匹配
-> BM25 / n-gram 可解释召回
-> embedding 语义召回或 rerank
-> Guardrails 判断自动合并、人工复核或拒绝
```

知乎问题标题通常比微博话题更完整，但仍可能存在历史内容污染、同名不同事件和泛词误命中。embedding 只能作为二阶段召回或重排信号，不能绕过实体、动作、对象和时间窗口约束。


