# Crawl Validation Report v0.1

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
