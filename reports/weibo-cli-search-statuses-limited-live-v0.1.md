# 微博 CLI 搜索补强采样 v0.1

生成时间：`2026-09-09T03:52:22.602741+00:00`

## 范围

本报告基于已登录并通过开发者认证的微博 CLI 体验服务，对 `search/statuses/limited` 做低频 smoke test。

本次只验证微博 CLI 对已知关键词的搜索补强能力，不验证微博热搜主榜采集能力。微博热搜种子继续由 RSSHub 或其它低成本候选生成器提供。

原始样本仅本地保留，不适合提交到公开仓库；报告只保留结构、字段完整率和短样例，避免长期保存大规模微博原文。

## 方法

- CLI 命令：`npx -y @weibo-ai/weibo-cli@latest search statuses/limited --q <topic> --type 1 --count 10 --output json`
- 搜索类型：`--type 1`，即微博正文搜索。
- 每个关键词返回数量：`--count 10`
- 查询关键词数：`3`
- 原始样本目录：`D:\desktop\public-opinion-analysis-refactor\backend\data\weibo-cli-live-samples`
- 汇总样本：`D:\desktop\public-opinion-analysis-refactor\backend\data\weibo-cli-live-samples-summary.json`
- CLI search 命令目录：`D:\desktop\public-opinion-analysis-refactor\backend\data\weibo-cli-search-commands.json`
- RSSHub 本地状态：本次未启动 `http://localhost:1200/weibo/search/hot`，因此未生成真实 RSSHub 热搜种子链路样本。

## 结果汇总

| CLI command | 调用次数 | 条目 / 结果数 | 成功 | 失败 | 主要作用 |
|---|---:|---:|---:|---:|---|
| `search/statuses/limited` | `3` | `30` | `3` | `0` | 对已有微博话题做正文、时间和互动字段补强 |

说明：

- RSSHub 和微博 CLI 是两条不同数据路径。RSSHub 当前负责提供“热搜话题种子”；微博 CLI 当前只能对已有 query 做搜索补强。
- 当前状态下，微博 CLI 不能完全替代 RSSHub；已验证的 `search/statuses/limited` 是 query-driven 命令，不能自己发现全局热搜列表。

## CLI search 已验证命令

| Command | Access | Required fields | Title |
|---|---|---|---|
| `search statuses/limited` | `allowed` | `q, type` | 搜索含某关键词的微博 |
| `search suggestions/users/biz` | `allowed` | `q` | 搜用户搜索建议 |

## `search/statuses/limited` 已观察数据

- 查询关键词数：`3`
- 每个关键词返回微博数：`10`
- 总返回微博数：`30`
- 每个查询均返回 `total_number`，可作为 `weibo_search_total_number_proxy`。
- 每条微博稳定返回 `id/idstr`、`mid`、`text`、`created_at`、评论数、转发数、点赞 / 态度数和用户昵称。
- 未稳定观察到可直接使用的微博详情 URL；后续如需要引用链接，应由 `mid` / `idstr` 另行构造或通过详情接口补强。

## 字段完整率

| 字段 | 完整率 | 比例 |
|---|---:|---:|
| `id/idstr` | `30/30` | `100%` |
| `mid` | `30/30` | `100%` |
| `text` | `30/30` | `100%` |
| `created_at` | `30/30` | `100%` |
| `comments_count` | `30/30` | `100%` |
| `reposts_count` | `30/30` | `100%` |
| `attitudes_count` | `30/30` | `100%` |
| `user.screen_name` | `30/30` | `100%` |
| `url/scheme` | `0/30` | `0%` |

## 可用 raw signal feature

| Feature | 可用率 | 比例 |
|---|---:|---:|
| `total_number` | `3/3` | `100%` |
| `comments_count` | `30/30` | `100%` |
| `reposts_count` | `30/30` | `100%` |
| `attitudes_count` | `30/30` | `100%` |
| `created_at` | `30/30` | `100%` |

字段来源和含义：

