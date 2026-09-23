# 官媒 query 与议程能力检测报告 v0.1

生成时间：2026-09-23

## 检测目标

本次只检测当前第一阶段已确定为 `use` 的两个官媒来源：

- 人民网
- 中国新闻网

检测目标分为两类：

1. **定向 query 能力**：判断系统能否用社区事件生成的 `official_query`，在官媒官网检索到相关报道，并把结果作为后续 `match_official_support` 的候选 evidence pool。
2. **官媒议程能力**：判断系统能否获取官媒站内热点、排行或推荐列表，用于发现“官媒自己在报道什么”，形成 `F_official_only` 或“高证据、低讨论”候选。

本报告只评估公开页面、前端检索接口、站内排行接口和字段可解析性，不评估报道内容是否一定支撑某个具体事件。事件级支撑仍由 Day 43 的 `match_official_support` 判断。

## 检测结果概览

| 来源 | 定向 query 能力 | 官媒议程 / 排行能力 | MVP 接入判断 | 说明 |
|---|---|---|---|---|
| 人民网 | `POST /search-platform/front/search` 返回 JSON | `GET /search-platform/front/searchRank` 返回站内热点排行 | `use_with_stability_guard` | 搜索页静态 HTML 是 Nuxt 壳，但前端 JSON 接口和热点排行接口可用 |
| 中国新闻网 | `GET /search.do?q={query}`，HTML 内嵌 `docArr` | `GET /cns/pub/hot_news_list` 和 `GET /cns/app/v1/hotnews_list` 返回热点列表 | `use` | 搜索页和热点列表均可低频程序化解析 |

## 人民网

### 定向 query 能力

浏览器访问入口：

```text
http://search.people.cn/s?keyword=DOTA2&st=0
https://search.people.cn/s?keyword=医保
```

检测现象：

- 搜索页可以返回 `200 OK`。
- 静态 HTML 主要包含 `__nuxt`、`_nuxt/*.js` 等前端资源。
- 静态 HTML 本身不直接包含搜索结果列表。
- 浏览器中能看到搜索结果，是因为前端加载后调用了后端搜索接口。

从 Nuxt 前端资源中可定位到实际搜索接口：

```text
POST http://search.people.cn/search-platform/front/search
```

该接口不支持 GET：

```text
GET /search-platform/front/search -> 405 Method Not Allowed
POST /search-platform/front/search -> 返回搜索结果 JSON
```

这说明人民网搜索页使用的是“读取型 POST 查询”。POST 在这里用于提交复杂查询条件，不表示创建或修改新闻内容。

示例请求：

```bash
curl -X POST "http://search.people.cn/search-platform/front/search" \
  -H "Content-Type: application/json;charset=UTF-8" \
  -H "Accept: application/json, text/plain, */*" \
  -H "Referer: http://search.people.cn/s?keyword=DOTA2&st=0" \
  -d '{
    "key": "DOTA2",
    "page": 1,
    "limit": 10,
    "hasTitle": true,
    "hasContent": true,
    "isFuzzy": false,
    "type": 0,
    "sortType": 0,
    "startTime": 0,
    "endTime": 0
  }'
```

请求体字段含义：

| 字段 | 观察含义 |
|---|---|
| `key` | 查询关键词 |
| `page` | 页码 |
| `limit` | 每页结果数 |
| `hasTitle` | 是否检索标题 |
| `hasContent` | 是否检索正文 / 摘要 |
| `isFuzzy` | 是否模糊匹配 |
| `type` | 检索类型，`0` 对应全网 / 综合 |
| `sortType` | 排序方式，观察到 `0`、`2` 等值 |
| `startTime` | 起始时间，`0` 表示不限制 |
| `endTime` | 结束时间，`0` 表示不限制 |

示例返回结构：

```json
{
  "code": "0",
  "data": {
    "records": [],
    "total": 60
  }
}
```

`DOTA2` 查询样例中观察到：

```text
code = 0
records = 10
total = 60
```

单条 `records[]` 可观察到的主要字段：

```text
title
url
content
contentOriginal
displayTime
inputTime
belongsName
belongsId
domain
id
source
sourceId
contentId
sourcetitle
originUrl
originNodeRname
originalName
keyword
hasImg
hasVideo
imageUrl
```

