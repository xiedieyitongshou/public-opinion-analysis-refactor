# 系统评估用例 v0.3

用例文件：[`system_cases_v0_3.json`](../backend/app/evaluation/cases/system_cases_v0_3.json)。保留 v0.2 的 134 条用例与预期，新增 74 条，共 208 条；原有 [评估 v0.1](../reports/evaluation-v0.1.md) 不覆盖。最终同集对照见 [评估 v0.2](../reports/evaluation-v0.2.md)。

## 新增样例

| 类型 | 数量 | 构造方式与限制 |
|---|---:|---|
| 合成事件匹配 | 16 | 八个虚构事件，每个一条改写正例、一条近似不同事件负例 |
| 合成官媒相关性 | 16 | 使用同一组正负对，检查不同业务策略 |
| 多候选召回 | 8 | 每个场景四个候选、一个相关项，检查 Top-2 召回 |
| 官媒查询真实摘录的事件匹配 | 17 | 2026-09-29 查询标题、URL、时间；15 个负例、2 个同发布方采访报道正例 |
| 同批真实摘录的官媒相关性 | 17 | 检查搜索命中是否足以作为证据 |

合成场景覆盖车型／年份、不同城市、同校不同课程、不同预警、不同产品、桥梁、疫苗及地震地点；不能当作实际发生的新闻。真实查询摘录排除了正文和原始响应。两个自然正例属于同一发布方的文字／视频采访，不是自然跨平台正例。模糊的综合报道未强行标成同一事件。

## 标签、分组与指标

所有语义标签是助手初标，等待人工复核。标注依据独立保存于 `rationale`，不从模型输出倒推标签；评估器不会把 `expected` 传入业务函数。同一事件及其变体不跨 dev/holdout 分组，校验器检查分组泄漏和引用完整性。

本轮扩充集被用于诊断和修复，已查看 holdout 的错误，所以它不能作为未触碰测试集；保留分组只便于追踪。没有通过修改原有标签把失败改成通过。后续应另采一批自然跨平台样本并人工双审，再评估泛化能力。

自动合并正例只有无需复核的 `merge` 才算 TP；正例进入复核计 FN，复核数量另报。官媒 `supported` 和 `weak_supported` 都算预测正例，防止把误匹配降成 weak 后掩盖 FP。执行错误不算普通失败，单独列出。不同模块的通过数不合成为系统总准确率。

新增召回场景规模小，相关候选只有一个，Top-2 很容易命中。它验证召回路径可运行，不能单凭 8/8 宣称 hybrid 优于 BM25。代表描述冲突由回归测试覆盖，尚未提供带完整标注的聚类 B-cubed / pairwise 指标。

## 复现

在 `backend` 目录运行：

```powershell
# 普通回归测试，模型与网络隔离
& ../.venv/Scripts/python.exe -m pytest -q

# 单一配置；rules 不加载模型
& ../.venv/Scripts/python.exe -m app.evaluation --profile rules --report-name evaluation-rules-local

# 按 matching-upgrade.md 准备模型后，运行完整三组对照
& ../.venv/Scripts/python.exe -m app.evaluation.ablation --output-dir ../reports
```

消融入口先实际加载并执行两种模型，模型缺失／失效会中止，不允许静默退化成三遍规则测试。公共冻结用例不需要本地采样文件即可复现；`scripts/build_matching_upgrade_cases.py` 只在重建真实摘录集时依赖被忽略的原始采样。

离线评估关闭套接字联网，调用现有 Collector（模拟传输）、Normalizer、规则抽取、合并 Guardrails、分类、热度、趋势及完整九步链路。每个链路场景使用独立 SQLite 内存库。没有重新请求知乎 API／微博 CLI；官媒实际请求单独见 [实测记录](../reports/official-search-validation-2026-09-29.md)。

输出包括三份 profile JSON、汇总 `evaluation-v0.2.md/json`，记录 app 源码／数据 SHA-256、模型 revision、依赖版本、非空模型特征的用例数量、推理批次数、降级情况、原 134 条的修复与回退清单。不同 profile 在同一进程复用缓存，耗时不能作为速度比较。

退出码：`0` 为全部预期满足；`1` 为至少一条业务预期不符；`2` 为执行、模型或输入错误。业务报告允许保留未解决的失败；pytest 通过不等于评估用例全部通过。