| Feature | 来源 | 描述对象 | 评分用途 | 注意事项 |
|---|---|---|---|---|
| `total_number` | `search/statuses/limited` 响应顶层字段 | 当前关键词搜索结果规模 | 可作为 `weibo_search_total_number_proxy`，描述关键词在微博搜索中的内容规模 | 不是热搜热度值，不是全站发帖总量，只能作为搜索接口返回的规模代理 |
| `comments_count` | 每条 `statuses[]` 微博对象字段 | 单条微博评论数 | 可用于代表性微博的讨论强度样本 | 不能直接相加为话题总评论数；需先做相关性过滤 |
| `reposts_count` | 每条 `statuses[]` 微博对象字段 | 单条微博转发数 | 可用于代表性微博的传播强度样本 | 不能代表话题整体传播总量 |
| `attitudes_count` | 每条 `statuses[]` 微博对象字段 | 单条微博点赞 / 态度数 | 可用于代表性微博的互动强度样本 | 不等于热搜热度，也不等于全话题点赞总量 |
| `created_at` | 每条 `statuses[]` 微博对象字段 | 单条微博发布时间 | 可用于新鲜度、最近活跃时间和 velocity proxy | 时间是样本微博发布时间，不是热搜上榜时间 |

## 话题级搜索结果

| Query | `total_number_proxy` | 返回微博 | 文本命中 query / 近似 query | 最高评论 | 最高转发 | 最高点赞 | 最新样本时间 UTC |
|---|---:|---:|---:|---:|---:|---:|---|
| `吃播圈催吐导泄都造成血钾暴跌` | `5543` | `10` | `4` | `9` | `1` | `14` | `2026-09-09T03:25:53+00:00` |
| `太子奶创始人李途纯去世` | `957` | `10` | `5` | `4` | `0` | `3` | `2026-09-09T03:15:18+00:00` |
| `网红宣传捐款百万实际只捐1元` | `81161` | `10` | `2` | `2` | `1` | `2` | `2026-09-09T03:30:00+00:00` |

## 样例

本节样例只来自 `search/statuses/limited` 的返回结果，不包含 `search suggestions/users/biz`。本次没有调用 `search suggestions/users/biz`，因为它是用户搜索建议接口，只返回用户建议，不适合补强话题热度。

| Query | Weibo ID | Author | Created at UTC | Comments | Reposts | Likes | Text preview |
|---|---|---|---|---:|---:|---:|---|
| `吃播圈催吐导泄都造成血钾暴跌` | `5341198218694159` | 文明宁夏 | `2026-09-09T03:25:53+00:00` | `0` | `0` | `0` | `【#出汗乏力当心低钾血症#！#很多人忽视了钾的重要性#】专家介绍，轻症中暑和轻症低钾血症症状相似度极高...` |
| `吃播圈催吐导泄都造成血钾暴跌` | `5341197737395680` | 选择中医__董洪涛 | `2026-09-09T03:23:58+00:00` | `2` | `1` | `4` | `千万不要反复催吐。近日，24岁吃播网红“干饭莹莹”因反复催吐导致血钾暴跌至2.3mmol/L...` |
| `吃播圈催吐导泄都造成血钾暴跌` | `5341196739939811` | 武汉交通广播 | `2026-09-09T03:20:00+00:00` | `0` | `0` | `0` | `【#出汗乏力当心低钾血症#！#很多人忽视了钾的重要性#】专家介绍，轻症中暑和轻症低钾血症症状相似度极高...` |
| `太子奶创始人李途纯去世` | `5341195557408735` | 不看评论区有毒 | `2026-09-09T03:15:18+00:00` | `0` | `0` | `0` | `#太子奶创始人李途纯去世# 那些移民的，估计没有过硬的关系才出走的吧...` |
| `太子奶创始人李途纯去世` | `5341194223356475` | 第一财经YiMagazine | `2026-09-09T03:10:00+00:00` | `0` | `0` | `0` | `#太子奶创始人李途纯去世# 9月8日，李途纯之子李帅对媒体表示，原湖南太子奶集团总裁...` |
| `太子奶创始人李途纯去世` | `5341190012535898` | 经理人杂志 | `2026-09-09T02:53:16+00:00` | `0` | `0` | `1` | `【#李途纯离世一代乳酸菌开拓者终谢幕##李途纯突发脑溢血摔倒#】9月8日...` |
| `网红宣传捐款百万实际只捐1元` | `5341199255735396` | 新浪广东 | `2026-09-09T03:30:00+00:00` | `0` | `0` | `0` | `【#网红晒捐百万实捐1元比不捐更恶劣#】#网红捐1元立百万人设是对善意的玩弄#...` |
| `网红宣传捐款百万实际只捐1元` | `5341190964379655` | 湖南专升本宝藏呀 | `2026-09-09T02:57:02+00:00` | `0` | `0` | `0` | `哎上课专升本学员反馈... #高考数学132分开学考只考12分#网红宣传捐款百万实际只捐1元#...` |
| `网红宣传捐款百万实际只捐1元` | `5341186602829283` | 哈尔滨日报 | `2026-09-09T02:39:43+00:00` | `0` | `0` | `0` | `#网红宣称捐款百万吸粉实际只捐1元#【#网红虚假宣传捐款百万被禁言#】受今年10号台风...` |

