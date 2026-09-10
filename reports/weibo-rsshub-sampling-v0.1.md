# 微博 RSSHub 热搜采样 v0.1

生成时间：`2026-09-08T08:22:46.105276+00:00`

## 范围

Day 18 针对 RSSHub 微博热搜路径做 smoke test 和字段结构审计。

本次验证两个 RSSHub 路径：

- 基础热搜列表：`/weibo/search/hot`
- fulltext 摘要增强：`/weibo/search/hot/fulltext`

本次运行只通过 RSSHub 获取 RSS XML，不使用微博账号 cookie，不配置代理池，
不做登录自动化或反爬绕过。原始样本仅本地保留，不适合提交到公开仓库。

## 方法

- RSSHub base URL：`http://localhost:1200`
- 每个路径抓取上限：`20`
- HTTP timeout：`20.0` 秒
- 最大重试：`2`
- 连续失败降级阈值草案：`3`
- 原始本地样本：`D:\desktop\public-opinion-analysis-refactor\notes\source-probe-raw\weibo-rsshub-sampling.json`
- 字段审计：`title`、`link`、`description`、`pubDate`、`guid`、item 顺序、可解析 `hot_value`

## 结果汇总

| API / Route | Suggested status | Items | Title | URL | Description | pubDate | guid | hot_value |
|---|---|---:|---:|---:|---:|---:|---:|---:|
| 基础热搜列表 `/weibo/search/hot` | use | 20 | 100% | 100% | 100% | 0% | 100% | 0% |
| fulltext 摘要增强 `/weibo/search/hot/fulltext` | use | 20 | 100% | 100% | 0% | 0% | 100% | 0% |

## 两个 API 分别获得了什么数据

### 基础热搜列表 `/weibo/search/hot`

- Endpoint: `http://localhost:1200/weibo/search/hot`
- HTTP: `200` / `application/xml; charset=utf-8` / `24494` bytes
- Parse: `ok`
- Suggested status: `use`
- Observed items: `20`
- Notes:
- 不使用 WEIBO_COOKIES、微博账号 cookie、代理池或反爬绕过逻辑。
- 请求尝试次数：1。
- 可用 RSS item 顺序生成 rank。
- 未稳定观察到可解析 hot_value，后续应保持 optional。
- 未观察到 item 级 pubDate，后续由 fetched_at 记录采集时间。

