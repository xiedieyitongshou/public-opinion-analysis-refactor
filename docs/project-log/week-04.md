# Week 04 Project Log

2026-09-29 补充：Day48–49 已对当前实现构建并运行评估，见 [用例说明](../evaluation-cases-v0.2.md) 与 [评估报告](../../reports/evaluation-v0.1.md)。下文保留当时的采集阶段记录。

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

## 2026-09-30：三模块匹配改进与复评记录

本节记录后续集成修复，不改写第四周当时的完成状态。

- 数据接口：统一嵌套／旧字段读取；缺失实体与动作不再产生虚假重叠；修复知乎短榜／空榜完整性和未知搜索质量误提置信度。
- 事件匹配：传递知乎父问题规范身份，确定身份绕过模型；实际执行 BM25、embedding 独立召回与 RRF、cross-encoder 重排；补充显式事实冲突及代表记录检查，高语义分不能单独决定合并。
- 官媒补证：在现有 `classify_events` Tool 中接入真实站点 query、预算、缓存、候选落库和引用审计。2026-09-29 中新网八次请求成功；人民网四次 HTTP 405，保留失败降级。
- 测试：237 项 pytest 通过，变更 Python 文件 Ruff 通过，`pip check` 与 `git diff --check` 通过。模型推理使用真实本地权重；普通回归测试隔离模型／网络。
- 评估：v0.3 共 208 条。规则版通过 190 条，hybrid 通过 191 条，hybrid_rerank 通过 193 条，均无执行错误。原 134 条由 118 提升到 132 条，修复 14 条且无新增回退。完整配置的合并 TP/FP/FN/TN=25/0/10/46；官媒=10/0/4/32。
- 边界：仍有自动合并漏召回、官媒漏检；旧新闻入池是待确定的产品策略。初标未人工复核，扩充集已经参与诊断，不能宣传为未触碰测试集或生产准确率；没有验证长期采集可用率。

交付：[评估 v0.2](../../reports/evaluation-v0.2.md)、[用例 v0.3](../evaluation-cases-v0.3.md)、[实现说明](../matching-upgrade.md)、[更新日志](../../CHANGELOG.md)。
