"""Load labeled cases, compute predictions, and emit auditable machine-readable results."""

import hashlib
import json
import subprocess
import sys
from collections import Counter
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import perf_counter

from app.evaluation.contracts import CASE_FILE, EvaluationSuite, RunEvaluationInput
from app.evaluation.evaluators import evaluate


def load_suite(path: Path = CASE_FILE) -> EvaluationSuite:
    return EvaluationSuite.model_validate_json(path.read_text(encoding="utf-8"))


def compare(expected: dict, actual: dict) -> list[dict]:
    """Exact partial-dict comparison. Missing fields are never mistaken for explicit null."""
    failures = []
    for key, wanted in expected.items():
        if key not in actual or actual[key] != wanted:
            failures.append(
                {
                    "field": key,
                    "expected": wanted,
                    "actual": actual.get(key),
                    "missing": key not in actual,
                }
            )
    return failures


def ratio(numerator: int | float, denominator: int) -> float | None:
    return round(numerator / denominator, 6) if denominator else None


def binary_metrics(rows, label="same_event"):
    valid = [r for r in rows if r["status"] != "error" and label in r["expected"]]
    tp = sum(r["expected"][label] is True and r["actual"][label] is True for r in valid)
    fp = sum(r["expected"][label] is False and r["actual"][label] is True for r in valid)
    fn = sum(r["expected"][label] is True and r["actual"][label] is False for r in valid)
    tn = sum(r["expected"][label] is False and r["actual"][label] is False for r in valid)
    return {
        "evaluated": len(valid),
        "errors_excluded": len(rows) - len(valid),
        "tp": tp,
        "fp": fp,
        "fn": fn,
        "tn": tn,
        "precision": ratio(tp, tp + fp),
        "recall": ratio(tp, tp + fn),
        "f1": ratio(2 * tp, 2 * tp + fp + fn),
        "accuracy": ratio(tp + tn, len(valid)),
    }


