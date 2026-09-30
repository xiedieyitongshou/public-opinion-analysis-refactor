"""Versioned, offline evaluation contracts; annotations are independent of predictions."""

from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

CASE_FILE = Path(__file__).parent / "cases" / "system_cases_v0_3.json"
KINDS = (
    "normalization",
    "matching",
    "official_support",
    "classification",
    "heat",
    "trend",
    "collection",
    "workflow",
    "retrieval",
)


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_id: str
    kind: Literal[
        "normalization",
        "matching",
        "official_support",
        "classification",
        "heat",
        "trend",
        "collection",
        "workflow",
        "retrieval",
    ]
    origin: Literal["real_excerpt", "legacy_title_pair", "derived", "synthetic"]
    split: Literal["dev", "holdout", "regression"]
    groups: list[str] = Field(default_factory=list)
    rationale: str = Field(min_length=1)
    label_status: str = "assistant_annotated_pending_human_review"
    input: dict[str, Any]
    expected: dict[str, Any] = Field(min_length=1)


class EvaluationSuite(BaseModel):
    model_config = ConfigDict(extra="forbid")

    suite_name: str
    version: str
    window_end: str
    description: str
    provenance: dict[str, Any]
    corpus: dict[str, dict[str, Any]]
    cases: list[EvaluationCase] = Field(min_length=1)

    @model_validator(mode="after")
    def validate_manifest(self):
        ids = [case.case_id for case in self.cases]
        if len(set(ids)) != len(ids):
            raise ValueError("duplicate case_id")
        assignments: dict[str, str] = {}

        def references(value):
            if isinstance(value, dict):
                if "ref" in value:
                    yield value["ref"]
                for child in value.values():
                    yield from references(child)
            elif isinstance(value, list):
                for child in value:
                    yield from references(child)

        for case in self.cases:
            for ref in references(case.input):
                if ref not in self.corpus:
                    raise ValueError(f"{case.case_id}: unknown corpus ref {ref}")
                group = self.corpus[ref]["group"]
                if group not in case.groups:
                    raise ValueError(f"{case.case_id}: untracked event group {group}")
            for group in case.groups:
                previous = assignments.setdefault(group, case.split)
                if previous != case.split:
                    raise ValueError(f"event group {group} leaks across splits")
        return self


class RunEvaluationInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    case_ids: list[str] | None = None
    split: Literal["dev", "holdout", "regression"] | None = None
    profile: Literal["rules", "hybrid", "hybrid_rerank"] = "rules"


class RunEvaluationOutput(BaseModel):
    status: Literal["succeeded", "partial", "failed"]
    evaluation_run_id: int | None = None
    report: dict[str, Any]
