# Week 04 Project Log

## Scope

Week 04 closed the collector implementation phase for the MVP data pipeline. The goal was not to maximize source coverage, but to make source collection observable, gated by `source_status`, and safe to run as either a validation job or a promoted daily collection job.

Covered implementation days:

- Day 29: Collector schema and registry boundary.
- Day 30-31: Zhihu `hot_list` and `zhihu_search` collectors.
- Day 32: Weibo RSSHub hot-search seed collector with optional CLI enrichment.
- Day 33: Official RSS collectors for ChinaNews, People, and Xinhua.
- Day 34: Collector Agent scheduling for daily collection and crawl validation.
- Day 35: Review of validation readiness, source gates, tool logs, and baseline evaluation inputs.

## Current Collector Surface

| Source | Source status | Runtime role | Contribution role | Day 35 status |
|---|---|---|---|---|
| `zhihu_hot_list` | `use` | community hot-list collector | attention, discussion, community hot candidate | implemented and covered by mocked API tests |
| `zhihu_search` | `use` | targeted search enrichment collector | discussion, interaction | implemented; skips without explicit target |
| `weibo_rsshub_hot_search` | `use` | topic discovery collector | weak attention seed | implemented with RSSHub fallback semantics |
| `chinanews_scroll_rss` | `use` | official RSS evidence collector | evidence, authority, coverage | implemented and covered by RSS fixture tests |
| `people_politics_rss` | `use` | official RSS evidence collector | evidence, authority, coverage | implemented and covered by RSS fixture tests |
| `xinhua_politics_rss` | `fallback` | official RSS evidence collector | evidence, authority, coverage | implemented with missing time fallback flags |
| `weibo_direct_hot_search` | `postpone` | not registered as real collector | none | skipped by scheduler gate |

## Validation Findings

The Day 35 review confirms these engineering properties:

- `CollectorRegistry` rejects sources marked `postpone`, so unstable direct Weibo hot-search scraping cannot be accidentally registered as a production collector.
- Scheduler-level `postponed_source_ids` are still respected, so an explicitly requested postponed source is returned as `skipped` instead of being fetched.
- `crawl_validation` plans force `validate_only=true`, `dry_run=true`, and `promote_to_event_pool=false`, even if the caller asks to promote validation output.
- Daily hotspot collection can promote normalized items into the item pool only when `promote_to_event_pool=true` and the run is not validation-only.
- Tool calls are recorded through `AgentTaskRunner` and `ToolRegistry`, giving each validation run an auditable `agent_tasks` and `agent_tool_calls` trail.
- `CrawlValidationRun` stores requested, skipped, unavailable source IDs, field completeness summary, contribution roles, validation summary, and a raw sample manifest placeholder.

## Field Readiness

The current collector outputs provide the minimum fields needed for the next pipeline stages:

- Stable source identity: `source_id`, `source_name`, `source_type`, `source_status`, `source_origin`, `platform`.
- Event-candidate text: `title`, `summary`, `content`, `event_text_for_match` after Day 36 normalization.
- Source citation: platform, source name, URL, source status, and quality flags.
- Community attention inputs: Zhihu rank, search discussion metrics, Weibo RSS item order, and optional CLI interaction sample metrics.
- Official evidence inputs: RSS source identity, link, publication time when available, summary text, and source authority weight.
- Guardrail inputs: `quality_flags`, `signal_role`, `signal_contribution_role`, and explicit source status.

Known gaps remain intentionally visible:

- Zhihu official APIs require credentials; tests use mocked API responses and configuration failure is surfaced as a failed source run.
- Weibo RSSHub can fail independently; collector output marks RSSHub route failure as source failure and CLI failure as degraded topic evidence.
- Official RSS sources do not expose public attention metrics. They must not be used as direct proxies for public heat.
- Xinhua RSS may miss publication time; this is flagged rather than backfilled with invented values.

## Evaluation Baseline

Baseline checks for Week 04 are implementation-level and replayable:

| Area | Baseline check | Current result |
|---|---|---|
| Collector registry | rejects `postpone` registration | covered |
| Validation mode | dry-run validation does not persist items | covered |
| Daily collection | promoted run persists items | covered |
| Zhihu hot-list | normalizes rank and source roles | covered with mocked API |
| Zhihu search | requires explicit target and records relation | covered with mocked API |
| Weibo RSSHub | parses topic seeds and list order semantics | covered with RSS fixture |
| Weibo CLI | failed CLI degrades without dropping RSSHub seed | covered with fake runner |
| Official RSS | handles missing author, missing pubDate, fallback source | covered with RSS fixtures |
| Tool logs | collector plan writes task and tool-call logs | covered |

## Release Boundary

This week should be treated as `collector-agent-v0.1` quality:

- It is ready for low-frequency smoke tests and validation plans.
- It is not yet a guarantee of continuous production source availability.
- Public reports should summarize source behavior and schema coverage, not include raw samples, credentials, cookies, private API details, or unstable scraping workarounds.

## Next Inputs

The next implementation stage can rely on the collector layer to provide normalized source-level items and validation metadata. Day 36+ should focus on deterministic normalization, `SourceSignal` / `EventSignal` extraction, event matching, merge guardrails, and later classification.
