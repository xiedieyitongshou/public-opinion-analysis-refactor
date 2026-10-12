# 数据源探测与访问路径评估 v0.1

[返回项目首页](../README.md) · [文档导航](../README.md#文档导航) · [验证记录](../README.md#验证记录)

生成时间：`2026-09-06T08:49:38.757236+00:00`

本报告保留当时的公开入口探测，并合并同日访问稳定性记录中的来源选择结论。它只证明该次请求的结果；当前采集入口和来源状态见[数据源说明](../docs/data-sources.md)。

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

## 访问路径与来源选择

原访问稳定性记录的复查时间为 **2026-09-06 16:46 Asia/Shanghai**。它与本报告针对同一批来源，侧重“有结构化数据”和“存在可用访问路径”的区别，不是一组长期稳定性统计。

| 来源 | 当时的访问判断 | 接入决策 |
| --- | --- | --- |
| 人民网、中国新闻网 | 公开 RSS 可解析；中新网需容忍部分不规范 XML | 首批 `use` 来源 |
| 新华网 | 公开 RSS 可解析，但该端点缺少发布时间 | `fallback`，保留时间缺失标记 |
| 微博热搜 | 公开页面触发访客／登录流程或拒绝访问 | 直连路径 `postpone`，另行验证 RSSHub＋CLI |
| 知乎热榜 | 公开页面／API 探测返回 403／401 | 直连路径 `postpone`，另行验证数据平台接口 |

不能把两家可用官媒、一家 fallback 与两个不可用直连入口合称“五源稳定”。当时的 Collector 闸门只允许前三家进入真实来源开发；社区分支先采用模拟数据或等待授权路径验证。后续采用知乎数据平台与微博 RSSHub＋CLI 的依据另见[社区访问路径记录](weibo-zhihu-official-access-v0.1.md)。

该轮选择低频、可关闭的公开或授权工具链，避免把个人 Cookie、自动登录或代理池作为项目稳定运行的前提。登录状态和上游访问限制属于来源可用性约束；一次 HTTP 成功不能推出持续可用。