def summarize(rows):
    by_kind = {}
    by_origin = {}
    by_split = {}
    for field, target in (("kind", by_kind), ("origin", by_origin), ("split", by_split)):
        for value in sorted({r[field] for r in rows}):
            subset = [r for r in rows if r[field] == value]
            counts = Counter(r["status"] for r in subset)
            target[value] = {
                "total": len(subset),
                "passed": counts["passed"],
                "failed": counts["failed"],
                "errors": counts["error"],
            }
    matches = [r for r in rows if r["kind"] == "matching"]
    methods = {}
    for row in matches:
        features = row["actual"].get("resolution", {}).get("match_features_json", {})
        for method in features.get("matched_by", []) or ["none"]:
            methods.setdefault(method, []).append(row)
    trend = [r for r in rows if r["kind"] == "trend" and r["status"] != "error"]
    durations = [
        abs(r["actual"]["continuous_topn_minutes"] - r["expected"]["continuous_topn_minutes"])
        for r in trend
        if r["expected"].get("continuous_topn_minutes") is not None
        and r["actual"].get("continuous_topn_minutes") is not None
    ]
    unknowns = [r for r in trend if r["expected"].get("trend_status") == "unknown"]
    classifications = [r for r in rows if r["kind"] == "classification" and r["status"] != "error"]
    tool_counts = Counter()
    task_counts = Counter()
    for row in rows:
        if row["kind"] == "workflow":
            tool_counts.update(row["actual"].get("tool_statuses", {}))
            task_counts.update(row["actual"].get("task_statuses", {}))
    return {
        "by_kind": by_kind,
        "by_origin": by_origin,
        "by_split": by_split,
        "matching": binary_metrics(matches),
        "matching_by_origin": {
            key: binary_metrics([r for r in matches if r["origin"] == key])
            for key in sorted({r["origin"] for r in matches})
        },
        "matching_by_split": {
            key: binary_metrics([r for r in matches if r["split"] == key])
            for key in sorted({r["split"] for r in matches})
        },
        "matching_by_method_overlapping": {k: binary_metrics(v) for k, v in methods.items()},
        "matching_review_count": sum(bool(r["actual"].get("review_required")) for r in matches),
        "matching_guardrails": dict(
            Counter(r["actual"].get("guardrail_status", "error") for r in matches)
        ),
        "official_support": binary_metrics(
            [r for r in rows if r["kind"] == "official_support"], label="supported"
        ),
        "classification": {
            "evaluated": len(classifications),
            "category_accuracy": ratio(
                sum(
                    r["actual"].get("priority_category") == r["expected"].get("priority_category")
                    for r in classifications
                ),
                len(classifications),
            ),
            "confidence_checks": sum("confidence_level" in r["expected"] for r in classifications),
            "confidence_failures": sum(
                "confidence_level" in r["expected"]
                and r["actual"].get("confidence_level") != r["expected"]["confidence_level"]
                for r in classifications
            ),
        },
        "trend": {
            "evaluated": len(trend),
            "direction_accuracy": ratio(
                sum(
                    r["actual"].get("trend_status") == r["expected"].get("trend_status")
                    for r in trend
                ),
                len(trend),
            ),
            "unknown_accuracy": ratio(
                sum(r["actual"]["trend_status"] == "unknown" for r in unknowns), len(unknowns)
            ),
            "numeric_duration_pairs": len(durations),
            "duration_mae_minutes": ratio(sum(durations), len(durations)),
            "duration_missing_predictions": sum(
                r["expected"].get("continuous_topn_minutes") is not None
                and r["actual"].get("continuous_topn_minutes") is None
                for r in trend
            ),
        },
        "workflow_tool_statuses": dict(tool_counts),
        "workflow_tool_call_success_rate": ratio(
            tool_counts["succeeded"], sum(tool_counts.values())
        ),
        "workflow_task_statuses": dict(task_counts),
        "retrieval": {
            "relevant_total": sum(
                r["actual"].get("relevant_total", 0) for r in rows if r["kind"] == "retrieval"
            ),
            "relevant_found": sum(
                r["actual"].get("relevant_found", 0) for r in rows if r["kind"] == "retrieval"
            ),
        },
    }