## 相关性和噪声观察

- `search/statuses/limited` 能返回可量化互动字段，但它是关键词搜索，不是热榜 API。
- `total_number` 可以作为搜索结果规模代理，但必须标记为 `search_result_total_proxy`，不能解释为微博平台热度值。
- 搜索结果存在明显关键词噪声。例如含有目标 hashtag 的营销 / 课程类微博也会进入结果，需要相关性过滤后再评分。
- 排序默认由接口决定；即使后续使用 `--sort hot`，也只能解释为搜索接口内部排序，不能解释为热搜排名。
- 本次 normalized sample 未触发缺字段类 `quality_flags`。

## 排序和样本扩展观察

`search/statuses/limited` 支持 `sort`、`count` 和 `page` 参数：

- `sort=time`：时间倒序，默认值。
- `sort=hot`：热门度排序，但命令说明标记为“设置此参数只会返回精选微博”。
- `sort=fwnum`：按转发数倒序。
- `sort=cmtnum`：按评论数倒序。
- `count`：每页返回数量，最小 `10`，最大 `50`。

CLI 命令语义补充：

- `sort=hot` 应解释为 CLI 搜索返回的热门 / 精选结果池，不应解释为默认搜索全集的简单重排，也不应解释为全量热搜话题微博。
- 搜索是模糊匹配，返回结果总数只能作为当前 query 和排序模式下的规模代理。

对 `网红宣传捐款百万实际只捐1元` 的低频对比：

| Query mode | Returned | `total_number` | Min comments | Max comments | Min reposts | Max reposts | Min likes | Max likes | Sum comments | Sum reposts | Sum likes |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| default / time / count 10 | `10` | `81161` | `0` | `2` | `0` | `1` | `0` | `2` | `3` | `2` | `4` |
| hot / count 10 | `10` | `832` | `11` | `467` | `5` | `308` | `93` | `6107` | `1219` | `545` | `14294` |
| hot / count 20 | `19` | `832` | `2` | `467` | `0` | `308` | `16` | `6107` | `1358` | `626` | `15075` |

观察：

- 默认返回结果不是随机样本，更接近时间倒序的新近微博样本。
- `sort=hot` 能更接近微博搜索接口认为“热门”的微博，互动字段明显更高。
- `sort=hot` 的 `total_number` 与默认排序不同，本次从 `81161` 变为 `832`，说明它不是同一全量候选池的简单重排，而更像精选 / 热门结果集合。
- 本地 `hot / count 10` 的 `10/10` 条全部出现在 `hot / count 20` 中，且顺序完全等于 `hot / count 20` 的前 10 条；这说明本轮样本中 `count=10` 更像 `sort=hot` 结果序列的前缀，不像随机返回。
- `sort=hot` 返回顺序不是简单按 `comments_count + reposts_count + attitudes_count` 降序。例如 `hot / count 20` 中第 3 条互动总和 `6260`，高于第 2 条 `334`；第 6 条 `863`，高于第 4 条 `163` 和第 5 条 `123`。因此“hot 前 10”不能表述为转评赞总和最高的 10 条。
- 如果目标是估计话题热度，建议同时采样 `sort=time` 和 `sort=hot`：前者看最新活跃，后者看代表性高互动样本。
- 体验服务下 `search/statuses/limited` 有调用额度限制，不建议大规模翻页；MVP 可限制为每个话题 `sort=time` 一次、`sort=hot` 一次，每次 `count=10` 或 `20`。

