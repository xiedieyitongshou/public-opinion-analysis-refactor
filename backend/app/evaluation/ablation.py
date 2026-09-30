"""Run the same frozen suite with rules, dense retrieval and cross-encoder scoring."""

import argparse
import json
import math
from pathlib import Path

from app.evaluation.contracts import CASE_FILE, RunEvaluationInput
from app.evaluation.reporting import percent, render_report
from app.evaluation.runner import run_suite, summarize
from app.services.semantic_models import SemanticUnavailable, get_semantic_models

PROFILES = ("rules", "hybrid", "hybrid_rerank")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=CASE_FILE)
    parser.add_argument("--output-dir", type=Path, default=Path("../reports"))
    parser.add_argument("--baseline", type=Path, default=Path("../reports/evaluation-v0.1.json"))
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    old = json.loads(args.baseline.read_text(encoding="utf-8"))
    previous = {row["case_id"]: row for row in old["results"]}
    reports = {}
    try:
        # A missing/broken model aborts this comparison instead of masquerading as an ablation.
        models = get_semantic_models()
        vector = models.encode(["模型加载与离线推理检查"])[0]
        score = models.rerank([("模型检查", "检查模型是否正常运行")])[0]
        if len(vector) != 512 or not math.isfinite(score):
            raise SemanticUnavailable("semantic preflight returned invalid outputs")
        for profile in PROFILES:
            print(f"Evaluating {profile} ...", flush=True)
            result = run_suite(RunEvaluationInput(profile=profile), path=args.cases)
            result["report_name"] = f"evaluation-v0.2-{profile}"
            subset = [row for row in result["results"] if row["case_id"] in previous]
            if len(subset) != len(previous) or any(
                row["expected"] != previous[row["case_id"]]["expected"] for row in subset
            ):
                raise ValueError("baseline cases/labels changed; invalid before/after comparison")
            result["original_134"] = {
                "passed": sum(row["status"] == "passed" for row in subset),
                "metrics": summarize(subset),
                "fixed": [
                    r["case_id"]
                    for r in subset
                    if r["status"] == "passed" and previous[r["case_id"]]["status"] != "passed"
                ],
                "regressed": [
                    r["case_id"]
                    for r in subset
                    if r["status"] != "passed" and previous[r["case_id"]]["status"] == "passed"
                ],
            }
            evidence = result["neural_inference"]
            if (
                evidence["degraded_cases"]
                or (profile != "rules" and not evidence["embedding_scored_cases"])
                or (profile == "hybrid_rerank" and not evidence["rerank_scored_cases"])
            ):
                raise SemanticUnavailable(f"{profile}: incomplete real model execution")
            path = args.output_dir / f"{result['report_name']}.json"
            path.write_text(
                json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
            )
            reports[profile] = result
            print(
                json.dumps(
                    {"profile": profile, "counts": result["counts"], "inference": evidence},
                    ensure_ascii=False,
                ),
                flush=True,
            )
    except (SemanticUnavailable, ValueError) as exc:
        parser.exit(2, f"Ablation not completed: {exc}\n")
    current = dict(reports["hybrid_rerank"])
    current["report_name"] = "evaluation-v0.2"
    current["ablation"] = {
        profile: {
            "artifact": report["report_name"] + ".json",
            "counts": report["counts"],
            "metrics": report["metrics"],
            "neural_inference": report["neural_inference"],
            "original_134": report["original_134"],
        }
        for profile, report in reports.items()
    }
    (args.output_dir / "evaluation-v0.2.json").write_text(
        json.dumps(current, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    text = render_report(current)
    marker = "## 分项结果"
    text = text.replace(marker, render_ablation(reports, old) + "\n\n" + marker, 1)
    (args.output_dir / "evaluation-v0.2.md").write_text(text, encoding="utf-8")
    errors = any(r["counts"].get("error") for r in reports.values())
    failures = any(r["counts"].get("failed") for r in reports.values())
    return 2 if errors else 1 if failures else 0


def render_ablation(reports, old):
    lines = [
        "## 同集对照与真实模型执行",
        "",
        "下列三组使用相同代码、用例与判定规则，只切换模型配置。"
        "BM25 与 N-gram 始终开启；hybrid 加入独立 dense 召回及 RRF，"
        "hybrid_rerank 再加入交叉编码器。规则版已经包含本轮接口与护栏修复。",
        "",
        "| 配置 | 通过 / 总数 | 合并 TP/FP/FN/TN | 合并 P / R | 官媒 TP/FP/FN/TN | 官媒 P / R |",
        "|---|---:|---|---|---|---|",
    ]
    for profile, report in reports.items():
        m, o = report["metrics"]["matching"], report["metrics"]["official_support"]

        def counts(row):
            return "/".join(str(row[k]) for k in ("tp", "fp", "fn", "tn"))

        lines.append(
            f"| [{profile}]({report['report_name']}.json) | "
            f"{report['counts'].get('passed', 0)} / {report['selected_case_count']} | "
            f"{counts(m)} | {percent(m['precision'])} / {percent(m['recall'])} | "
            f"{counts(o)} | {percent(o['precision'])} / {percent(o['recall'])} |"
        )
    lines += [
        "",
        "| 配置 | embedding 用例 | rerank 用例 | 降级用例 | 召回命中 / 相关候选 |",
        "|---|---:|---:|---:|---:|",
    ]
    for profile, report in reports.items():
        evidence, recall = report["neural_inference"], report["metrics"]["retrieval"]
        lines.append(
            f"| {profile} | {evidence['embedding_scored_cases']} | "
            f"{evidence['rerank_scored_cases']} | {len(evidence['degraded_cases'])} | "
            f"{recall['relevant_found']} / {recall['relevant_total']} |"
        )
    current = reports["hybrid_rerank"]
    original = current["original_134"]
    lines += [
        "",
        "真实推理使用本地 BGE-small-zh-v1.5 与 BGE-reranker-base 的 ONNX int8 转换。"
        "通过预检查后执行，模型失效会中止对照；分数和模型修订见 JSON。"
        "同一进程复用模型及缓存，耗时不用于比较算法性能。"
        "8 个 Top-2 召回场景各只有 4 个候选；此结果不能证明大库召回优势。",
        "",
        f"原有 134 条用例保持标签不变：升级前 {old['counts'].get('passed', 0)} 条通过，"
        f"当前 {original['passed']} 条通过；修复 {len(original['fixed'])} 条，"
        f"新增回退 {len(original['regressed'])} 条。具体 ID 见 JSON 的 `original_134`。",
        "",
        "新增集包含自然同发布方报道正例、合成改写和困难负例。"
        "同一主体、地点或高模型分数均不足以证明同一事件；自动合并保持保守，"
        "仍可能漏合并，也可能存在规则未覆盖的官媒误匹配。详细失败清单保留如下。",
        "",
        "[实现、模型安装及边界说明](../docs/matching-upgrade.md)；"
        "[2026-09-29 官媒定向查询实测](official-search-validation-2026-09-29.md)。",
    ]
    return "\n".join(lines)


if __name__ == "__main__":
    raise SystemExit(main())
