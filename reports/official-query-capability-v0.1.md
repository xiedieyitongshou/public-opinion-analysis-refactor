# 官媒 query 能力检测报告 v0.1

生成时间：2026-09-22

## 检测目标

本次只检测当前第一阶段已确定为 `use` 的两个官媒来源：

- 人民网
- 中国新闻网

目标是判断系统能否用社区事件生成的 `official_query`，在官媒官网检索到相关报道，并把结果作为后续 `match_official_support` 的候选 evidence pool。

本报告只评估公开检索入口和字段可解析性，不评估报道内容是否一定支撑某个具体事件。事件级支撑仍由 Day 43 的 `match_official_support` 判断。

## 检测结果概览

| 来源 | 检索入口 | 是否能搜到相关信息 | MVP 接入判断 | 说明 |
|---|---|---:|---|---|
| 人民网 | `https://search.people.cn/s?keyword={query}` | 暂不能稳定从静态 HTML 解析 | `rss_only` / `postpone` | 页面可访问，但返回内容主要是 Nuxt 前端壳，静态 HTML 未直接包含结果列表 |
| 中国新闻网 | `https://sou.chinanews.com.cn/search.do?q={query}` | 能 | `use` | 页面可访问，HTML 内嵌 `docArr`，包含标题、URL、发布时间、摘要等字段 |

## 人民网

检测入口：

```text
https://search.people.cn/s?keyword=医保
http://search.people.cn/s/?keyword=医保
```

检测现象：

- 请求可以返回 `200 OK`。
- HTTPS 入口会跳转到 HTTP 搜索页。
- 返回静态 HTML 主要包含 `__nuxt`、`_nuxt/*.js` 等前端资源。
- 静态 HTML 中未直接观察到可解析的搜索结果列表。
- 当前不引入浏览器渲染、JS 执行或复杂前端接口逆向。

结论：

- 人民网官网搜索页可能具备用户侧搜索能力，但 **不适合作为 Day 45 MVP 的稳定程序化 query 来源**。
- 第一阶段继续使用已有 `people_politics_rss` 作为人民网 evidence pool。
- 对社区事件执行官媒补证时，人民网若仅有 RSS 池可用，应标记：

```text
search_capability = site_search_page
requires_js = true
source_status = rss_only 或 postpone
quality_flags += official_query_unavailable
```

## 中国新闻网

检测入口：

```text
https://sou.chinanews.com.cn/search.do?q=医保
```

检测现象：

- 请求可以返回 `200 OK`。
- `q` 参数可以返回与关键词相关的结果。
- 页面 HTML 中内嵌 `docArr` 数据。
- `docArr` 中可观察到以下字段：

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

结论：

- 中国新闻网官网搜索结果 **可以支持 Day 45 的低频官媒定向补证**。
- MVP 可将其标记为：

```text
search_capability = site_search_page
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

## 接入边界

- 人民网：不做 Day 45 定向搜索接入，继续使用 RSS evidence pool。
- 中国新闻网：可以做低频定向搜索接入，但必须设置超时、结果条数上限和失败降级。
- 两个来源都不能把“搜到报道”直接解释为“公众热度高”。
- 搜索命中只表示存在候选官媒 evidence，是否支撑事件仍需经过实体、动作、关键词和时间窗口匹配。

## 后续标记规则

建议 fallback 标记：

```text
official_query_unavailable   # 搜索入口不可稳定程序化使用
official_query_failed        # 请求失败、超时或结构异常
official_support_not_found   # 已检索但无有效支撑命中
official_pool_empty          # 本地官媒 evidence pool 为空
```

建议第一阶段接入优先级：

1. 中国新闻网定向搜索。
2. 中国新闻网 RSS evidence pool。
3. 人民网 RSS evidence pool。
4. 人民网官网搜索暂缓。

