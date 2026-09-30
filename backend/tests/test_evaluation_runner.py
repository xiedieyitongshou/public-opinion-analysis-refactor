"""Check the evaluator's truthfulness and isolation, independently of business pass rates."""

import json
import socket

import pytest
from pydantic import ValidationError
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from app.db.init_db import init_db
from app.evaluation import evaluators
from app.evaluation.__main__ import main
from app.evaluation.contracts import CASE_FILE, EvaluationSuite, RunEvaluationInput
from app.evaluation.reporting import render_report
from app.evaluation.runner import binary_metrics, compare, load_suite, run_suite
from app.models.agent_runtime import EvaluationRun
from app.tools import default_tool_registry


def write_suite(tmp_path, data):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return path


def test_frozen_corpus_has_twenty_seed_groups_without_split_leakage():
    suite = load_suite()
    seeds = [v for v in suite.corpus.values() if v.get("core_seed")]
    assert len(seeds) == len({v["group"] for v in seeds}) == 20
    assert sum(v["split"] == "holdout" for v in seeds) == 6
    baseline = [c for c in suite.cases if not c.case_id.startswith("upgrade-")]
    assert len(baseline) == 134
    assert len([c for c in baseline if c.kind == "matching"]) == 48
    assert len(suite.cases) == 208
    assert len([c for c in suite.cases if c.kind == "retrieval"]) == 8
    assert all(c.label_status == "assistant_annotated_pending_human_review" for c in suite.cases)


@pytest.mark.parametrize(
    "change, message",
    [
        ("duplicate", "duplicate case_id"),
        ("split", "leaks across splits"),
        ("missing_ref", "unknown corpus ref"),
        ("hidden_group", "untracked event group"),
    ],
)
def test_manifest_rejects_invalid_or_leaking_cases(change, message):
    data = json.loads(CASE_FILE.read_text(encoding="utf-8"))
    case = data["cases"][0]
    if change == "duplicate":
        data["cases"].append(case)
    elif change == "split":
        case["split"] = "holdout"
    elif change == "missing_ref":
        case["input"]["item"]["ref"] = "nonexistent"
    else:
        case["groups"] = []
    with pytest.raises(ValidationError, match=message):
        EvaluationSuite.model_validate(data)


def test_expected_null_requires_an_actual_field():
    assert not compare({"published_at": None}, {"published_at": None})
    assert compare({"published_at": None}, {})[0]["missing"] is True


def test_confusion_metrics_count_abstention_as_miss_and_never_invent_denominators():
    rows = [
        {"status": "passed", "expected": {"same_event": actual}, "actual": {"same_event": actual}}
        for actual in (True, False)
    ]
    rows.append(
        {
            "status": "failed",
            "expected": {"same_event": True},
            "actual": {"same_event": False, "review_required": True},
        }
    )
    rows.append({"status": "error", "expected": {"same_event": True}, "actual": {}})
    result = binary_metrics(rows)
    assert (result["tp"], result["fp"], result["fn"], result["tn"]) == (1, 0, 1, 1)
    assert result["precision"] == 1 and result["recall"] == 0.5
    assert result["errors_excluded"] == 1
    assert binary_metrics([])["precision"] is None
    assert binary_metrics([])["accuracy"] is None


def test_wrong_label_is_detected_instead_of_becoming_classification_input(tmp_path):
    data = load_suite().model_dump()
    case = next(c for c in data["cases"] if c["case_id"] == "classification-A")
    case["expected"]["priority_category"] = "F_official_only"
    data["cases"] = [case]
    result = run_suite(path=write_suite(tmp_path, data))
    assert result["counts"] == {"failed": 1}
    assert result["results"][0]["actual"]["priority_category"] == "A_cross_platform_with_official"


@pytest.mark.parametrize(
    "selection",
    [
        RunEvaluationInput(case_ids=[]),
        RunEvaluationInput(case_ids=["absent"]),
        RunEvaluationInput(case_ids=["classification-A"], split="holdout"),
    ],
)
def test_empty_or_unknown_selection_is_not_a_success(selection):
    with pytest.raises(ValueError):
        run_suite(selection)


def test_accidental_network_access_is_an_error_and_does_not_stop_other_cases(monkeypatch):
    def attempts_network(data, suite):
        with socket.socket() as connection:
            connection.connect(("127.0.0.1", 9))

    monkeypatch.setitem(evaluators.EVALUATORS, "normalization", attempts_network)
    result = run_suite(RunEvaluationInput(case_ids=["normalize-00", "classification-A"]))
    assert result["status"] == "failed"
    assert result["counts"] == {"error": 1, "passed": 1}
    assert "network disabled" in result["results"][0]["error"]


def test_registered_evaluation_tool_runs_real_workflow_and_persists_summary():
    engine = create_engine("sqlite:///:memory:")
    init_db(engine)
    try:
        with Session(engine) as db:
            result = default_tool_registry.call(
                "run_evaluation_suite",
                {
                    "case_ids": ["workflow-idempotent"],
                },
                db=db,
            )
            assert result.status == "succeeded", result.error_message
            assert result.output["status"] == "succeeded"
            actual = result.output["report"]["results"][0]["actual"]
            assert actual["fetch_calls"] == 1
            assert actual["tool_call_count"] == 18
            row = db.scalar(select(EvaluationRun))
            assert row.id == result.output["evaluation_run_id"]
            assert row.metrics_json["by_kind"]["workflow"]["passed"] == 1
    finally:
        engine.dispose()


def test_cli_writes_report_with_results_and_returns_failure_for_bad_oracle(tmp_path, monkeypatch):
    data = load_suite().model_dump()
    case = next(c for c in data["cases"] if c["case_id"] == "classification-A")
    case["expected"]["priority_category"] = "unknown"
    data["cases"] = [case]
    path = write_suite(tmp_path, data)
    monkeypatch.setattr(
        "sys.argv", ["evaluation", "--cases", str(path), "--output-dir", str(tmp_path)]
    )
    assert main() == 1
    report = json.loads((tmp_path / "evaluation-v0.2.json").read_text(encoding="utf-8"))
    markdown = (tmp_path / "evaluation-v0.2.md").read_text(encoding="utf-8")
    assert report["counts"] == {"failed": 1}
    assert "classification-A" in markdown
    assert "待人工复核" in render_report(report)
