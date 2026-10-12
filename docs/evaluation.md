# 评估与验证指南

[返回项目首页](../README.md) · [验证报告](../README.md#验证记录) · [模型安装](event-matching.md#模型安装与运行)

回归测试、离线业务评估和真实运行验收分别回答代码约束是否保持、业务结果是否符合标签、外部来源与投递是否可持续工作，不能混成一个准确率。本文说明用例、标签边界和复现方法。

## 用例版本与报告版本

| 用例集 | 范围 | 对应记录 |
| --- | --- | --- |
| [Day27 v0.1](../backend/app/evaluation/cases/day27_evaluation_cases_v0_1.json) | 早期真实采样的分类、匹配及错误案例 | [原始标签说明](../reports/evaluation-labels-v0.1.md)，保留当时口径 |
| [system v0.2](../backend/app/evaluation/cases/system_cases_v0_2.json) | 134 条，覆盖采集、归一化、事件、官媒、分类、热度、趋势及完整编排 | [评估报告 v0.1](../reports/evaluation-v0.1.md)是改进前结果 |
| [system v0.3](../backend/app/evaluation/cases/system_cases_v0_3.json) | 保留原 134 条与标签，新增 74 条，共 208 条 | [评估报告 v0.2](../reports/evaluation-v0.2.md)是匹配改进后三组对照 |

当前执行器默认使用 system v0.3。用例版本与报告版本编号不同，不要按文件后缀误判。

v0.2 的 134 条包括：归一化 20、事件匹配 48、官媒 13、分类 8、热度排序 8、时间趋势 18、采集 9、完整编排 10。v0.3 新增：合成匹配 16、合成官媒 16、多候选召回 8、官媒查询真实摘录匹配 17、同批官媒相关性 17。

## 数据与标签边界

公共集保留真实标题、URL、时间和必要指标，省略正文、用户资料、凭据与原始完整响应。采集 hash 用于追溯原件，不代表摘录与原件内容相同。

数据分为 `real_excerpt`、`legacy_title_pair`、`derived`、`synthetic`；语义标签由助手初标，尚待人工复核。原 20 个主题按事件组分为 dev 14／holdout 6，同一事件与变体不能跨分区；规则回归另列。加载器检查泄漏、重复 ID、缺失引用及事件组。

扩充集已用于诊断与修复，holdout 错误已被查看，不能当作未触碰的盲测集。自然跨平台正例仍不足；同 URL 变体和同发布方的文字／视频采访不能替代自然跨平台样本。新增或更正标签需记录理由，不能按当前预测修改 expected。

## 复现命令

在 `backend` 目录执行。普通回归使用模拟响应；不会为了测试主动请求付费采集接口：

```powershell
..\.venv\Scripts\python.exe -m pytest -q
..\.venv\Scripts\python.exe -m ruff check app tests
```

离线业务评估建议输出到临时目录，避免覆盖仓库的历史结果：

```powershell
..\.venv\Scripts\python.exe -m app.evaluation --profile rules --output-dir "$env:TEMP/poa-evaluation" --report-name evaluation-local
..\.venv\Scripts\python.exe -m app.evaluation --split holdout --output-dir "$env:TEMP/poa-holdout"
..\.venv\Scripts\python.exe -m app.evaluation --case-ids workflow-search-same-question --output-dir "$env:TEMP/poa-case"
```

使用旧集时显式指定 `--cases app/evaluation/cases/system_cases_v0_2.json`。已有完整原始采集响应时，可用 `--raw-replay <collection.json>` 加做结构回放；它保留观测元数据，只报告结构结果，不加入语义准确率分母。公共固定用例不依赖私人原件。

准备好本地模型后运行同集三组对照：

```powershell
..\.venv\Scripts\python.exe -m app.evaluation.ablation --output-dir "$env:TEMP/poa-ablation"
```

对照包含 `rules`、`hybrid`、`hybrid_rerank`。开始时真实加载 embedding 和 reranker 并检查推理，模型缺失或失效中止，不允许静默退化成三遍规则测试。不同 profile 会复用缓存，耗时不能直接当作模型速度对比。

单配置评估退出码：0 为全部预期满足，1 为业务预期不符，2 为执行或输入错误。未知用例、空选择和执行异常不能包装为“100% 通过”。

## 隔离与执行范围

执行器阻断网络，调用现有 Collector 的模拟传输、Normalizer、规则抽取、匹配、Guardrails、分类、热度和趋势。完整编排使用实际九步 Plan，只替换采集响应，各链路场景使用独立 SQLite 内存库。

`run_evaluation_suite` Tool 在独立子进程运行，避免评估的网络限制和样本影响宿主任务；提供数据库 Session 时可保存摘要。它不属于每轮热点分析的九步业务链路。

[`build_evaluation_cases_v02.py`](../backend/scripts/build_evaluation_cases_v02.py)与 [`build_matching_upgrade_cases.py`](../backend/scripts/build_matching_upgrade_cases.py)用于重建冻结数据，正常评估无需执行。重建真实摘录需要本地原件，标签依据与预测分开。

## 指标解释

| 项目 | 口径与限制 |
| --- | --- |
| 自动合并 | 只有无需复核的 `merge` 算预测正例；正例转人工计自动召回 FN，人工负担另报 |
| 官媒相关性 | `supported` 与 `weak_supported` 都计支持，不能把误匹配降为 weak 来掩盖 FP |
| 分类 | 正确类别与置信度分别检查；给定证据的规则通过不代表上游证据正确 |
| 热度与排序 | 只比较同平台／适用类别，不使用跨平台总分 |
| 趋势 | rising／stable／cooling／unknown；unknown 与缺失单列，不能当作正确涨跌 |
| Tool | 协议执行成功与业务成功分开，来源超时可以被合法返回为 failed |
| 候选召回 | 新增场景仅 8 条、每例候选很少，验证路径可运行，不能据此声称普遍优于其他方法 |
| 存储与运行 | 当前可查询请求、任务、日志和投递；业务数据清理、压缩与长期增长指标仍待实现 |

不同模块通过数不合成为系统生产准确率。未自动归并既可能是漏召回，也可能是正确拒绝或保守转人工，需要逐例判断。新增自然跨平台样本、人工复核标签和真实多轮观测仍是后续工作。

## 历史结果与修复追踪

[2026-09-29 基线](../reports/evaluation-v0.1.md)为 118/134， [2026-09-30 对照](../reports/evaluation-v0.2.md)为 193/208；这些是相应日期的结果，未因之后的代码变化回填。后续同问题增强、榜单完整性和时效修复见 [v0.1 对应问题](../reports/evaluation-v0.1.md#本轮问题定位)和[规则复评 JSON](../reports/evaluation-hotlist-freshness-rules.json)。

最近功能回归与 UI／邮件验收见[邮箱管理报告](../reports/email-management-acceptance-v0.1.md)。真实采样、Docker、本地 Mailpit 和尚待完成的服务器／真实邮箱验收统一从[验证记录](../README.md#验证记录)进入；本次文档整理不重新生成这些历史测试成绩。
