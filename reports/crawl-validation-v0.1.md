# 采集器验收基线 v0.1

[返回项目首页](../README.md) · [文档导航](../README.md#文档导航) · [验证记录](../README.md#验证记录)

> 历史记录：本文保留采集阶段 Day 29–35 的验收口径，并合并原周日志中的采集实现记录。后续功能进展见[更新日志](../CHANGELOG.md)，当前行为见[数据源说明](../docs/data-sources.md)。下文的待办描述属于当时状态。

## Purpose

This report records the Day 35 crawl-validation baseline for the MVP collector layer. It is a public engineering summary, not a dump of raw samples. Raw payloads, credentials, private endpoints, cookies, and unstable bypass details remain outside the public repository.

The validation target is the collector chain built through Day 29-34:

```text
Planner
-> Collector Agent
-> fetch_source_items tool
-> CollectorRegistry
-> source collectors
-> CrawlValidationResult
-> CrawlValidationRun
```

## Validation Mode Contract

`crawl_validation` is designed as a safe smoke-test path:

- `validate_only=true`
- `dry_run=true`
- `promote_to_event_pool=false`
- source-specific small limits
- postponed sources skipped by scheduler gate
- raw sample manifest recorded as metadata only

Even if a caller passes `promote_to_event_pool=true`, validation mode forces it back to false. This prevents smoke tests from polluting the formal item and event pools.

## Source Coverage

| Source ID | Source class | Status gate | Default validation behavior | Expected output |
|---|---|---|---|---|
| `zhihu_hot_list` | `ZhihuHotListCollector` | `use` | small TopN request through official API contract | normalized community hot-list items |
| `zhihu_search` | `ZhihuSearchCollector` | `use` | only runs with explicit query/candidate target | accepted or audit-only search enrichment items |
| `weibo_rsshub_hot_search` | `WeiboHeatCollector` | `use` | RSSHub topic seed fetch, optional CLI enrichment | weak topic-discovery signals with quality flags |
| `chinanews_scroll_rss` | `OfficialRSSCollector` | `use` | RSS fixture / low-frequency RSS smoke path | official evidence items |
| `people_politics_rss` | `OfficialRSSCollector` | `use` | RSS fixture / low-frequency RSS smoke path | official evidence items |
| `xinhua_politics_rss` | `OfficialRSSCollector` | `fallback` | RSS fixture / low-frequency fallback source | official evidence items with fallback flags |
| `weibo_direct_hot_search` | not registered | `postpone` | scheduler skip | skipped result, no fetch |

## Baseline Metrics

The current public baseline is test-backed rather than a live production measurement. Live counts depend on credentials, RSSHub availability, and external source health.

| Metric | Baseline expectation | Current covered behavior |
|---|---|---|
| Source registration success | all `use` / `fallback` collectors can register | covered by registry and default collector construction |
| Postpone gate | `postpone` source cannot be registered as real collector | covered |
| Validation success rate | mixed success/failure/skipped is represented as `succeeded`, `partial`, `failed`, or `skipped` | covered |
| Field completeness | summary includes weighted completeness over normalized items | covered |
| Quota reporting | schema reserves `quota_before` and `quota_after` | schema present; live quota integration pending |
| Raw sample replay | schema reserves `raw_sample_path` and validation run manifest | manifest shape present; public raw samples excluded |
| Tool-call logging | validation plan writes `AgentTask` and `AgentToolCall` records | covered |
| Persistence safety | validation does not save normalized items | covered |
| Event-pool promotion | daily collection can persist items when explicitly promoted | covered |

## Field Completeness Baseline

Required fields for downstream normalization and event extraction:

- `source_id`
- `source_name`
- `source_type`
- `source_status`
- `source_origin`
- `platform`
- `signal_role`
- `signal_contribution_role`
- `title`
- `fetched_at`
- `content_hash`
- `source_citation`
- `quality_flags`

Optional or source-dependent fields:

- `url`
- `summary`
- `content`
- `published_at`
- `author`
- `rank`
- `raw_metrics`
- `normalized`

Handling rules:

- Missing official-news heat metrics are not backfilled as public attention values.
- Missing RSS publication time is represented as `missing_published_at` or source-specific fallback flags.
- Weibo RSSHub list order is recorded as RSS item order, not official Weibo heat score.
- Weibo CLI enrichment is a supplement for known topics, not an independent hotspot source.
- Search enrichment without explicit parent query or candidate is skipped.

## Source-Specific Notes

### Zhihu hot_list

The collector normalizes official API items into community hot-list records. Rank is preserved as a platform-local signal. Missing credentials are surfaced as source failure rather than silently falling back to mock output.

### Zhihu search

The collector requires explicit `query`, `queries`, or `candidates`. Results are evaluated against their parent candidate and may become audit-only. This keeps search enrichment from becoming an uncontrolled broad crawler.

### Weibo RSSHub + CLI

RSSHub provides topic seeds and weak attention signals. CLI enrichment can add representative statuses and interaction samples when available. CLI failure lowers signal quality but should not drop the RSSHub topic seed if RSSHub itself succeeded.

### Official RSS

Official sources contribute evidence, authority, and coverage signals. They do not provide stable public heat metrics in the MVP. Publication time, URL, author, and summary gaps are represented through quality flags.

## Baseline Test Commands

Validated command set for this baseline:

```powershell
..\.venv\Scripts\python.exe -m pytest tests/test_day29_collectors.py tests/test_day30_31_zhihu_collectors.py tests/test_day32_weibo_collector.py tests/test_day33_official_collectors.py tests/test_day34_collector_agent.py -q
..\.venv\Scripts\python.exe -m ruff check app tests
```

## Open Follow-Ups

- Add a sanitized raw-sample replay fixture format if live smoke tests expose parser regressions.
- Persist source quota usage once real API quota responses are available.
- Add a scheduled low-frequency crawl-validation command for deployment.
- Feed validation summaries into the future Agent Ops panel.
- Convert repeated live failures into `source_status` review tasks instead of ad hoc code changes.

## 采集阶段实现记录

原周日志对应的交付顺序为：Day 29 定义 Collector 协议与注册边界；Day 30–31 接入知乎热榜／搜索；Day 32 接入微博 RSSHub 与可选 CLI；Day 33 接入三家官媒 RSS；Day 34 接通采集 Agent；Day 35 验证来源闸门、字段完整性和执行记录。来源清单、测试范围及缺失处理统一保留在本文前述章节。

`CollectorRegistry` 拒绝注册 `postpone` 来源，调度器同时处理 `postponed_source_ids`。验证计划不落正式条目池；普通采集只有显式开启 promotion 且不是 dry run 时才持久化条目。`AgentTaskRunner`／`ToolRegistry` 保存任务与调用记录，`CrawlValidationRun` 保存请求、跳过和不可用来源、字段完整率、贡献角色及样本清单元数据。

下游交接包含来源身份、候选文字、引用、质量标记及平台指标：知乎保留 rank 和搜索互动；微博保留 RSS presence／列表顺序与可选 CLI 样本；官媒保留发布时间、来源和报道依据。Day 36 起的归一化与事件抽取另行处理，不能把当时采集器就绪等同于完整日报已完成。

该阶段的可回放测试还覆盖 CLI 失败保留 RSS 种子、无搜索目标时跳过、缺少凭据时明确失败，以及缺少作者／发布时间时保留质量标记。它们证明协议和降级分支可运行，没有证明外部来源长期可用。阶段交付为 `collector-agent-v0.1`，适合低频 smoke test；后续真实联调和产品验收从[首页验证记录](../README.md#验证记录)进入。
