# Weibo / Zhihu Official Access v0.1

Generated at: 2026-09-06

Last updated: 2026-09-07

## Scope

Follow-up check for whether Weibo and Zhihu can provide stable, compliant
information sources without using personal cookies, automated login, proxy pools,
or anti-bot bypass.

This report only covers Weibo and Zhihu themselves. It does not evaluate
third-party hotlist substitutes.

## Current Result

| Source | Public no-login hotlist path | Official authorized path | Suggested status | Decision |
|---|---|---|---|---|
| Weibo hot search / trends | No | Yes, via Weibo Open Platform APIs / CLI direction | `postpone` | Keep as representative BBS source, but require OAuth/app authorization and endpoint validation before collector work. |
| Zhihu hotlist | No | Yes, via Zhihu Data Open Platform `hot_list` API / MCP | `use` | Access Secret smoke test succeeded. Use `hot_list` as the Mode A Zhihu collector source; keep `zhihu_search` as a Mode B evidence/search tool. |

## Weibo

Observed public path:

- `https://s.weibo.com/top/summary?cate=realtimehot` redirects to the Sina visitor
  system in the smoke test.
- The public page cannot be treated as a stable no-login structured source.

Official path found:

- Weibo Open Platform is reachable.
- Weibo Business API page is reachable at `https://open.weibo.com/ai/business-api`.
- Weibo API v2 documentation is reachable.
- Weibo API docs expose OAuth2 authorization flow and `access_token` use.
- Relevant API pages observed:
  - `https://open.weibo.com/wiki/2/trends/hourly`
  - `https://open.weibo.com/wiki/2/search/topics`
  - `https://open.weibo.com/wiki/2/search/statuses`
- Business API list endpoints are publicly readable:
  - `https://open.weibo.com/ai/aj/openapi/api/list?type=1`
  - `https://open.weibo.com/ai/aj/openapi/api/list?type=2`
  - `https://open.weibo.com/ai/aj/openapi/api/detail?t=search/statuses/limited`
  - `https://open.weibo.com/ai/aj/openapi/api/detail?t=search/hotword/mention_count`

Relevant Business API capabilities observed:

```text
search/statuses/limited
  description = 搜索含某关键词的微博
  endpoint = https://c.api.weibo.com/2/search/statuses/limited.json
  auth = OAuth access_token required
  sort supports time / hot / fwnum / cmtnum

search/hotword/mention_count
  description = 搜索热词提及数
  endpoint = https://c.api.weibo.com/2/search/hotword/mention_count.json
  auth = OAuth access_token required
  required query = q, starttime, endtime

微博关键词舆情监控
  description = preset keyword monitoring for sentiment, views, hot topics, trend data
  endpoints = /v1/trend/task/create, /v1/trend/task/result
  auth = login / token based
```

Business API cost signals observed:

```text
按条数配额收费 API
按查询次数收费 API
按权限收费 API
微博指定查询类接口组: trial_days = 7, trial_quota = 20000
微博关键词舆情监控: trial_days = 7, trial_quota = 1 keyword
```

Engineering interpretation:

- `2/trends/hourly` is a closer match for trends than direct hot-search scraping,
  but it still requires OAuth `access_token` and has rate limits.
- `2/search/topics` and `2/search/statuses` are search/topic APIs, not direct
  hot-search list collectors. They require authorization and may require higher
  permissions.
- `search/statuses/limited` can support keyword-based Weibo evidence collection
  after another source has produced candidate topics. It is not a source of the
  global real-time hot-search list.
- `search/hotword/mention_count` can support trend/attention scoring for known
  hotwords. It cannot discover hotwords by itself.
- `微博关键词舆情监控` may return hot topics and trend data for preset keywords, but
  it is a commercial monitoring product, not a free global hot-search feed.
- This is a legitimate direction only if app registration, permissions, rate
  limits, pricing, and allowed use are confirmed.

Current status:

```text
source_status = postpone
reason = official authorized path exists, but project has not validated credentials,
endpoint scope, quota, or terms for stable collection.
```

Lowest-cost interpretation:

```text
Do not treat Business API as a no-cost Weibo hot-search data source.
If trial access is available, use it only for a small smoke test.
Best project fit: keyword-based evidence/trend enrichment, not primary hotlist discovery.
```

## Zhihu

Observed public path:

- `https://www.zhihu.com/hot` returns 403 in the smoke test.
- `https://www.zhihu.com/api/v3/feed/topstory/hot-lists/total?...` returns 401
  `AuthenticationError`.
- The public page/API cannot be treated as a stable no-login structured source.

Official path found:

