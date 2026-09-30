"""Process boundary for the Agent tool; evaluation mismatches are valid worker output."""

import json
import sys

from app.evaluation.contracts import RunEvaluationInput
from app.evaluation.runner import run_suite


def main() -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    try:
        request = RunEvaluationInput.model_validate_json(sys.stdin.read())
        print(json.dumps(run_suite(request), ensure_ascii=False))
        return 0
    except (ValueError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