字段价值：

- `title` 可映射为官媒候选 item 标题。
- `url` 可作为 citation URL。
- `content` 可作为摘要 / 匹配文本，命中词可能包含 `<em>` 高亮标签。
- `contentOriginal` 可作为正文片段或补充匹配文本，但需要 HTML 清洗。
- `displayTime` / `inputTime` 可映射为发布时间或入库时间，需要确认毫秒时间戳语义。
- `belongsName` 可作为频道 / 栏目路径。
- `sourceId`、`contentId`、`id` 可作为外部 ID 或去重辅助字段。

建议 MVP 标记为：

```text
search_capability = frontend_json_api
endpoint = http://search.people.cn/search-platform/front/search
method = POST
query_param_name = request_body.key
requires_js = false
official_public_api_doc = false
source_status = use_with_stability_guard
```

### 官媒议程 / 站内排行能力

人民网搜索页前端还包含“热点排行”组件，其接口为：

```text
GET http://search.people.cn/search-platform/front/searchRank
```

示例返回结构：

```json
{
  "code": "0",
  "data": [
    {
      "id": 1,
      "contentId": 40802962,
      "url": "http://cpc.people.com.cn/n1/2026/0921/c64387-40802962.html",
      "title": "张又侠、刘振立严重违纪违法被开除党籍军籍",
      "hitCount": 23495
    }
  ],
  "server_time": 1790157351
}
```

可用字段：

```text
id
contentId
url
title
hitCount
server_time
```

字段价值：

- `id` 可映射为站内排行位置或列表序号。
- `title` 可映射为官媒议程 item 标题。
- `url` 可作为 citation URL。
- `contentId` 可作为外部 ID 或去重辅助字段。
- `hitCount` 可作为人民网站内点击 / 热点排行弱代理字段。

接入边界：

- `searchRank` 可用于发现人民网当前站内热点报道。
- `hitCount` 只能解释为人民网搜索站内排行或点击相关弱信号，不能解释为全网公众热度。
- `searchRank` 适合作为 `F_official_only` 候选来源之一。
- 同时观察到 `GET /search-platform/front/searchKeysRank/rankType`，但当前返回 `code = 100002`，暂不进入 MVP。

建议 MVP 标记为：

```text
agenda_capability = frontend_rank_api
endpoint = http://search.people.cn/search-platform/front/searchRank
method = GET
rank_field = id
optional_metric = hitCount
source_status = use_with_stability_guard
quality_flags = []
```

## 中国新闻网

### 定向 query 能力

检测入口：

```text
https://sou.chinanews.com.cn/search.do?q=医保
```

检测现象：

- 请求可以返回 `200 OK`。
- `q` 参数可以返回与关键词相关的结果。
- 页面 HTML 中内嵌 `docArr` 数据。
- 不需要浏览器渲染即可从 HTML 源码中解析结果。

`docArr` 中可观察到以下字段：

```text
title
url
pubtime
content_without_tag
primary_channel
unique_id
createtime
thumbnail_file
```

字段价值：

- `title` 可映射为官媒候选 item 标题。
- `url` 可作为 citation URL。
- `pubtime` 可映射为 `published_at`。
- `content_without_tag` 可作为摘要 / 匹配文本。
- `primary_channel` 可作为频道。
- `unique_id` 可作为外部 ID 或去重辅助字段。

MVP 可标记为：

```text
search_capability = site_search_page
endpoint = https://sou.chinanews.com.cn/search.do
method = GET
query_param_name = q
requires_js = false
source_status = use
```

建议实现：

```text
official_query
-> GET https://sou.chinanews.com.cn/search.do?q={query}
-> parse docArr
-> normalize to Item
-> match_official_support
```

### 官媒议程 / 热点列表能力

中国新闻网首页暴露了公开热点列表接口：

```text
GET https://dw.chinanews.com/cns/pub/hot_news_list
GET https://dw.chinanews.com/cns/app/v1/hotnews_list
```

`pub/hot_news_list` 示例字段：

```text
msgcode
data[].id
data[].title
data[].shareUrl
message
```

`app/v1/hotnews_list` 示例字段：

