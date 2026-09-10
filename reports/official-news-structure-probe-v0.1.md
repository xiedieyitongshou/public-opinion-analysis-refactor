# 官媒新闻数据结构采样报告 v0.1

生成时间：2026-09-10

## 范围

本报告补充“知乎 / 微博数据结构”之后的官媒新闻源结构观察，目标是确认官媒新闻能否稳定映射为第一阶段的 `NormalizedItem`。

本次只使用公开 RSS endpoint 做低频采样，不使用账号、Cookie、Token、代理、浏览器自动化或反爬绕过方式。

关联入口说明：

- 根目录 `main.py` 当前只是 PyCharm 示例脚本，没有业务数据源逻辑。
- 实际官媒探测脚本位于 `backend/scripts/source_probe.py`。
- 本次原始本地样例写入 `notes/source-probe-raw/official-news-structure-probe-v0.1.raw.json`。

## 采样来源

| 来源 | Endpoint | 条目数 | 建议状态 | 主要结构 |
|---|---|---:|---|---|
| 人民网 | `http://www.people.com.cn/rss/politics.xml` | 10 | `use` | RSS item: `title`, `link`, `pubDate`, `author`, `description` |
| 中国新闻网 | `https://www.chinanews.com.cn/rss/scroll-news.xml` | 10 | `use` | RSS item: `title`, `link`, `description`, `pubDate` |
| 新华网 | `http://www.xinhuanet.com/politics/news_politics.xml` | 10 | `fallback` | RSS item: `title`, `author`, `link`, `description` |

字段完整度：

| 来源 | title | url | published_at | summary | author | raw_metrics |
|---|---:|---:|---:|---:|---:|---:|
| 人民网 | 100% | 100% | 100% | 100% | 100% | 0% |
| 中国新闻网 | 100% | 100% | 100% | 100% | 0% | 0% |
| 新华网 | 100% | 100% | 0% | 100% | 100% | 0% |

## 5 条新闻样例