| Rank | Title | URL | Description | pubDate | hot_value |
|---:|---|---|---|---|---:|
| 1 | `此身长报国` | [link](https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D%E6%AD%A4%E8%BA%AB%E9%95%BF%E6%8A%A5%E5%9B%BD) | `此身长报国` |  |  |
| 2 | `网红宣传捐款百万实际只捐1元` | [link](https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D%E7%BD%91%E7%BA%A2%E5%AE%A3%E4%BC%A0%E6%8D%90%E6%AC%BE%E7%99%BE%E4%B8%87%E5%AE%9E%E9%99%85%E5%8F%AA%E6%8D%901%E5%85%83) | `网红宣传捐款百万实际只捐1元` |  |  |
| 3 | `苹果折叠手机价格` | [link](https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D%E8%8B%B9%E6%9E%9C%E6%8A%98%E5%8F%A0%E6%89%8B%E6%9C%BA%E4%BB%B7%E6%A0%BC) | `苹果折叠手机价格` |  |  |
| 4 | `e法同行兴辽治宁` | [link](https://m.weibo.cn/search?containerid=100103type%3D1%26q%3De%E6%B3%95%E5%90%8C%E8%A1%8C%E5%85%B4%E8%BE%BD%E6%B2%BB%E5%AE%81) | `e法同行兴辽治宁` |  |  |
| 5 | `为什么子女买房会把父母安排在次卧` | [link](https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D%E4%B8%BA%E4%BB%80%E4%B9%88%E5%AD%90%E5%A5%B3%E4%B9%B0%E6%88%BF%E4%BC%9A%E6%8A%8A%E7%88%B6%E6%AF%8D%E5%AE%89%E6%8E%92%E5%9C%A8%E6%AC%A1%E5%8D%A7) | `为什么子女买房会把父母安排在次卧` |  |  |
| 6 | `苹果 华为` | [link](https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D%E8%8B%B9%E6%9E%9C%20%E5%8D%8E%E4%B8%BA) | `苹果 华为` |  |  |
| 7 | `吃播圈催吐导泄都造成血钾暴跌` | [link](https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D%E5%90%83%E6%92%AD%E5%9C%88%E5%82%AC%E5%90%90%E5%AF%BC%E6%B3%84%E9%83%BD%E9%80%A0%E6%88%90%E8%A1%80%E9%92%BE%E6%9A%B4%E8%B7%8C) | `吃播圈催吐导泄都造成血钾暴跌` |  |  |
| 8 | `拉面范中国国家队的选择` | [link](https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D%E6%8B%89%E9%9D%A2%E8%8C%83%E4%B8%AD%E5%9B%BD%E5%9B%BD%E5%AE%B6%E9%98%9F%E7%9A%84%E9%80%89%E6%8B%A9) | `拉面范中国国家队的选择` |  |  |

### fulltext 摘要增强 `/weibo/search/hot/fulltext`

- Endpoint: `http://localhost:1200/weibo/search/hot/fulltext`
- HTTP: `200` / `application/xml; charset=utf-8` / `22474` bytes
- Parse: `ok`
- Suggested status: `use`
- Observed items: `20`
- Notes:
- 不使用 WEIBO_COOKIES、微博账号 cookie、代理池或反爬绕过逻辑。
- 请求尝试次数：1。
- 可用 RSS item 顺序生成 rank。
- 未稳定观察到可解析 hot_value，后续应保持 optional。
- 未观察到 item 级 pubDate，后续由 fetched_at 记录采集时间。
- fulltext 路径会额外抓取热搜词下内容，耗时和失败概率高于基础路径。

| Rank | Title | URL | Description | pubDate | hot_value |
|---:|---|---|---|---|---:|
| 1 | `此身长报国` | [link](https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D%E6%AD%A4%E8%BA%AB%E9%95%BF%E6%8A%A5%E5%9B%BD) | `` |  |  |
| 2 | `网红宣传捐款百万实际只捐1元` | [link](https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D%E7%BD%91%E7%BA%A2%E5%AE%A3%E4%BC%A0%E6%8D%90%E6%AC%BE%E7%99%BE%E4%B8%87%E5%AE%9E%E9%99%85%E5%8F%AA%E6%8D%901%E5%85%83) | `` |  |  |
| 3 | `苹果折叠手机价格` | [link](https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D%E8%8B%B9%E6%9E%9C%E6%8A%98%E5%8F%A0%E6%89%8B%E6%9C%BA%E4%BB%B7%E6%A0%BC) | `` |  |  |
| 4 | `e法同行兴辽治宁` | [link](https://m.weibo.cn/search?containerid=100103type%3D1%26q%3De%E6%B3%95%E5%90%8C%E8%A1%8C%E5%85%B4%E8%BE%BD%E6%B2%BB%E5%AE%81) | `` |  |  |
| 5 | `为什么子女买房会把父母安排在次卧` | [link](https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D%E4%B8%BA%E4%BB%80%E4%B9%88%E5%AD%90%E5%A5%B3%E4%B9%B0%E6%88%BF%E4%BC%9A%E6%8A%8A%E7%88%B6%E6%AF%8D%E5%AE%89%E6%8E%92%E5%9C%A8%E6%AC%A1%E5%8D%A7) | `` |  |  |
| 6 | `苹果 华为` | [link](https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D%E8%8B%B9%E6%9E%9C%20%E5%8D%8E%E4%B8%BA) | `` |  |  |
| 7 | `吃播圈催吐导泄都造成血钾暴跌` | [link](https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D%E5%90%83%E6%92%AD%E5%9C%88%E5%82%AC%E5%90%90%E5%AF%BC%E6%B3%84%E9%83%BD%E9%80%A0%E6%88%90%E8%A1%80%E9%92%BE%E6%9A%B4%E8%B7%8C) | `` |  |  |
| 8 | `旅行青蛙即将停运` | [link](https://m.weibo.cn/search?containerid=100103type%3D1%26q%3D%E6%97%85%E8%A1%8C%E9%9D%92%E8%9B%99%E5%8D%B3%E5%B0%86%E5%81%9C%E8%BF%90) | `` |  |  |

## 与官媒 RSS 对比

微博 RSSHub 多了：

- 提供社区即时注意力入口，能直接补充官媒未报道或尚未充分报道的公众关注话题。
- 提供榜单顺序，可派生 `rank`，用于微博侧 `attention_signal` 和后续 `velocity` 计算。
- 链接指向微博搜索页，适合做事件候选发现和后续人工核验入口。
- 话题标题更短、更像搜索词，适合捕捉平台型争议、娱乐、消费、游戏和突发社会话题。

微博 RSSHub 少了或更弱：

- 没有稳定观察到 item 级 `pubDate`，不能直接提供事件发生时间或发布时间。
- 没有稳定观察到 `hot_value`，第一阶段不能把微博热度值作为必需评分输入。
- `description` 在基础路径中通常等于标题；fulltext 路径虽然可能增强摘要，但稳定性和成本更差。
- RSSHub 属第三方转换层，但当前微博热点获取方式采用 RSSHub + CLI，`source_status` 记为 `use`，
  不能像人民网 / 中国新闻网 RSS 那样作为强依赖。
- 微博热搜本身不是事实来源，只能作为关注信号；事实确认仍需官媒、新闻源或其它可引用证据补强。

官媒 RSS 多了：

- 人民网和中国新闻网在 Day 15 样本中提供稳定 `title`、`url`、
  `published_at` 和 `description`，更适合事实证据、时间线和来源引用。
- 官媒 RSS 的报道文本更完整，适合做事件确认、摘要生成和 `news_evidence_score`。
- 来源权威性更高，可作为 `authority_score` / `coverage_score` 的输入。

官媒 RSS 少了：

- 没有榜单顺序或社区热度指标，不能直接表示公众注意力。
- 对社区先发话题响应可能滞后，容易漏掉短周期平台争议。
- 不覆盖或低覆盖娱乐、游戏、亚文化和平台社区内部争议。

## Schema 影响

- 微博 RSSHub 应映射为 `source_origin = rsshub`、
  `source_status = use`、`signal_role = attention_signal`。
- `rank` 从 item 顺序生成，写入 `raw_metrics.rank`。
- `hot_value` 和 `published_at` 必须允许为空；缺失时分别写入
  `missing_hot_value`、`missing_pub_date`。
- fulltext 路径不应作为默认定时采集路径；基础路径足够支撑热榜候选发现，
  fulltext 只适合作为低频验证或人工辅助。
- 评分阶段只能先做平台内归一化，再进入事件级总分；不能把微博 rank、
  知乎互动数和官媒报道数量直接相加。

## 决策

- `weibo_direct_hot_search = postpone`
- `weibo_rsshub_hot_search = use`
- Day 33 可实现微博 RSSHub collector，但默认只使用 `/weibo/search/hot`。
- RSSHub 请求失败、403、503 或结构异常时跳过微博源，不阻塞新闻源和知乎源。
- RSSHub endpoint 保持环境变量化，允许本地、自建服务器或公共实例切换；公共实例不可作为生产强依赖。
