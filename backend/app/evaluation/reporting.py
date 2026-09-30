"""Deterministic report rendering from saved evaluation results."""

import json


def percent(value):
    return "N/A（分母为 0）" if value is None else f"{value:.1%}"


def render_report(report: dict) -> str:
    metrics = report["metrics"]
    counts = report["counts"]
    lines = [
        "# 三模块系统评估报告 v0.2（匹配改进后）",
        "",
        f"评估时间：{report['started_at']}；固定业务窗口结束：{report['window_end']}。",
        "",
        f"本轮执行 **{report['selected_case_count']} 条用例**："
        f"{counts.get('passed', 0)} 条通过、{counts.get('failed', 0)} 条预期不符、"
        f"{counts.get('error', 0)} 条执行错误。不同类型用例不合成为系统总准确率。",
        "",
        "这是当前实现的离线基线。所有新增语义标签由助手整理，**待人工复核**，"
        "不是已经完成双人标注的 gold set。改动前结果保存在 evaluation-v0.1.md/json。",
        "",
        "## 评估范围与证据",
        "",
        "- 基础来源为 2026-09-28 采集摘录及 Day27 历史标题对；扩充集另含 2026-09-29 "
        "官媒 query 候选和合成改写/困难负例，详见用例清单。",
        "- 摘录回放会省略正文；真实摘录、历史标题对、衍生样例和合成场景分别统计。"
        "同一事件的变体不跨分区。扩充集也用于诊断与修复；holdout 保留分组隔离，"
        "但已查看错误，不再是未触碰测试集，不声称盲评。",
        "- 模块评估直接调用现有归一化、规则抽取、事件合并、官媒匹配、分类、热度和趋势实现。"
        "完整链路使用现有九步 Agent Plan/Runner/Tools，仅替换采集返回值。",
        f"- 匹配配置：`{report.get('matching_profile', 'rules')}`；"
        "采集协议测试使用实际 Collector 和模拟传输层；禁用网络和 LLM，"
        "使用独立 SQLite 内存库。"
        "本轮没有请求知乎 API 或微博 CLI，没有消耗其额度。",
        "",
        "## 分项结果",
        "",
        "| 测试范围 | 用例数 | 通过 | 预期不符 | 执行错误 |",
        "|---|---:|---:|---:|---:|",
    ]
    labels = {
        "normalization": "归一化、时间与引用接口",
        "matching": "事件自动合并",
        "official_support": "官媒证据相关性",
        "classification": "类别规则",
        "heat": "平台热度边界与排序",
        "trend": "持续时间与趋势",
        "collection": "采集解析与失败语义",
        "workflow": "完整九步链路",
        "retrieval": "多候选召回",
    }
    for kind, row in metrics["by_kind"].items():
        lines.append(
            f"| {labels[kind]} | {row['total']} | {row['passed']} | "
            f"{row['failed']} | {row['errors']} |"
        )
    category = metrics["classification"]
    lines += [
        "",
        f"分类规则的类别准确率为 {percent(category['category_accuracy'])}，"
        f"但 {category['confidence_checks']} 个置信度检查中"
        f"有 {category['confidence_failures']} 个未通过。"
        "上游的官媒相关性错误单独评估，不能用类别规则通过来证明最终分类正确。",
        "",
    ]
    lines += [
        "",
        "## 事件合并",
        "",
        "正例表示同一事件；预测正例要求直接合并且不待复核。转人工属于自动合并的弃权，"
        "在正例上计为 FN，同时单列复核数量。未合并的负例计 TN，不代表创建了正确的新事件。",
        "候选对评估只给定一个候选事件，不能代表大规模候选召回或聚类整体效果。",
        "",
        "| 来源 | 对数 | TP / FP / FN / TN | Precision | Recall | F1 |",
        "|---|---:|---|---:|---:|---:|",
    ]
    for origin, row in {
        "全部（混合样本，仅供排查）": metrics["matching"],
        **metrics["matching_by_origin"],
    }.items():
        lines.append(
            f"| {origin} | {row['evaluated']} | "
            f"{row['tp']} / {row['fp']} / {row['fn']} / {row['tn']} | "
            f"{percent(row['precision'])} | {percent(row['recall'])} | {percent(row['f1'])} |"
        )
    lines += [
        "",
        f"触发复核：{metrics['matching_review_count']} 对；Guardrail 状态："
        f"`{json.dumps(metrics['matching_guardrails'], ensure_ascii=False)}`。",
        "",
        "分区和 matched_by 维度的计数见 JSON；一对样例可能同时命中多种方法，"
        "方法分组可以重叠，不是独立算法消融实验。模型是否实际执行需结合特征值和模型清单检查。",
        "真实正例与预留 holdout 数量仍少；衍生同 URL 用例单列，不能据此声称自然跨平台泛化能力。",
        "",
    ]
    official = metrics["official_support"]
    trend = metrics["trend"]
    lines += [
        "## 官媒、趋势与工具执行",
        "",
        f"官媒支持判定：TP={official['tp']}、FP={official['fp']}、FN={official['fn']}、TN={official['tn']}；"
        f"Precision={percent(official['precision'])}，Recall={percent(official['recall'])}。",
        "",
        f"合成时间线趋势方向准确率：{percent(trend['direction_accuracy'])}；"
        f"预期 unknown 的准确率：{percent(trend['unknown_accuracy'])}。"
        f"持续时间 MAE：{trend['duration_mae_minutes']} 分钟"
        f"（{trend['numeric_duration_pairs']} 对数值结果；"
        f"缺失预测 {trend['duration_missing_predictions']}）。"
        "这些结果验证时间规则，不能当成真实热点预测效果。",
        "",
        f"完整链路 Tool 调用状态：`{json.dumps(metrics['workflow_tool_statuses'])}`；"
        f"业务任务状态：`{json.dumps(metrics['workflow_task_statuses'])}`。"
        "Tool 调用成功只表示返回值符合接口；业务任务可为 partial/failed/skipped，"
        "不能据此认定结果正确。",
        "",
        "## 预期不符及执行错误",
        "",
        "| 用例 | 依据 | 预期与实际差异 |",
        "|---|---|---|",
    ]
    for row in report["results"]:
        if row["status"] == "passed":
            continue
        diff = row["error"] or "; ".join(
            f"{f['field']}: {json.dumps(f['expected'], ensure_ascii=False)} → "
            f"{json.dumps(f['actual'], ensure_ascii=False)}"
            for f in row["failures"]
        )
        lines.append(
            f"| `{row['case_id']}` | {row['rationale'].replace('|', '/')} | "
            f"{diff.replace('|', '/')} |"
        )
    if not any(row["status"] != "passed" for row in report["results"]):
        lines.append("| — | 当前所选用例均通过 | 仍受样本和标注范围限制 |")
    lines += diagnose(report)
    lines += [
        "",
        "## 采集稳定性：已有真实记录",
        "",
        "[上次真实采集报告](live-analysis-validation-2026-09-28.md)记录了 1 轮主链路采集："
        "知乎热榜、微博 RSS、人民网 RSS、中新网 RSS、新华网 RSS 五个源返回成功，共 24 条。"
        "额外的知乎搜索返回 3 条；微博 CLI 在服务激活后复测成功，保留 3 条结果，未重新跑完整链路。",
        "",
        "这只能证明当时可访问，不能估计长期可用率、限流概率或持续运行稳定性。"
        "人民日报旧日期、新华网缺少发布时间以及初次 CLI 失败已在真实报告中保留，"
        "HTTP 成功和字段存在不等于内容新鲜、相关或可用于热点分析。",
        "",
        "本轮采集用例检查解析、完整榜单判断、缺失时间和故障降级；"
        "没有模拟出一个长期可用率数字。额度守卫、CLI 进程重放和真实相关性专项"
        "仍依赖既有测试及后续多轮实采。知乎客户端的 HTTP 协议层也未在本评估中重放。",
        "",
    ]
    if "raw_replay" in report:
        raw = report["raw_replay"]
        lines += [
            "## 原始整轮补充回放",
            "",
            f"额外读取本地完整 collection.json，SHA-256：`{raw['input_sha256']}`。"
            "该文件未提交；公共用例集可独立运行。整轮回放仅列结构性结果，不纳入语义准确率分母。",
            "",
            f"原始条目 {raw['item_count']}；回放事件 {raw['actual']['event_count']}；"
            f"待复核 {raw['actual']['review_count']}；任务 {raw['actual']['task_count']}；"
            f"分类计数 `{json.dumps(raw['actual']['category_counts'])}`。",
            "",
        ]
    lines += [
        "## 解释与后续处理",
        "",
        "1. 原字段路径、缺失值和父问题身份问题已修复；当前失败以逐例差异为准。",
        "2. 旧官媒报道入池用例是拟定的时效验收要求，当前实现没有明确的发布时间门槛；"
        "应先确定‘历史报道’与‘今日热点’的产品边界，再修正实现和标签。",
        "3. 对未自动合并的正例区分安全转人工与实际漏合并，再决定阈值；不能仅为提高召回率放松护栏。",
        "4. 人工复核新增标签，补充自然跨平台正例、真实 A/B/D 类及多轮连续采集；"
        "当前真实正例较少，衍生同 URL 用例会明显抬高混合指标。",
        "5. 日报质量与去重、LLM 动态规划、压力测试和线上长期稳定性不在本轮已验证范围。",
        "",
        "## 复现与版本",
        "",
        f"- Git 基线：`{report['baseline_git_revision']}`。",
        f"- 本轮 app 源码 SHA-256：`{report['app_source_sha256']}`。",
        f"- 用例集 SHA-256：`{report['dataset_sha256']}`。",
        f"- [完整逐例结果]({report.get('report_name', 'evaluation-v0.2')}.json)、"
        "[扩充用例与标注说明](../docs/evaluation-cases-v0.3.md)。",
        "",
        "在仓库根目录执行（不需要 API key、Docker 或数据库服务）：",
        "",
        "```powershell",
        "Set-Location backend",
        "& ../.venv/Scripts/python.exe -m app.evaluation --output-dir ../reports",
        "```",
        "",
        "退出码：0=所选用例全部通过；1=至少一个业务预期不符；2=执行错误或无效用例。"
        "报告中的执行耗时用于排查，不是并发性能或生产 SLA 基准。",
        "",
    ]
    return "\n".join(lines)


