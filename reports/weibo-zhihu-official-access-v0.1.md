# 微博 CLI / 知乎官方访问路径 v0.1

生成日期：2026-09-06

最后更新：2026-09-07

## 范围

本报告用于补充确认：微博和知乎是否能在不使用个人 Cookie、自动登录、代理池或反爬绕过的前提下，提供稳定且合规的信息源。微博侧项目口径收敛为 RSSHub 热搜种子 + 微博 CLI 搜索补强。

本报告只保留当前项目采用的微博 CLI 路径和知乎数据平台路径。

## 当前结果

| Source | Public no-login hotlist path | Project path | Suggested status | Decision |
|---|---|---|---|---|
| Weibo hot search / trends | No | RSSHub topic seed + Weibo CLI search enrichment | `fallback_candidate` | 用 RSSHub 获取热搜话题种子，CLI 对已知话题补充微博正文、时间和转评赞样本。 |
| Zhihu hotlist | No | Zhihu Data Open Platform `hot_list` API / MCP | `use` | Access Secret smoke test 已通过。`hot_list` 可作为模式 A 的知乎采集来源；`zhihu_search` 保留为模式 B 的证据 / 搜索工具。 |

## 微博

已观察到的公开入口：

- `https://s.weibo.com/top/summary?cate=realtimehot` 在 smoke test 中会跳转到新浪访客系统。
- 公开页面不能视为稳定的免登录结构化数据源。

项目路径：

```text
RSSHub /weibo/search/hot
-> 产出微博热搜话题种子
-> 微博 CLI search/statuses/limited 对已知话题做搜索补强
-> 可选 CLI 评论 / 转发接口补充代表性微博互动样本
```

已观察到的 CLI 能力：

```text
search/statuses/limited
  role = 搜索含某关键词的微博
  input = 已知 topic / query
  output = 微博正文、发布时间、微博 ID、用户、comments_count、reposts_count、attitudes_count、total_number
```

工程判断：

- RSSHub 只负责低成本热搜话题发现；它的列表顺序只能作为 `list_position`，不能解释为官方 rank 或真实热度值。
- 微博 CLI 只对已有 query 做小样本搜索补强，适合获取代表性微博的正文、时间和转评赞指标。
- CLI 搜索结果必须经过相关性过滤；低相关微博只进入审计日志，不参与评分。
- 体验额度或 CLI 登录不可用时，微博链路降级为 RSSHub-only 或直接跳过，不阻塞知乎和官媒链路。

当前状态：

```text
weibo_rsshub_hot_search.source_status = experimental_fallback
weibo_cli_search_statuses_limited.source_status = fallback_candidate
reason = use low-cost CLI enrichment for known topics.
```

## 知乎

已观察到的公开入口：

- `https://www.zhihu.com/hot` 在 smoke test 中返回 403。
- `https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?...` 返回 401 `AuthenticationError`。
- 公开页面 / API 不能视为稳定的免登录结构化数据源。

已找到的官方入口：

- `https://developer.zhihu.com/` 可访问。
- 页面元数据将其标识为 `知乎数据开放平台`。
- 页面元数据列出 `API`、`MCP`、`Skills`、`搜索`、`直答`、`知识库`、`RAG` 和 `热榜`。
- 前端 bundle 包含 `/hotlist`、`/docs`、`/authentication` 等路由，以及 `/console/api/user_info`、`/console/api/v3/docs` 等控制台 API。
- `https://developer.zhihu.com/docs?key=zhihu_search` 通过 `https://developer.zhihu.com/console/api/v3/docs` 暴露公开文档。
- 文档包含 `hot_list`、`zhihu_search`、quota、Skill 和 MCP 接口。

相关官方接口：

```text
GET https://developer.zhihu.com/api/v1/content/hot_list?Limit=10
GET https://developer.zhihu.com/api/v1/content/zhihu_search?Query=<query>&Count=5
GET https://developer.zhihu.com/api/v1/quota?APIIDs=hot_list,zhihu_search
MCP SSE https://developer.zhihu.com/api/mcp/hot_list/v1/sse
MCP message https://developer.zhihu.com/api/mcp/hot_list/v1/message
```

预期 `hot_list` 字段：

```text
Total
Items[].Title
Items[].Url
Items[].ThumbnailUrl
Items[].Summary
```

