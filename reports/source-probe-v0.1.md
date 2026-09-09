# 数据源探测 v0.1

生成时间：`2026-09-06T08:49:38.757236+00:00`

## 范围

Day 15 对已选官媒 / 新闻源和文本 BBS / 社区源做 smoke test：

- 人民网
- 中国新闻网
- 新华网
- 微博热搜
- 知乎热榜

本次运行只使用公开 RSS endpoint 和公开网页。不使用账号、Cookie、Token、私有 API、代理配置或反爬绕过技术。

## 方法

- 每个来源抓取上限：`10`
- 每个来源链接稳定性检查数量：`5`
- 第一阶段必需字段检查：`title`、`url`、`fetched_at`
- 可选但有价值字段检查：`published_at`、`summary`、`author`、`raw_metrics`
- 原始样本写入 `notes/source-probe-raw/`，必须仅本地保留。

## 结果汇总

| 来源 | 建议状态 | 条目数 | Title | URL | 发布时间 | 摘要 | 链接可访问 | 时间质量 | 热度指标 |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| 人民网 | use | 10 | 100% | 100% | 100% | 100% | 100% | high | no |
| 中国新闻网 | use | 10 | 100% | 100% | 100% | 100% | 100% | high | no |
| 新华网 | fallback | 10 | 100% | 100% | 0% | 100% | 100% | missing | no |
| 微博热搜 | postpone | 0 | 0% | 0% | 0% | 0% | 0% | missing | no |
| 知乎热榜 | postpone | 0 | 0% | 0% | 0% | 0% | 0% | missing | no |

## 来源说明

### 人民网

- Endpoint: `http://www.people.com.cn/rss/politics.xml`
- HTTP: `200` / `text/xml` / `382202` bytes
- Parse: `ok`
- 建议状态：`use`
- 说明：
- RSS 未提供热度指标，官媒报道强度应由报道数量、来源权重和时效性计算。

### 中国新闻网

- Endpoint: `https://www.chinanews.com.cn/rss/scroll-news.xml`
- HTTP: `200` / `text/xml` / `18021` bytes
- Parse: `ok`
- 建议状态：`use`
- 说明：
- RSS 未提供热度指标，官媒报道强度应由报道数量、来源权重和时效性计算。

### 新华网

- Endpoint: `http://www.xinhuanet.com/politics/news_politics.xml`
- HTTP: `200` / `text/xml; charset=utf-8` / `174092` bytes
- Parse: `ok`
- 建议状态：`fallback`
- 说明：
- RSS 未提供热度指标，官媒报道强度应由报道数量、来源权重和时效性计算。

### 微博热搜

- Endpoint: `https://passport.weibo.com/visitor/visitor?entry=miniblog&a=enter&url=https%3A%2F%2Fs.weibo.com%2Ftop%2Fsummary%3Fcate%3Drealtimehot&domain=.weibo.com&sudaref=&ua=php-sso_sdk_client-0.6.29&_rand=1788684577.8349`
- HTTP: `200` / `text/html` / `9666` bytes
- Parse: `failed`
- 建议状态：`postpone`
- 说明：
- 公开页面直连触发登录、访客或风控校验，未采集样本。
- 第一阶段仍选择该文字型社区源，但正式接入前需要找到稳定公开入口。

### 知乎热榜

- Endpoint: `https://www.zhihu.com/hot`
- HTTP: `403` / `text/html` / `650` bytes
- Parse: `failed`
- 建议状态：`postpone`
- 说明：
- 公开页面直连触发登录、访客或风控校验，未采集样本。
- 第一阶段仍选择该文字型社区源，但正式接入前需要找到稳定公开入口。

## 决策

- `中国新闻网`：保留为首批 Collector 候选，因为 RSS 能提供当前、多领域的新闻，并具备稳定的标题、URL 和发布时间字段。
- `人民网`：保留为优先官媒来源。RSS 可访问，字段质量足以支撑第一阶段 `NormalizedItem`；评分时应使用官媒权重逻辑，而不是 raw heat metrics。
- `新华网`：保留为优先官媒来源。RSS 可访问，适合做权威事件确认，但后续 Collector 应预期不同频道的结构差异。
- `微博热搜`：保留为已选文本社区来源，但当前公开页面直连访问需要后续验证，暂不进入生产采集。
- `知乎热榜`：保留为已选文本社区来源，但当前公开页面直连访问需要后续验证，暂不进入生产采集。

## Schema 影响

- 对官媒 / 新闻源，`raw_metrics` 必须保持可选。
- 本次 RSS 探测中 `published_at` 可用，但 schema 仍应允许缺失，因为非 RSS 列表页可能因频道不同而变化。
- 不同来源的 `summary` 可用性不同，不能让摘要缺失阻断标准化。
- 官媒 / 新闻源的热度应由来源覆盖、来源权重、时效性和重复报道推导，而不是依赖平台提供的 hot value。
- 文本社区来源应使用 `rank`、`title`、`url` 和可选的平台热度值；它们应作为热度 / 讨论信号，而不是独立事实来源。