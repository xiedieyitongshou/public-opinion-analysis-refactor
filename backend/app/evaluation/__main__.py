"""CLI: python -m app.evaluation --output-dir ../reports [--raw-replay ...]."""

import argparse
import hashlib
import json
from datetime import datetime
from pathlib import Path

from app.evaluation.contracts import CASE_FILE, RunEvaluationInput
from app.evaluation.evaluators import evaluate
from app.evaluation.reporting import render_report
from app.evaluation.runner import load_suite, run_suite
from app.schemas.collectors import FetchSourceItemsOutput


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cases", type=Path, default=CASE_FILE)
    parser.add_argument("--case-ids", nargs="+")
    parser.add_argument("--split", choices=["dev", "holdout", "regression"])
    parser.add_argument("--profile", choices=["rules", "hybrid", "hybrid_rerank"], default="rules")
    parser.add_argument("--report-name", default="evaluation-v0.2")
    parser.add_argument("--output-dir", type=Path, default=Path("../reports"))
    parser.add_argument("--raw-replay", type=Path, help="Optional local original collection JSON")
    args = parser.parse_args()
    try:
        report = run_suite(
            RunEvaluationInput(case_ids=args.case_ids, split=args.split, profile=args.profile),
            path=args.cases,
        )
        if args.raw_replay:
            collection = FetchSourceItemsOutput.model_validate_json(
                args.raw_replay.read_text(encoding="utf-8")
            )
            if collection.validate_only or collection.dry_run or not collection.run_id:
                raise ValueError("raw replay must be an analysis-mode collection")
            suite = load_suite(args.cases)
            # Keep acquisition time and use the same production nine-step workflow.
            end = max(
                datetime.fromisoformat(str(value.observed_at))
                for value in collection.results
                if value.observed_at is not None
            ).isoformat()
            raw_suite = suite.model_copy(update={"window_end": end})
            data = {"collection": collection.model_dump(mode="json")}
            actual = evaluate("workflow", data, raw_suite, profile=args.profile)
            # No original text bodies or full original source responses in the public report.
            actual.pop("categories_by_title", None)
            actual.pop("zhihu_analysis_by_title", None)
            report["raw_replay"] = {
                "input_sha256": hashlib.sha256(args.raw_replay.read_bytes()).hexdigest(),
                "item_count": len(collection.items),
                "actual": actual,
            }
        args.output_dir.mkdir(parents=True, exist_ok=True)
        report["report_name"] = args.report_name
        (args.output_dir / f"{args.report_name}.json").write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
        )
        (args.output_dir / f"{args.report_name}.md").write_text(
            render_report(report), encoding="utf-8"
        )
        print(
            json.dumps(
                {
                    "status": report["status"],
                    "counts": report["counts"],
                    "output_dir": str(args.output_dir),
                },
                ensure_ascii=False,
            )
        )
        return 2 if report["counts"].get("error") else 1 if report["counts"].get("failed") else 0
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Evaluation input/output error: {exc}\n")


if __name__ == "__main__":
    raise SystemExit(main())