为什么 `sort=hot` 后 `total_number` 会变化：

- CLI 命令定义中，`sort=hot` 的语义更接近热门 / 精选结果。
- 因此 `sort=hot` 不是对默认搜索结果全集做排序，而是让接口返回一个被平台筛选过的热门 / 精选结果集合。
- `total_number` 应解释为“当前 query + 当前过滤条件 + 当前排序模式下的结果规模代理”，不是固定的全话题发帖总量。
- 当前没有观察到官方公开说明称 `sort=hot` 必须达到某个评论、转发或点赞阈值才会进入结果。更稳妥的表述是：平台按内部热门度 / 精选规则筛选，互动数显著相关，但具体算法和阈值未知。

CLI 使用边界：

- 当前 CLI smoke test 只验证到 `search/statuses/limited` 可用。
- `search/statuses/limited --sort hot` 支持按已知 query 获取热门 / 精选微博，单页 `count` 最大 `50`，可带 `page`；但它不保证抓取某个热搜话题下的全量 hot 微博。

## 与 RSSHub 热搜种子对比

微博 CLI 多了：

- 微博正文样本。
- 微博 ID / MID，可用于后续详情、评论和转发补强。
- 发布时间 `created_at`，可用于 freshness / velocity proxy。
- 互动字段：`comments_count`、`reposts_count`、`attitudes_count`。
- `total_number`，可作为关键词搜索规模代理。

微博 CLI 少了或更弱：

- 不能作为全局微博热搜发现入口；已验证命令必须先有 query。
- 必须先有 query，因此仍需要 RSSHub、新闻源或其它候选生成器。
- 搜索结果可能混入历史内容、泛词内容、广告和低相关内容。
- 依赖 OAuth 登录、开发者认证、体验服务 / 正式服务和额度。

替代关系判断：

- 当前不能“全部用微博 CLI 查找结果”，因为已解锁的 `search/statuses/limited` 是 query-driven 接口，不能自己发现全局热搜列表。
- 当前可行链路仍是 `RSSHub 热搜种子 -> 微博 CLI 搜索补强`。

RSSHub 多了：

- 不需要微博 CLI 登录即可提供热搜标题种子。
- 能给出热搜列表顺序，可派生 `list_position`。

RSSHub 少了：

- 缺少微博正文、互动数、发布时间和稳定 `hot_value`。
- `list_position` 不是官方结构化 rank，也不是热度值。

## Schema 影响

- CLI 搜索结果应映射为 `source_origin = weibo_cli`，`source_status = fallback_pending_credentials` 或验证后 `fallback`。
- `total_number` 写入 `raw_metrics.weibo_search_total_number_proxy`，并加 `search_result_total_proxy` 语义标记。
- `comments_count`、`reposts_count`、`attitudes_count` 写入代表性微博样本指标，不直接等同于话题总互动。
- `created_at` 可用于 `latest_status_created_at` 和 freshness proxy。
- 未通过相关性过滤的微博只进审计日志，不进入事件评分。

## 决策

- `weibo_cli_search_statuses_limited` 可从 `fallback_pending_credentials` 升级为 `fallback_candidate`，因为登录、认证、服务和字段 smoke test 已通过。
- 暂不升级为正式 `use`，因为还没有多轮稳定性、额度消耗和相关性保留率验证。
- v0.3 最小链路继续保持：RSSHub 做话题种子，微博 CLI 做小样本搜索补强。