| # | 来源 | 栏目 | 标题 | 发布时间 | 作者 | 质量标记 |
|---:|---|---|---|---|---|---|
| 1 | 人民网 | 时政 | [学习卡丨“人不负青山，青山定不负人”](http://politics.people.com.cn/n1/2025/0605/c1001-40494898.html) | `2025-06-05` | 人民网 | 无 |
| 2 | 人民网 | 时政 | [镜观·足迹｜呵护千山万水 擘画永续发展](http://politics.people.com.cn/n1/2025/0605/c1001-40494899.html) | `2025-06-05` | 人民网 | 无 |
| 3 | 中国新闻网 | 即时新闻 | [“工业互联网+绿色低碳”融合创新发展论坛在沈阳举办](https://www.chinanews.com.cn/cj/2026/09-10/10693964.shtml) | `2026-09-10T15:00:03+08:00` | 缺失 | `missing_author` |
| 4 | 中国新闻网 | 即时新闻 | [“阅·见西部”第三季全民阅读推广活动在新疆哈密举办](https://www.chinanews.com.cn/cul/2026/09-10/10693961.shtml) | `2026-09-10T15:00:03+08:00` | 缺失 | `missing_author` |
| 5 | 新华网 | 时政 | [微视频｜“新”在中国](http://www.news.cn/politics/2022-12/14/c_1129207254.htm) | 缺失 | `www.xinhuanet.com` | `missing_published_at` |

说明：

- 人民网 RSS 结构最完整，但当前 `politics.xml` 返回的条目时间明显较旧，应在 Collector 阶段继续寻找更新频率更合适的人民网频道入口。
- 中国新闻网即时新闻 RSS 当前性最好，适合优先验证实时新闻 Collector。
- 新华网 RSS 可提供标题、链接、作者和摘要，但当前入口缺少 `pubDate`，因此更适合作为降级或权威补证来源。

## 官媒原始结构

官媒样例的原始层主要来自 RSS `item`：

```text
RSS channel
└── item[]
    ├── title
    ├── link
    ├── pubDate optional
    ├── author optional
    └── description
```

映射到项目统一结构后：

```json
{
  "source_id": "people_politics_rss",
  "source_name": "人民网",
  "source_type": "official_news",
  "source_status": "use",
  "platform": "people.cn",
  "channel": "时政",
  "rank": null,
  "title": "新闻标题",
  "url": "新闻链接",
  "published_at": "发布时间或 null",
  "fetched_at": "采集时间",
  "author": "媒体/作者或 null",
  "summary": "RSS description，可含 HTML",
  "content_text": null,
  "raw_metrics": null,
  "raw_payload": {
    "rss_item": "原始 RSS item 字段"
  },
  "quality_flags": [],
  "signal_role": "evidence_signal",
  "score_contribution_role": "evidence / authority / coverage"
}
```

## 与知乎/微博结构的差异

| 维度 | 官媒新闻 | 微博热搜 / 微博搜索 | 知乎热榜 / 搜索 |
|---|---|---|---|
| 核心对象 | 新闻报道 | 话题或微博正文 | 问题 / 回答 / 搜索结果 |
| 必备字段稳定性 | 标题、链接稳定 | 话题名稳定，正文依赖 CLI 搜索 | 标题、链接稳定，指标依赖 API |
| 发布时间 | RSS 中常见，但不同源差异大 | 微博正文可有，热搜话题未必有 | hot_list 通常缺失，search 可有 edit time |
| 作者字段 | 媒体名或缺失 | 用户 / 博主 | 作者名，取决于接口 |
| 热度字段 | 通常没有 | 排名、评论、转发、点赞等 | 排名、赞同、评论、RankingScore 等 |
| 事实角色 | 事实确认、权威报道、引用证据 | 关注度 / 传播信号，不应单独当事实 | 讨论焦点 / 问题信号，不应单独当事实 |

## Schema 结论

官媒 Collector 应按“新闻证据源”设计，而不是按“热榜源”设计。

第一阶段必填字段：

```text
source_id
source_name
source_type = official_news
source_status
platform
title
url
fetched_at
```

第一阶段可选字段：

```text
channel
published_at
author
summary
content_text
raw_metrics
raw_payload
quality_flags
```

处理规则：

- `raw_metrics` 必须保持可选，官媒 RSS 通常不提供阅读量、评论数或热度值。
- `summary` 可能包含 HTML，应在标准化层保留原始值，同时提供可选的纯文本清洗结果。
- `published_at` 不能作为硬性必填。人民网、中国新闻网可用；新华网当前入口缺失。
- `author` 不能作为硬性必填。中国新闻网当前 RSS 未提供作者。
- 官媒对热度评分的贡献应来自 `evidence_score`、`authority_score`、`coverage_score` 和时效性，而不是平台热度值。
- 官媒报道可以提升事实置信度，但不等于“公众高度关注”；公众关注仍应由微博、知乎等社区信号补充。

## 曝光度字段补充

本次补充检查了 RSS、5 条文章页 HTML、外链脚本和少量公开 JSON。结论是：当前 3 个官媒 RSS 源没有稳定的“单篇阅读数 / 浏览数 / 点击量”字段。

观察到的相关结构：

| 来源 | 观察到的曝光相关结构 | 是否可作为单篇阅读数 | 判断 |
|---|---|---|---|
| 人民网 | 文章页包含 `http://counter.people.cn:8000/c.gif?id=<article_id>` 和 `webdig_test.js` 统计脚本 | 否 | 这是站方统计埋点，返回空 GIF，不返回计数值 |
| 中国新闻网 | 文章页包含 `newsid`、`aiCommentId`、评论列表接口、频道 `hot_news_list` 接口 | 否 | 评论接口可返回评论样本，热榜接口只返回 `id/title/shareUrl`，没有阅读数 |
| 新华网 | `json/bangdan/top1.json` 榜单结构包含 `clickCount` 字段 | 暂不可用 | 当前样例中 `clickCount = null`，只能把榜单顺序作为弱曝光代理 |
| 央视新闻 H5 | 前端包中存在 `viewCount`、评论、点赞和文章信息接口名 | 不建议第一阶段使用 | 依赖客户端 H5 / EMAS 接口语义，不适合作为公开稳定 Collector 依赖 |

可纳入第一阶段的弱代理：

```text
news_list_position          # RSS 或列表页位置，只能反映编辑/列表排序
source_coverage_count       # 同一事件被多少官媒源报道
source_authority_weight     # 来源权重，如人民网/新华网/央视网
recency_score               # 发布时间与采集时间的距离
repeated_reporting_count    # 同源或跨源重复报道次数
chinanews_hot_list_position # 中国新闻网公开 hot_news_list 排序，可选弱代理
xinhua_bangdan_position     # 新华网公开榜单排序，可选弱代理
```

不建议纳入第一阶段的字段：

```text
read_count
view_count
click_count
pv
uv
```

原因：

- RSS 没有这些字段。
- 文章页公开 HTML 没有直接展示这些数值。
- 埋点接口能证明站方统计访问，但不能稳定读取计数。
- 部分前端包出现 `viewCount` 或 `clickCount` 字段名，但样例没有公开数值，或依赖客户端接口。
- 强行使用这类字段会把私有统计、客户端接口和公开新闻采集混在一起，稳定性和合规边界都较差。

## 接入建议

优先级：

1. 中国新闻网 RSS：优先做实时新闻 Collector 验证，当前性和字段稳定性最好。
2. 人民网 RSS：适合作为权威源，但需要更换或补充更新频率更好的频道入口。
3. 新华网 RSS：作为 `fallback` 权威补证源接入，允许 `published_at = null`。

建议新增/确认的质量标记：

```text
missing_published_at
missing_author
html_summary
stale_feed_candidate
no_raw_metrics
```

建议的 `EventSignal` 映射：

```text
signal_role = evidence_signal
score_contribution_role = evidence / authority / coverage
confidence_score 根据 source_status、字段完整度、链接可访问性和发布时间质量计算
```

## 后续匹配设计补充

官媒标题通常比社区话题更接近新闻事件描述，但和微博短话题、知乎问题标题之间可能存在表达差异。后续事件合并应采用 `docs/event-matching.md` 中的混合匹配链路：

```text
标题 / 实体 / 时间规则
-> BM25 / n-gram 可解释召回
-> embedding 语义召回或 rerank
-> Guardrails 判断自动合并、人工复核或拒绝
```

官媒支撑不能只靠 embedding 相似度确认。只有当官媒标题 / 摘要与社区候选在实体、动作、对象和时间窗口上足够一致时，才能标记 `official_support_status = supported`；字段缺失、来源 fallback 或只命中背景报道时只能标记 `weak_supported` 或进入人工复核。