def run_suite(request: RunEvaluationInput | None = None, *, path: Path = CASE_FILE) -> dict:
    request = request or RunEvaluationInput()
    suite = load_suite(path)
    if request.case_ids is not None:
        unknown = set(request.case_ids) - {c.case_id for c in suite.cases}
        if unknown:
            raise ValueError(f"unknown case ids: {sorted(unknown)}")
    cases = [
        c
        for c in suite.cases
        if (request.case_ids is None or c.case_id in request.case_ids)
        and (request.split is None or c.split == request.split)
    ]
    if not cases:
        raise ValueError("selection contains no evaluation cases")
    started = datetime.now(UTC)
    from app.services.semantic_models import get_semantic_models

    models = get_semantic_models()
    calls_before = dict(models.calls)
    rows = []
    for case in cases:
        clock = perf_counter()
        actual = {}
        error = None
        failures = []
        try:
            actual = evaluate(case.kind, case.input, suite, profile=request.profile)
            failures = compare(case.expected, actual)
            status = "failed" if failures else "passed"
        except Exception as exc:  # noqa: BLE001 - preserve case errors and keep evaluating.
            status = "error"
            error = f"{type(exc).__name__}: {exc}"
        rows.append(
            {
                "case_id": case.case_id,
                "kind": case.kind,
                "origin": case.origin,
                "split": case.split,
                "label_status": case.label_status,
                "rationale": case.rationale,
                "expected": case.expected,
                "actual": actual,
                "status": status,
                "failures": failures,
                "error": error,
                "duration_ms": round((perf_counter() - clock) * 1000, 2),
            }
        )
    root = Path(__file__).resolve().parents[3]
    try:
        revision = subprocess.check_output(
            ["git", "rev-parse", "HEAD"], cwd=root, text=True
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        revision = "unknown"
    counts = Counter(row["status"] for row in rows)
    source_hash = hashlib.sha256()
    for source in sorted((root / "backend/app").rglob("*.py")):
        source_hash.update(source.relative_to(root).as_posix().encode())
        source_hash.update(source.read_bytes())
    packages = {}
    for name in (
        "torch",
        "sentence-transformers",
        "transformers",
        "onnxruntime",
        "tokenizers",
        "sentencepiece",
        "numpy",
    ):
        try:
            packages[name] = version(name)
        except PackageNotFoundError:
            packages[name] = None
    from app.core.config import settings
    from app.services.semantic_models import DEFAULT_MODEL_ROOT

    manifest = Path(settings.semantic_model_dir or DEFAULT_MODEL_ROOT) / "manifest.json"
    return {
        "suite_name": suite.suite_name,
        "version": suite.version,
        "baseline_git_revision": revision,
        "app_source_sha256": source_hash.hexdigest(),
        "dataset_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "started_at": started.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "window_end": suite.window_end,
        "network": "disabled",
        "use_llm": False,
        "use_embedding": request.profile != "rules",
        "use_reranker": request.profile == "hybrid_rerank",
        "matching_profile": request.profile,
        "neural_inference": {
            "batch_calls": {key: models.calls[key] - calls_before[key] for key in calls_before},
            "embedding_scored_cases": sum(
                _has_score(r["actual"], "embedding_similarity") for r in rows
            ),
            "rerank_scored_cases": sum(_has_score(r["actual"], "rerank_score") for r in rows),
            "degraded_cases": [r["case_id"] for r in rows if _has_degradation(r["actual"])],
        },
        "model_manifest": json.loads(manifest.read_text(encoding="utf-8"))
        if manifest.exists()
        else None,
        "package_versions": packages,
        "label_status": "assistant annotations pending human review",
        "selected_case_count": len(rows),
        "selection": request.model_dump(mode="json"),
        "counts": dict(counts),
        "status": "failed" if counts["error"] else "partial" if counts["failed"] else "succeeded",
        "metrics": summarize(rows),
        "results": rows,
    }


def _has_score(value, key):
    if isinstance(value, dict):
        return value.get(key) is not None or any(_has_score(v, key) for v in value.values())
    return isinstance(value, list) and any(_has_score(v, key) for v in value)


def _has_degradation(value):
    if isinstance(value, dict):
        return any(_has_degradation(v) for v in value.values())
    if isinstance(value, list):
        return any(_has_degradation(v) for v in value)
    return isinstance(value, str) and value.startswith(
        (
            "embedding_model_not_",
            "reranker_model_not_",
            "embedding_load_failed",
            "reranker_load_failed",
            "embedding_inference_failed",
            "reranker_inference_failed",
        )
    )


def run_evaluation_tool(input_data, context):
    from app.evaluation.contracts import RunEvaluationOutput
    from app.models.agent_runtime import EvaluationRun

    # Isolate the socket guard and fixture state from other requests in the host process.
    completed = subprocess.run(
        [sys.executable, "-B", "-m", "app.evaluation.worker"],
        input=input_data.model_dump_json(),
        capture_output=True,
        text=True,
        encoding="utf-8",
        cwd=Path(__file__).resolve().parents[2],
        timeout=300,
        check=False,
    )
    if completed.returncode != 0:
        raise ValueError(f"evaluation worker failed: {completed.stderr.strip()}")
    report = json.loads(completed.stdout)
    run_id = None
    if context.db_session is not None:
        row = EvaluationRun(
            suite_name=report["suite_name"],
            status=report["status"],
            metrics_json=report["metrics"],
            summary=json.dumps(report["counts"]),
            started_at=datetime.fromisoformat(report["started_at"]),
            finished_at=datetime.fromisoformat(report["finished_at"]),
        )
        context.db_session.add(row)
        context.db_session.commit()
        run_id = row.id
    return RunEvaluationOutput(status=report["status"], evaluation_run_id=run_id, report=report)
