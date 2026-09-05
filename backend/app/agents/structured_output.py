"""Structured output validation helpers."""

from typing import Any

from pydantic import BaseModel, ValidationError


class StructuredOutputValidationResult(BaseModel):
    is_valid: bool
    output: dict[str, Any] | None = None
    error_message: str | None = None


def validate_structured_output(
    output_model: type[BaseModel],
    raw_output: dict[str, Any] | BaseModel,
) -> StructuredOutputValidationResult:
    try:
        if isinstance(raw_output, output_model):
            parsed_output = raw_output
        else:
            parsed_output = output_model.model_validate(raw_output)
    except ValidationError as exc:
        return StructuredOutputValidationResult(is_valid=False, error_message=str(exc))

    return StructuredOutputValidationResult(
        is_valid=True,
        output=parsed_output.model_dump(mode="json"),
    )


def validate_structured_output_or_raise(
    output_model: type[BaseModel],
    raw_output: dict[str, Any] | BaseModel,
) -> BaseModel:
    result = validate_structured_output(output_model, raw_output)
    if not result.is_valid:
        raise ValueError(result.error_message)
    return output_model.model_validate(result.output)