```text
msgcode
data[].id
data[].title
data[].shareUrl
data[].pubtime
data[].source
data[].author
data[].content
data[].contentForDetail
data[].pre_channel
data[].classify
data[].listPosition
data[].channelOrder
data[].homePosition
message
```

字段价值：

- `title` 可映射为官媒议程 item 标题。
- `shareUrl` 可作为 citation URL。
- `pubtime` / `stime` 可映射为发布时间。
- `source` / `author` 可作为来源和作者。
- `content` / `contentForDetail` 可作为摘要或匹配文本，但需要 HTML 清洗。
- `listPosition` / `channelOrder` / `homePosition` 可作为中国新闻网热点列表位置或首页推荐弱信号。

接入边界：

- `pub/hot_news_list` 字段更轻，适合作为热点列表种子。
- `app/v1/hotnews_list` 字段更完整，适合作为 `OfficialAgendaItem` 的详细补充来源。
- 列表顺序只能解释为中国新闻网站内热点 / 推荐顺序，不能解释为公众热度。

建议 MVP 标记为：

```text
agenda_capability = hot_news_list
endpoint_primary = https://dw.chinanews.com/cns/pub/hot_news_list
endpoint_detail = https://dw.chinanews.com/cns/app/v1/hotnews_list
method = GET
rank_field = list_index 或 listPosition
source_status = use
quality_flags = []
```

## F 类数据制作建议

官媒议程能力用于补足 Day 45 路径 B：

```text
人民网 searchRank
中国新闻网 hot_news_list
官媒 RSS
-> OfficialAgendaItem
-> normalize_raw_items
-> SourceSignal
-> extract_event_signals
-> match_and_resolve_events
-> Event
-> 如果无知乎 / 微博 presence
-> F_official_only
```

建议新增或显式标记的字段：

```text
official_path_type = agenda_discovery
official_agenda_source = people_search_rank | chinanews_hot_news_list | official_rss
official_site_rank
official_list_position
official_hit_count_optional
official_recommend_source
official_coverage_level
```

F 类生成条件：

- 来源为官媒 agenda / RSS。
- 成功进入事件链路。
- 没有知乎 / 微博 TopN、搜索补强或 CLI presence。
- 至少存在可引用的官媒 source citation。

F 类排序建议：

1. 站内列表位置：`official_site_rank` / `official_list_position`。
2. 发布时间新鲜度。
3. 来源权重。
4. 官媒覆盖强度。
5. 字段完整率和 URL 可引用性。

展示边界：

- F 类进入“官媒重点报道 / 高证据低讨论”区域。
- F 类不进入社区热点主列表。
- F 类不参与知乎 / 微博平台热度分。
- 不能把 `hitCount`、`listPosition` 或多官媒覆盖写成“公众热度高”。

## 接入边界

- 人民网：可接入前端 JSON 搜索接口和站内热点排行接口，但必须标记为 `frontend_json_api` / `frontend_rank_api`，不要写成官方开放 API。
- 中国新闻网：可接入搜索页 HTML 内嵌 `docArr`，也可接入公开热点列表接口。
- 两个来源都必须设置低频访问、请求超时、结果条数上限和失败降级。
- 两个来源都不能把“搜到报道”或“站内排行靠前”直接解释为“公众热度高”。
- 搜索命中只表示存在候选官媒 evidence，是否支撑事件仍需经过实体、动作、关键词和时间窗口匹配。

## 后续标记规则

建议 fallback 标记：

```text
official_query_unavailable   # 搜索入口不可稳定程序化使用
official_query_failed        # 请求失败、超时或结构异常
official_agenda_unavailable  # 官媒议程 / 排行入口不可用
official_agenda_failed       # 官媒议程请求失败、超时或结构异常
official_support_not_found   # 已检索但无有效支撑命中
official_pool_empty          # 本地官媒 evidence pool 为空
```

建议第一阶段接入优先级：

1. 中国新闻网定向搜索。
2. 人民网前端 JSON 搜索接口。
3. 中国新闻网 `hot_news_list` 官媒议程列表。
4. 人民网 `searchRank` 官媒议程列表。
5. 中国新闻网 RSS evidence pool。
6. 人民网 RSS evidence pool。