def diagnose(report):
    failed = {r["case_id"] for r in report["results"] if r["status"] == "failed"}
    lines = ["", "## 本轮问题定位", ""]
    if "official-unrelated-00-15" in failed:
        lines += [
            "- **官媒误匹配优先处理。** 养老金事件与安哥拉讲座的关键词和 n-gram 重叠均为 0，"
            "实际却得到 action_overlap=1 和 supported。"
            "[持久化接口](../backend/app/services/analysis_interfaces.py)把实体和行动放在 "
            "`event_detail_json.match_features`，"
            "[官媒匹配器](../backend/app/services/official_support.py)读取顶层；"
            "行动词为空时又从候选报道自身提取并与自身比较，造成假阳性。"
            "九步链路复现了 E → D 的错误升级。",
        ]
    if "workflow-search-same-question" in failed:
        lines += [
            "- **同问题搜索增强未归并。** 搜索相关性层已经识别 same_zhihu_question_id，"
            "事件合并层未复用这个父问题身份；问题 URL 与答案 URL 不相等，"
            "当前 platform_id 又包含不同 source_id。两条实际摘录在模块中均判 create，"
            "完整链路也产生了 2 个事件。先补齐身份传递，再讨论降低语义合并阈值。",
        ]
    if "collect-zhihu-normal" in failed or "collect-zhihu-empty" in failed:
        lines += [
            "- **榜单完整性标记丢失。** [知乎采集器](../backend/app/collectors/zhihu.py)"
            "把已知 total 推导出的 list_complete 写入 normalize_config，返回时调用 "
            "`_result(effective_config, ...)`；因此已知短榜和已知空榜仍返回 false。"
            "这会影响后续‘明确离榜’与‘未知’的区分。",
        ]
    if "classification-rss-fallback" in failed:
        lines += [
            "- **未知搜索质量被当作置信度支持。** "
            "[分类规则](../backend/app/services/event_classification.py)使用 "
            "`search_hit_quality != 'none'`，默认 unknown 也满足此条件；"
            "CLI 失败且没有有效搜索证据的输入得到 medium，验收预期为 low。",
        ]
    if "workflow-old-news" in failed:
        lines += [
            "- **时效策略待确定。** 2025 年发布的真实官媒条目仍生成当日 F 类事件。"
            "当前时间窗口主要使用 fetched_at，缺少发布时间入池策略；"
            "该条属于拟定产品要求与当前实现的差异，应在修复前确认允许的历史证据用途。",
        ]
    return lines if len(lines) > 3 else []