鉴权和限制：

```text
Authorization: Bearer <your_access_secret>
X-Request-Timestamp: Unix timestamp in seconds
Limit: default 30, maximum 30
error 20001: authorization failed
error 30001: rate limited
```

Quota / 成本说明：

```text
GET https://developer.zhihu.com/api/v1/quota?APIIDs=hot_list,zhihu_search
```

- 公开文档提到“每日限免额度”和“用量统计”。
- Quota 响应字段包含 `TotalQuota`、`TotalUsed` 和 `RemainingQuota`。
- 本次检查的公开文档没有暴露精确免费额度和付费价格。
- 成本必须等项目登录开发者平台、创建 Access Secret，并查询 `hot_list` 与 `zhihu_search` 的 quota 后才能最终确认。
- 除非开发者控制台明确说明，否则 HTTP API 和 MCP 应按消耗同一类能力 quota 处理。

2026-09-07 smoke test 结果：

```text
hot_list: success, 10 items returned
hot_list quota: TotalQuota = 100, TotalUsed = 2, RemainingQuota = 98
zhihu_search: success with Count = 1
zhihu_search quota: TotalQuota = 5000, TotalUsed = 1, RemainingQuota = 4999
```

已观察到的 `hot_list` 字段：

```text
Data.Total
Data.Items[].Title
Data.Items[].Url
Data.Items[].ThumbnailUrl
Data.Items[].Summary
```

已观察到的 `zhihu_search` 字段：

```text
Data.HasMore
Data.SearchHashId
Data.Items[].Title
Data.Items[].ContentType
Data.Items[].ContentID
Data.Items[].ContentText
Data.Items[].Url
Data.Items[].CommentCount
Data.Items[].VoteUpCount
Data.Items[].AuthorName
Data.Items[].EditTime
Data.Items[].RankingScore
```

工程判断：

- 已确认存在第一方开发者 / 数据平台入口，可用于访问知乎热榜。
- HTTP JSON API 更适合定时后端采集，因为它直接返回结构化 JSON。
- MCP 路径更适合 Agent 工具调用，但结果通过 SSE 返回文本 / XML，会增加确定性 Collector 的客户端复杂度。
- 未鉴权调用 `hot_list`、`zhihu_search` 和 quota 会返回带 `Code=20001` 的 JSON，说明接口存在，但需要 Access Secret。
- 本项目已经验证 Access Secret 鉴权、quota、接口 schema 和低频调用可用性。
- 已观察响应中，`hot_list` 不暴露平台热度值。第一阶段评分应使用 rank、重复快照出现次数和 rank 变化，不应虚构 hot value。
- 已观察响应中，`hot_list` 不暴露 item 发布时间。Collector 应设置 `published_at = null`、`fetched_at = now`，并使用快照时间计算 velocity。
- `zhihu_search` 暴露更丰富的单条内容指标和 edit time，但它是 query-driven 的。应把它保留为模式 B 的证据增强工具，而不是模式 A 的定时热榜发现源。

当前状态：

```text
source_status = use
reason = official hot_list API authentication, quota, schema, and stable JSON
sample were validated with project credentials. Schedule at low frequency to
respect daily quota.
```

## 账号风险

本项目不应使用个人登录态、Cookie、浏览器自动化或逆向私有接口作为数据采集方式。微博侧只使用 RSSHub 话题种子和微博 CLI 的低频授权命令，只接入 RSSHub + CLI 链路。

风险包括：

- 运行时不稳定，依赖账号 / 会话状态。
- 触发访客 / 登录验证。
- 触发请求限流或临时限制。
- 根据平台条款、请求模式、接口敏感度以及访问是否授权，可能带来账号受限或封禁风险。

## Day 16 闸门

产品设计中可以继续保留微博和知乎作为代表性 BBS / 社区来源。真实 Collector 只实现已经通过 smoke test 且成本可控的路径：

- 微博：RSSHub 作为实验性话题种子，微博 CLI 作为低频搜索 / 互动补强。
- 知乎：使用已验证的第一方数据平台接口。

当前闸门结果：

```text
weibo_rsshub_hot_search.source_status = experimental_fallback
weibo_cli_search_statuses_limited.source_status = fallback_candidate
zhihu_hotlist.source_status = use
```
