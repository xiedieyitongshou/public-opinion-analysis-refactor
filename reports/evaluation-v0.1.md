# 三模块系统评估报告 v0.1（Day48–49）

评估时间：2026-09-29T07:03:20.522305+00:00；固定业务窗口结束：2026-09-28T12:00:00+00:00。

本轮执行 **134 条用例**：118 条通过、16 条预期不符、0 条执行错误。不同类型用例不合成为系统总准确率。

这是当前实现的离线基线。所有新增语义标签由助手整理，**待人工复核**，不是已经完成双人标注的 gold set。未修改采集、匹配、分类或趋势业务规则。

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
| `workflow-old-news` | 拟定产品验收要求：明确发表于 2025 年的旧官媒报道不能作为 2026 年当日新热点入池；仍待确认业务时效策略。 | event_count: 0 → 1 |
| `workflow-search-same-question` | 同一问题的知乎热榜与搜索答案进入完整链路，应归并到一个事件。 | event_count: 1 → 2 |

## 本轮问题定位

- **官媒误匹配优先处理。** 养老金事件与安哥拉讲座的关键词和 n-gram 重叠均为 0，实际却得到 action_overlap=1 和 supported。[持久化接口](../backend/app/services/analysis_interfaces.py)把实体和行动放在 `event_detail_json.match_features`，[官媒匹配器](../backend/app/services/official_support.py)读取顶层；行动词为空时又从候选报道自身提取并与自身比较，造成假阳性。九步链路复现了 E → D 的错误升级。
- **同问题搜索增强未归并。** 搜索相关性层已经识别 same_zhihu_question_id，事件合并层未复用这个父问题身份；问题 URL 与答案 URL 不相等，当前 platform_id 又包含不同 source_id。两条实际摘录在模块中均判 create，完整链路也产生了 2 个事件。先补齐身份传递，再讨论降低语义合并阈值。
- **榜单完整性标记丢失。** [知乎采集器](../backend/app/collectors/zhihu.py)把已知 total 推导出的 list_complete 写入 normalize_config，返回时调用 `_result(effective_config, ...)`；因此已知短榜和已知空榜仍返回 false。这会影响后续‘明确离榜’与‘未知’的区分。
- **未知搜索质量被当作置信度支持。** [分类规则](../backend/app/services/event_classification.py)使用 `search_hit_quality != 'none'`，默认 unknown 也满足此条件；CLI 失败且没有有效搜索证据的输入得到 medium，验收预期为 low。
- **时效策略待确定。** 2025 年发布的真实官媒条目仍生成当日 F 类事件。当前时间窗口主要使用 fetched_at，缺少发布时间入池策略；该条属于拟定产品要求与当前实现的差异，应在修复前确认允许的历史证据用途。

## 采集稳定性：已有真实记录

[上次真实采集报告](live-analysis-validation-2026-09-28.md)记录了 1 轮主链路采集：知乎热榜、微博 RSS、人民网 RSS、中新网 RSS、新华网 RSS 五个源返回成功，共 24 条。额外的知乎搜索返回 3 条；微博 CLI 在服务激活后复测成功，保留 3 条结果，未重新跑完整链路。

这只能证明当时可访问，不能估计长期可用率、限流概率或持续运行稳定性。人民日报旧日期、新华网缺少发布时间以及初次 CLI 失败已在真实报告中保留，HTTP 成功和字段存在不等于内容新鲜、相关或可用于热点分析。

本轮采集用例检查解析、完整榜单判断、缺失时间和故障降级；没有模拟出一个长期可用率数字。额度守卫、CLI 进程重放和真实相关性专项仍依赖既有测试及后续多轮实采。知乎客户端的 HTTP 协议层也未在本评估中重放。

## 原始整轮补充回放

额外读取本地完整 collection.json，SHA-256：`2772c7c64a6d368444f42daa3bde323fb30bac5ccb5146b5c36140ed3adfc580`。该文件未提交；公共用例集可独立运行。整轮回放仅列结构性结果，不纳入语义准确率分母。

原始条目 24；回放事件 20；待复核 4；任务 9；分类计数 `{"D_single_platform_with_official": 5, "F_official_only": 15}`。

## 解释与后续处理

1. 优先复核官媒无关证据用例及九步链路的分类结果，排查匹配特征在持久化与读取时的字段路径。
2. 旧官媒报道入池用例是拟定的时效验收要求，当前实现没有明确的发布时间门槛；应先确定‘历史报道’与‘今日热点’的产品边界，再修正实现和标签。
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
