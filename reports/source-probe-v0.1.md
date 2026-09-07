# Source Probe v0.1

Generated at: `2026-09-06T08:49:38.757236+00:00`

## Scope

Day 15 smoke test for selected official/news and text BBS/community sources:

- 人民网
- 中国新闻网
- 新华网
- 微博热搜
- 知乎热榜

This run uses public RSS endpoints and public web pages only. It does not use
accounts, cookies, tokens, private APIs, proxy configuration, or anti-bot bypass
techniques.

## Method

- Fetch limit per source: `10`
- Link stability checks per source: `5`
- Required first-stage fields checked: `title`, `url`, `fetched_at`
- Optional but valuable fields checked: `published_at`, `summary`, `author`, `raw_metrics`
- Raw samples are written to `notes/source-probe-raw/` and must remain local-only.

## Result Summary

| Source | Suggested status | Items | Title | URL | Published time | Summary | Links OK | Time quality | Heat metrics |
|---|---|---:|---:|---:|---:|---:|---:|---|---|
| 人民网 | use | 10 | 100% | 100% | 100% | 100% | 100% | high | no |
| 中国新闻网 | use | 10 | 100% | 100% | 100% | 100% | 100% | high | no |
| 新华网 | fallback | 10 | 100% | 100% | 0% | 100% | 100% | missing | no |
| 微博热搜 | postpone | 0 | 0% | 0% | 0% | 0% | 0% | missing | no |
| 知乎热榜 | postpone | 0 | 0% | 0% | 0% | 0% | 0% | missing | no |

## Source Notes

### 人民网

- Endpoint: `http://www.people.com.cn/rss/politics.xml`
- HTTP: `200` / `text/xml` / `382202` bytes
- Parse: `ok`
- Suggested status: `use`
- Notes:
- RSS 未提供热度指标，官媒报道强度应由报道数量、来源权重和时效性计算。

### 中国新闻网

- Endpoint: `https://www.chinanews.com.cn/rss/scroll-news.xml`
- HTTP: `200` / `text/xml` / `18021` bytes
- Parse: `ok`
- Suggested status: `use`
- Notes:
- RSS 未提供热度指标，官媒报道强度应由报道数量、来源权重和时效性计算。

### 新华网

- Endpoint: `http://www.xinhuanet.com/politics/news_politics.xml`
- HTTP: `200` / `text/xml; charset=utf-8` / `174092` bytes
- Parse: `ok`
- Suggested status: `fallback`
- Notes:
- RSS 未提供热度指标，官媒报道强度应由报道数量、来源权重和时效性计算。

### 微博热搜

- Endpoint: `https://passport.weibo.com/visitor/visitor?entry=miniblog&a=enter&url=https%3A%2F%2Fs.weibo.com%2Ftop%2Fsummary%3Fcate%3Drealtimehot&domain=.weibo.com&sudaref=&ua=php-sso_sdk_client-0.6.29&_rand=1788684577.8349`
- HTTP: `200` / `text/html` / `9666` bytes
- Parse: `failed`
- Suggested status: `postpone`
- Notes:
- 公开页面直连触发登录、访客或风控校验，未采集样本。
- 第一阶段仍选择该文字型社区源，但正式接入前需要找到稳定公开入口。

### 知乎热榜

- Endpoint: `https://www.zhihu.com/hot`
- HTTP: `403` / `text/html` / `650` bytes
- Parse: `failed`
- Suggested status: `postpone`
- Notes:
- 公开页面直连触发登录、访客或风控校验，未采集样本。
- 第一阶段仍选择该文字型社区源，但正式接入前需要找到稳定公开入口。

## Decision

- `中国新闻网`: keep as first collector candidate because RSS gives current multi-domain
  news with stable title, URL, and publication time fields.
- `人民网`: keep as priority official source. RSS is accessible and field quality is
  sufficient for a first-stage `NormalizedItem`; use official-score logic rather than
  raw heat metrics.
- `新华网`: keep as priority official source. RSS is accessible and suitable for
  authoritative event confirmation, but later collector work should expect channel-level
  structure differences.
- `微博热搜`: keep as selected text community source, but current direct public page
  access requires follow-up validation before production collection.
- `知乎热榜`: keep as selected text community source, but current direct public page
  access requires follow-up validation before production collection.

## Schema Impact

- `raw_metrics` must remain optional for official/news sources.
- `published_at` is available in this RSS-based probe, but schema should still allow
  missing values because non-RSS list pages may vary by channel.
- `summary` availability differs by source and should not block normalization.
- Official/news source heat should be derived from source coverage, source weight,
  recency, and repeated reporting rather than platform-provided hot values.
- Text community sources should use `rank`, `title`, `url`, and optional
  platform-provided heat values; they must remain heat/discussion signals rather than
  standalone fact sources.