- `https://developer.zhihu.com/` is reachable.
- Page metadata identifies it as `知乎数据开放平台`.
- Page metadata lists `API`, `MCP`, `Skills`, `搜索`, `直答`, `知识库`, `RAG`, and
  `热榜`.
- The front-end bundle includes routes such as `/hotlist`, `/docs`, and
  `/authentication`, plus console APIs such as `/console/api/user_info` and
  `/console/api/v3/docs`.
- `https://developer.zhihu.com/docs?key=zhihu_search` exposes public docs through
  `https://developer.zhihu.com/console/api/v3/docs`.
- The docs include `hot_list`, `zhihu_search`, quota, Skill, and MCP interfaces.

Relevant official interfaces:

```text
GET https://developer.zhihu.com/api/v1/content/hot_list?Limit=10
GET https://developer.zhihu.com/api/v1/content/zhihu_search?Query=<query>&Count=5
GET https://developer.zhihu.com/api/v1/quota?APIIDs=hot_list,zhihu_search
MCP SSE https://developer.zhihu.com/api/mcp/hot_list/v1/sse
MCP message https://developer.zhihu.com/api/mcp/hot_list/v1/message
```

Expected `hot_list` fields:

```text
Total
Items[].Title
Items[].Url
Items[].ThumbnailUrl
Items[].Summary
```

Auth and limits:

```text
Authorization: Bearer <your_access_secret>
X-Request-Timestamp: Unix timestamp in seconds
Limit: default 30, maximum 30
error 20001: authorization failed
error 30001: rate limited
```

Quota / cost notes:

```text
GET https://developer.zhihu.com/api/v1/quota?APIIDs=hot_list,zhihu_search
```

- Public docs describe "每日限免额度" and "用量统计".
- Quota response fields include `TotalQuota`, `TotalUsed`, and `RemainingQuota`.
- Exact free quota and paid pricing are not exposed in the public docs checked here.
- Cost cannot be finalized until the project logs into the developer platform,
  creates an Access Secret, and queries quota for `hot_list` and `zhihu_search`.
- HTTP API and MCP should be treated as consuming the same capability quota unless
  the developer console states otherwise.

Smoke test result on 2026-09-07:

```text
hot_list: success, 10 items returned
hot_list quota: TotalQuota = 100, TotalUsed = 2, RemainingQuota = 98
zhihu_search: success with Count = 1
zhihu_search quota: TotalQuota = 5000, TotalUsed = 1, RemainingQuota = 4999
```

Observed `hot_list` fields:

```text
Data.Total
Data.Items[].Title
Data.Items[].Url
Data.Items[].ThumbnailUrl
Data.Items[].Summary
```

Observed `zhihu_search` fields:

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

Engineering interpretation:

- There is a concrete first-party developer/data-platform path for hotlist access.
- The HTTP JSON API is a better fit for scheduled backend collection because it
  returns structured JSON directly.
- The MCP path is a better fit for Agent tool usage, but its result is text/XML
  over SSE and adds client complexity for deterministic collectors.
- Unauthenticated calls to `hot_list`, `zhihu_search`, and quota return JSON with
  `Code=20001`, confirming that the endpoint exists but requires Access Secret.
- Access Secret authentication, quota, endpoint schema, and low-frequency calls
  have been validated for this project.
- `hot_list` does not expose a platform heat value in the observed response.
  First-stage scoring should use rank, repeated snapshot presence, and rank
  movement rather than inventing a hot value.
- `hot_list` does not expose item publication time in the observed response.
  The collector should set `published_at = null`, `fetched_at = now`, and use
  snapshot time for velocity calculation.
- `zhihu_search` exposes richer per-item metrics and edit time, but it is query
  driven. Keep it for Mode B evidence enrichment rather than Mode A scheduled
  hotlist discovery.

Current status:

```text
source_status = use
reason = official hot_list API authentication, quota, schema, and stable JSON
sample were validated with project credentials. Schedule at low frequency to
respect daily quota.
```

## Account Risk

Using personal logged-in sessions, cookies, browser automation, or reverse
engineered private endpoints should not be used for this project.

Risks:

- unstable runtime dependency on account/session state
- visitor/login verification
- request throttling or temporary restriction
- possible account limitation or suspension depending on platform terms, request
  pattern, endpoint sensitivity, and whether access is authorized

## Day 16 Gate

Keep Weibo and Zhihu as representative BBS sources in product design. A real
collector can be implemented only when both are true:

- a first-party authorized path is available to this project
- a smoke test with project credentials confirms stable JSON/XML fields,
  rate-limit behavior, and allowed usage

Current gate result:

```text
weibo_hot_search.source_status = postpone
zhihu_hotlist.source_status = use
```
