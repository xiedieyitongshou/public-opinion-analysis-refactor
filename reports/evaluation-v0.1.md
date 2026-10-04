# 三模块系统评估报告 v0.1（Day48–49）

评估时间：2026-09-29T07:03:20.522305+00:00；固定业务窗口结束：2026-09-28T12:00:00+00:00。

本轮执行 **134 条用例**：118 条通过、16 条预期不符、0 条执行错误。不同类型用例不合成为系统总准确率。

这是 2026-09-29 改进前实现的离线基线。所有新增语义标签由助手整理，**待人工复核**，不是已经完成双人标注的 gold set。本轮未修改采集、匹配、分类或趋势业务规则。以下指标和失败清单保留评估当时的结果。

**后续进展（2026-09-30）：**[评估报告 v0.2](evaluation-v0.2.md#同集对照与真实模型执行)在原有 134 条用例中修复了 14 条，包含本报告的 5 条官媒误匹配；原有 13 条官媒用例的 FP 从 5 降为 0。扩充到 46 条官媒用例后，完整配置为 TP=10、FP=0、FN=4、TN=32。官媒误匹配的改善主要来自[字段读取和证据判定规则的修复](../docs/matching-upgrade.md#官媒误匹配的字段与判定规则改动)；仅用修复后的规则，扩充集的官媒 FP 也为 0，不能把这一改善单独归因于 rerank。此处的旧结果不回填为新结果。

## 评估范围与证据

- 真实来源：2026-09-28 已有采集记录。公共用例集保留 26 条标题、URL、时间及指标摘录，其中 20 个核心候选主题按事件组分为 dev 14 / holdout 6；另外复用 6 对 Day27 历史标题标签。
- 摘录回放会省略正文；真实摘录、历史标题对、衍生样例和合成场景分别统计。同一事件的变体不跨分区。holdout 只是预留分区，本轮未调参，也不声称盲评。
- 模块评估直接调用现有归一化、规则抽取、事件合并、官媒匹配、分类、热度和趋势实现。完整链路使用现有九步 Agent Plan/Runner/Tools，仅替换采集返回值。
- 采集协议测试使用实际 Collector 和模拟传输层；禁用网络、LLM 和 embedding，使用独立 SQLite 内存库。本轮没有请求知乎 API 或微博 CLI，没有消耗其额度。

## 分项结果

| 测试范围 | 用例数 | 通过 | 预期不符 | 执行错误 |
|---|---:|---:|---:|---:|
| 类别规则 | 8 | 7 | 1 | 0 |
| 采集解析与失败语义 | 9 | 7 | 2 | 0 |
| 平台热度边界与排序 | 8 | 8 | 0 | 0 |
| 事件自动合并 | 48 | 43 | 5 | 0 |
| 归一化、时间与引用接口 | 20 | 20 | 0 | 0 |
| 官媒证据相关性 | 13 | 8 | 5 | 0 |
| 持续时间与趋势 | 18 | 18 | 0 | 0 |
| 完整九步链路 | 10 | 7 | 3 | 0 |

分类规则的类别准确率为 100.0%，但 1 个置信度检查中有 1 个未通过。上游的官媒相关性错误单独评估，不能用类别规则通过来证明最终分类正确。


## 事件合并

正例表示同一事件；预测正例要求直接合并且不待复核。转人工属于自动合并的弃权，在正例上计为 FN，同时单列复核数量。未合并的负例计 TN，不代表创建了正确的新事件。
候选对评估只给定一个候选事件，不能代表大规模候选召回或聚类整体效果。

| 来源 | 对数 | TP / FP / FN / TN | Precision | Recall | F1 |
|---|---:|---|---:|---:|---:|
| 全部（混合样本，仅供排查） | 48 | 20 / 0 / 5 / 23 | 100.0% | 80.0% | 88.9% |
| derived | 20 | 20 / 0 / 0 / 0 | 100.0% | 100.0% | 100.0% |
| legacy_title_pair | 6 | 0 / 0 / 3 / 3 | N/A（分母为 0） | 0.0% | 0.0% |
| real_excerpt | 22 | 0 / 0 / 2 / 20 | N/A（分母为 0） | 0.0% | 0.0% |

触发复核：6 对；Guardrail 状态：`{"pass": 20, "warn": 25, "block": 3}`。

分区和 matched_by 维度的计数见 JSON；一对样例可能同时命中多种方法，方法分组可以重叠，不是独立算法消融实验。embedding 未启用，不报 embedding 成绩。
当前真实摘录的两个正例来自同一个知乎问题的两个答案；预留 holdout 中的正例均为同 URL 变体，不能据此声称自然跨平台泛化能力。

## 官媒、趋势与工具执行

官媒支持判定：TP=4、FP=5、FN=0、TN=4；Precision=44.4%，Recall=100.0%。

合成时间线趋势方向准确率：100.0%；预期 unknown 的准确率：100.0%。持续时间 MAE：0.0 分钟（13 对数值结果；缺失预测 0）。这些结果验证时间规则，不能当成真实热点预测效果。

完整链路 Tool 调用状态：`{"succeeded": 117}`；业务任务状态：`{"succeeded": 89, "skipped": 26, "failed": 1, "partial": 1}`。Tool 调用成功只表示返回值符合接口；业务任务可为 partial/failed/skipped，不能据此认定结果正确。

## 预期不符及执行错误

| 用例 | 依据 | 预期与实际差异 |
|---|---|---|
| `match-answer-0` | 答案 URL 的 question ID 与热榜问题一致，标题也指向同一养老金事件。 | same_event: true → false |
| `match-answer-1` | 答案 URL 的 question ID 与热榜问题一致，标题也指向同一养老金事件。 | same_event: true → false |
| `match-legacy-1` | 复用 Day27 的同事件标签，仅提供历史标题对和占位 URL；不声称重放原始搜索响应。 | same_event: true → false |
| `match-legacy-2` | 复用 Day27 的同事件标签，仅提供历史标题对和占位 URL；不声称重放原始搜索响应。 | same_event: true → false |
| `match-legacy-3` | 复用 Day27 的同事件标签，仅提供历史标题对和占位 URL；不声称重放原始搜索响应。 | same_event: true → false |
| `official-unrelated-00-15` | 官媒条目没有报道目标事件，时间接近和泛化行动词不构成官媒支持。 | supported: false → true |
| `official-unrelated-02-15` | 官媒条目没有报道目标事件，时间接近和泛化行动词不构成官媒支持。 | supported: false → true |
| `official-unrelated-03-15` | 官媒条目没有报道目标事件，时间接近和泛化行动词不构成官媒支持。 | supported: false → true |
| `official-unrelated-06-15` | 官媒条目没有报道目标事件，时间接近和泛化行动词不构成官媒支持。 | supported: false → true |
| `official-unrelated-02-16` | 官媒条目没有报道目标事件，时间接近和泛化行动词不构成官媒支持。 | supported: false → true |
| `classification-rss-fallback` | CLI 失败时保留 RSS 存在信号，但未知的搜索质量不能作为增强置信度的依据。 | confidence_level: "low" → "medium" |
| `collect-zhihu-normal` | 正常响应通过真实 collector 解析。 | list_complete: true → false |
| `collect-zhihu-empty` | 已知空榜与服务失败分开。 | list_complete: true → false |
| `workflow-unrelated-official` | 养老金事件与中国高校赴安哥拉讲座无关，完整编排后不应升级成有官媒支持的 D 类。 | target_category: "E_single_platform_only" → "D_single_platform_with_official" |
| `workflow-old-news` | v0.1 当时尚未实现的时效要求：明确发表于 2025 年的旧官媒报道不能作为 2026 年当日新热点入池；后续修复见下方。 | event_count: 0 → 1 |
| `workflow-search-same-question` | 同一问题的知乎热榜与搜索答案进入完整链路，应归并到一个事件。 | event_count: 1 → 2 |

## 本轮问题定位

- **官媒误匹配优先处理（本轮发现，后续已改善）。** 养老金事件与安哥拉讲座的关键词和 n-gram 重叠均为 0，实际却得到 action_overlap=1 和 supported。[持久化接口](../backend/app/services/analysis_interfaces.py)把实体和行动放在 `event_detail_json.match_features`，[官媒匹配器](../backend/app/services/official_support.py)当时读取顶层；行动词为空时又从候选报道自身提取并与自身比较，造成假阳性。九步链路复现了 E → D 的错误升级。后续修复及对照结果见[评估报告 v0.2](evaluation-v0.2.md#同集对照与真实模型执行)和[字段／判定规则改动说明](../docs/matching-upgrade.md#官媒误匹配的字段与判定规则改动)。
- **同问题搜索增强未归并（原始故障已修正，2026-10-03 补强业务约束）。** v0.1 评估时，搜索相关性层已经识别 `same_zhihu_question_id`，事件合并层却未复用父问题身份；问题 URL 与答案 URL 不相等，`platform_id` 又包含不同 `source_id`。两条实际摘录在模块中均判 `create`，完整链路产生了 2 个事件。后续已通过[知乎问题 ID 的统一身份匹配](../backend/app/services/match_documents.py)修正这一窄问题；本次在此基础上补齐以下两处约束。

  **改动范围：** 属于现有链路的局部业务规则调整，涉及 5 个业务代码文件、4 个测试文件及工作流文档，复用已有采集器、相关性过滤、事件匹配和人工复核机制。改动直接影响热度计算输入与事件是否自动创建，不只是 trace 展示字段调整。

  **微博 CLI 先过滤、再汇总：** [微博热度采集归一化](../backend/app/services/weibo_heat_client.py)逐条记录帖子与 RSSHub 父话题的 `SearchEnrichmentRelation`，剔除低相关结果、可判定为超过 72 小时的历史帖和明确事实冲突。只有通过的样本参与数量、最高互动量和最新可解析时间的汇总；所有样本及判定仍可审计。查询级 `total_number` 包含未返回、未核验的命中，因此只保留在审计字段，不再参与热度评分。同时修正[相关性过滤器](../backend/app/services/search_enrichment_filter.py)，不再把搜索 query 本身当作结果正文来证明相关。效果是：无关的高互动帖子不会抬高父话题指标，搜索命中总数也不会被当成已确认的相关帖子数。

  **父话题约束进入自动事件归并：** [热点分析 Plan](../backend/app/agents/analysis.py)启用 `discovery_mode=automatic`，[归并接口](../backend/app/services/analysis_interfaces.py)先处理热榜种子，再处理搜索增强。知乎回答须找到本轮已成功归并的同平台热榜父话题，支持以问题 ID 识别 URL 变体，并通过既有事件匹配及冲突检查才能进入父事件。父话题缺失、归属不确定或冲突时，条目进入复核，不再因匹配失败而自动另建热点。微博 CLI 样本继续嵌在 RSSHub 话题中，不单独建立事件信号。用户指定事件的 `targeted` 模式保留原有处理方式；分类仍按具体事件接纳的平台证据计算，同平台“热榜＋搜索”不会被算作跨平台。

  **效果与验证（2026-10-03）：** 后端 243 个测试通过，Ruff 检查通过；新增回归覆盖无关高互动帖、旧帖、动作冲突、query 泄漏、缺少父种子时不建热点，以及有效回答归入父事件而冲突回答待复核。[微博用例](../backend/tests/test_weibo_heat_client.py)、[过滤用例](../backend/tests/test_day23_signals.py)和[完整链路用例](../backend/tests/test_hotspot_analysis.py)保留这些检查。扩充集中的 `match-answer-0`、`match-answer-1`、`workflow-search-same-question` 均通过，同题完整链路符合只生成 1 个事件的预期。规则配置共 190/208 条通过，18 条失败的用例名单与[已有规则基线](evaluation-v0.2-rules.json)一致，无新增失败；这次边界补强不能被计作上述同题案例的首次修复，也不代表整套评估全部通过。上方 v0.1 的 134 条历史结果保持不变。

  **剩余问题与边界：** 相关性仍依赖标题、hashtag、关键词及有限的事实冲突规则，不能保证识别所有同名事件或蹭话题内容。CLI 时间缺失或无法解析时只记录质量标记，不会仅因此拒绝样本，72 小时规则无法覆盖这些情况，仍需真实样本验证误接纳与漏接纳。

  自动归并当前要求本轮已有同平台父种子；仅有历史父话题、跨平台父候选或父种子尚待复核时，增强结果会转复核，可能增加人工处理量。本次没有增加自动生成知乎搜索候选或跨平台搜索编排，知乎搜索仍需调用方通过现有候选/query 参数触发。

  历史已拆分的事件、旧 CLI 指标及采集缓存未批量回填；重放旧采集响应不会重新执行 CLI 逐条过滤，需要另行重采集或处理历史数据。本次验证为离线测试与评估，没有新增真实 API/CLI 采样；真实噪声比例、持续运行效果以及其余 18 个既有评估失败仍未在本次解决。执行细节见[当前热点分析工作流](../docs/hotspot-analysis-workflow.md)。

- **榜单完整性标记丢失（原始传参错误已修正，2026-10-03 补齐有效榜单与入榜／离榜规则）。** v0.1 时，[知乎采集器](../backend/app/collectors/zhihu.py)把根据 total 推导的 `list_complete` 写入 `normalize_config`，返回结果却使用 `effective_config`，导致已知短榜、空榜仍被标记为不完整；这一传参错误在此前修复中已更正。本次统一两平台的可观测榜单规则，使完整性标记真正支持入榜／离榜判断。

  **改了什么：** 知乎返回合法条目列表时，已知 total 按实际应返回数量确认完整性；没有 total 时，按当前能获取到的有效列表处理，短榜、空榜均可作为本轮有效观测。若 total 明确表明应有更多条目而实际未返回，仍标记不完整。微博成功解析的 RSS channel 同样视为当前可观测榜单，包含有效短榜、空榜和跳过置顶后的采样范围；CLI 失败不否定 RSS 榜单。客户端新增响应结构检查，错误 JSON、错误页和无效 XML 不会伪装成空榜；缺少标题或 URL 的残缺条目也不能证明榜单完整。[公共采集结果](../backend/app/collectors/base.py)尊重显式 `list_complete=false`，不再因数量够多而覆盖它。

  **业务效果：** 对已关联的事件，本轮在榜记为 `current_topn_present=true`；本轮成功取得完整且范围可比较的榜单、事件缺席，记为 `false`，表示在当前可观测范围内离榜。采集失败、已知残缺、采样范围改变而不能比较时仍为 `null`（未知）。[趋势分析](../backend/app/services/platform_trend.py)还补齐默认调用：即使未配置采样间隔，同一轮的明确在榜／离榜仍能返回；连续上榜时长和升降温趋势继续等待有效采样配置，不据此臆测。

  **与 24 小时汇总的关系：** 当前离榜不等于抹除此前的关注记录。窗口固定为 UTC `(window_end - 24h, window_end]`，`last_topn_seen_at`、`snapshot_presence_count` 及 A–F 分类保留窗口内已接纳证据；缺席或采集故障不续期，也不把当前热度延用为在榜热度。最后一条有效发现证据离开窗口后，事件不再进入本轮 `event_ids`、分类和分析结果，数据库历史保留；重新上榜时复用可确认的原事件身份。

  **剩余边界：** “完整”指当前接口、limit 和 skip_top 范围内可观测的榜单，不证明平台所有讨论都已覆盖。上游若返回结构合法但实际被截断、又没有总数等提示，现有能力无法识别；按约定采用可获取榜单作为业务依据。离榜在下一轮有效采集后确认，故障不会被当作空榜。本次没有增加调度器或展示页面。

- **未知搜索质量被当作置信度支持（此前已修正，2026-10-04 补齐修复状态）。** 原先[分类规则](../backend/app/services/event_classification.py)使用 `search_hit_quality != 'none'`，默认 `unknown` 也满足条件，导致 CLI 失败、没有有效搜索支持的单平台输入被错误提升为 medium。当前规则只让明确的 `same_id_or_title`、`entity_action_match`、`keyword_overlap` 提供搜索质量支持，`unknown` 和 `none` 均不因此提高置信度。

  **与采集 fallback 的关系：** [Week 04](../docs/project-log/week-04.md)中已有“CLI 失败保留 RSS 热榜种子”的降级机制，失败会标记 `cli_unavailable`，互动指标保持缺失。该故障的修复重点是分类层正确使用未知状态，而非另建 fallback 或把成功的 RSS 也标成失败。`classification-rss-fallback` 已复核通过：只有一次单平台 RSS 上榜、CLI 失败且无其他支持时，结果为 **E 类、low**；多轮有效上榜等独立证据仍可按规则提高置信度。代码修复属于此前匹配升级，本次同步文档状态；结果见[本次规则评估](evaluation-hotlist-freshness-rules.json)。

- **时效策略待确定（2026-10-03 已明确并实现）。** 原先仅凭 `fetched_at`，2025 年发布的报道在 2026 年重新抓取后仍能生成当日 F 类事件。本次按来源区分“当前关注”与“历史内容”，与上述入榜／离榜规则共同完成本轮时效处理。

  **解决方案：** 知乎、微博热榜按本次有效观测时间计入 24 小时关注窗口，旧问题或旧话题重新上榜仍是当前关注。官媒按 `published_at` 判断：只有发布时间位于同一滚动 24 小时窗口内的报道，才能进入当前热点提取和当前官媒来源判定。过期、缺失／无法解析发布时间、未来发布时间分别记录 `official_publication_expired`、`official_publication_unknown`、`official_publication_in_future`，原始 Item 和引用保留，信号标记为审计用途，不自动建立当天热点；重新抓取不延长报道时效。实现见[统一时效判定](../backend/app/services/hotspot_freshness.py)及[持久化、入池接口](../backend/app/services/analysis_interfaces.py)。

  **防止后续分类绕过：** [分类组装](../backend/app/services/classification_assembly.py)再次校验已存官媒信号的发布时间；[官媒支撑匹配](../backend/app/services/official_support.py)和[定向搜索及缓存](../backend/app/services/official_search.py)使用相同分析窗口，只用合格报道证明当前官媒关注，不以旧 citation 恢复过期来源。排除明细写入 `official_support_detail.freshness_exclusions`，窗口依据写入 `attention_window`；入池步骤返回 `excluded_count` 并在条目中记录 `normalized.hotspot_freshness`。官媒议程的新鲜度也固定使用分析窗口，回放结果不受执行当天日期影响。官媒证据过期后，如果社区证据仍在窗口内，按剩余来源重新分类；如果没有任何有效热榜或官媒发现证据，事件退出本轮结果。

  **共同验证（2026-10-03）：** 后端 `python -m pytest -q` 为 **271 项通过**，包含新增的两平台有效短榜／空榜、错误响应、缺席与重入、24 小时边界、旧知乎问题当日上榜、官媒旧／无日期／未来报道、官媒到期后 D → E、搜索缓存与历史引用不能续期等回归。[采集用例](../backend/tests/test_day30_31_zhihu_collectors.py)、[微博采集用例](../backend/tests/test_day32_weibo_collector.py)、[完整链路用例](../backend/tests/test_hotspot_analysis.py)、[官媒查询用例](../backend/tests/test_official_search_upgrade.py)记录了这些检查。`ruff check app tests` 通过；扩大到 `ruff check .` 时仍有未改动的 `scripts/source_probe.py` 中既有 `UP017` 提示。pytest 的两条依赖弃用提示不影响通过结果。

  扩充评估集规则配置由此前 **190/208** 提升为 **191/208**，无新增失败、无执行错误；唯一新增通过的是 `workflow-old-news`，旧官媒案例现在生成 **0 个事件**。`collect-zhihu-normal`、`collect-zhihu-empty`、`workflow-fresh-official`、`workflow-source-outage` 和此前的 `workflow-search-same-question` 继续通过。剩余 **17 项**均为已有事件匹配／官媒匹配预期不符，不属于本轮完整性与时效修复范围。见[本次逐例结果](evaluation-hotlist-freshness-rules.json)。上方 v0.1 原始评估数据和前一轮搜索增强修复的验证数字均作为历史记录保留。

  **剩余问题：** 发布时间缺失时会漏掉可能新鲜的官媒报道，需要后续补全可靠发布时间；当前不使用抓取时间或推测的更新时间代替。历史文章即使有新进展，若没有可核验的新报道时间，也只保留为背景。此次验证为离线回归和规则评估，未新增真实 API／RSS／CLI 采样或重跑神经模型配置；未批量重写历史事件及旧快照。下游应读取本轮结果及其窗口，不能把数据库中历史 `active` 事件或旧分类缓存直接当作当前榜单。

## 采集稳定性：已有真实记录

[上次真实采集报告](live-analysis-validation-2026-09-28.md)记录了 1 轮主链路采集：知乎热榜、微博 RSS、人民网 RSS、中新网 RSS、新华网 RSS 五个源返回成功，共 24 条。额外的知乎搜索返回 3 条；微博 CLI 在服务激活后复测成功，保留 3 条结果，未重新跑完整链路。

这只能证明当时可访问，不能估计长期可用率、限流概率或持续运行稳定性。人民日报旧日期、新华网缺少发布时间以及初次 CLI 失败已在真实报告中保留，HTTP 成功和字段存在不等于内容新鲜、相关或可用于热点分析。

本轮采集用例检查解析、完整榜单判断、缺失时间和故障降级；没有模拟出一个长期可用率数字。额度守卫、CLI 进程重放和真实相关性专项仍依赖既有测试及后续多轮实采。知乎客户端的 HTTP 协议层也未在本评估中重放。

## 原始整轮补充回放

额外读取本地完整 collection.json，SHA-256：`2772c7c64a6d368444f42daa3bde323fb30bac5ccb5146b5c36140ed3adfc580`。该文件未提交；公共用例集可独立运行。整轮回放仅列结构性结果，不纳入语义准确率分母。

原始条目 24；回放事件 20；待复核 4；任务 9；分类计数 `{"D_single_platform_with_official": 5, "F_official_only": 15}`。

## 解释与后续处理

1. 优先复核官媒无关证据用例及九步链路的分类结果，排查匹配特征在持久化与读取时的字段路径。
2. 榜单完整性与时效策略已按上文规则完成本轮修复，后续补充多轮真实采集，重点验证上游短榜、故障恢复和官媒发布时间缺失；历史数据如需展示，应按相同窗口规则重算。
3. 对未自动合并的正例区分安全转人工与实际漏合并，再决定阈值；不能仅为提高召回率放松护栏。
4. 人工复核新增标签，补充自然跨平台正例、真实 A/B/D 类及多轮连续采集；当前真实正例较少，衍生同 URL 用例会明显抬高混合指标。
5. 日报质量与去重、LLM 动态规划、embedding、压力测试和线上稳定性不在本轮已验证范围。

## 复现与版本

- Git 基线：`b18fd959ffa618b40a6c5122d70d4967e1242aba`。
- 本轮 app 源码 SHA-256：`10ad48d52210f2d3e3c3277d64de7e36de13a037d62a762fbe6a84c5fd3c5fe6`。
- 用例集 SHA-256：`9c24401589c0c88d19cc55574f21a8a52c4bfc73474b3599b1030c53b4062b1e`。
- [完整逐例结果](evaluation-v0.1.json)、[用例与标注说明](../docs/evaluation-cases-v0.2.md)。

在仓库根目录执行（不需要 API key、Docker 或数据库服务）：

```powershell
Set-Location backend
& ../.venv/Scripts/python.exe -m app.evaluation --output-dir ../reports
```

退出码：0=所选用例全部通过；1=至少一个业务预期不符；2=执行错误或无效用例。报告中的执行耗时用于排查，不是并发性能或生产 SLA 基准。
