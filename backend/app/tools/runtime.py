"""Tool registration and execution runtime."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from time import perf_counter
from typing import Any

from pydantic import BaseModel, ValidationError
from sqlalchemy.orm import Session

from app.models import AgentToolCall


class ToolRegistryError(Exception):
    """Base error for tool registry failures."""


class ToolAlreadyRegisteredError(ToolRegistryError):
    """Raised when a tool name is registered more than once."""


class ToolNotFoundError(ToolRegistryError):
    """Raised when a requested tool does not exist."""


class ToolExecutionError(ToolRegistryError):
    """Raised when a tool fails after all allowed attempts."""


class ToolContext(BaseModel):
    task_id: int | None = None
    trace_id: str | None = None
    actor: str = "agent"


class ToolResult(BaseModel):
    tool_name: str
    status: str
    output: dict[str, Any] | None = None
    error_message: str | None = None
    attempts: int
    tool_call_id: int | None = None


ToolHandler = Callable[[BaseModel, ToolContext], BaseModel | dict[str, Any]]


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_model: type[BaseModel]
    output_model: type[BaseModel]
    handler: ToolHandler
    has_side_effect: bool = False
    retryable: bool = False
    max_retries: int = 0

    @property
    def input_schema(self) -> dict[str, Any]:
        return self.input_model.model_json_schema()

    @property
    def output_schema(self) -> dict[str, Any]:
        return self.output_model.model_json_schema()


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, definition: ToolDefinition) -> None:
        if definition.name in self._tools:
            raise ToolAlreadyRegisteredError(f"Tool already registered: {definition.name}")
        self._tools[definition.name] = definition

    def get(self, name: str) -> ToolDefinition:
        try:
            return self._tools[name]
        except KeyError as exc:
            raise ToolNotFoundError(f"Tool not found: {name}") from exc

    def list_tools(self) -> list[ToolDefinition]:
        return list(self._tools.values())

    def call(
        self,
        name: str,
        raw_input: dict[str, Any] | BaseModel | None = None,
        *,
        context: ToolContext | None = None,
        db: Session | None = None,
    ) -> ToolResult:
        definition = self.get(name)
        context = context or ToolContext()
        raw_input = raw_input or {}
        attempts_allowed = 1 + (definition.max_retries if definition.retryable else 0)
        last_error: str | None = None

        for attempt in range(1, attempts_allowed + 1):
            tool_call = self._create_log(db, definition, context, raw_input, attempt)
            started = perf_counter()

            try:
                parsed_input = self._validate_input(definition, raw_input)
                raw_output = definition.handler(parsed_input, context)
                parsed_output = self._validate_output(definition, raw_output)
                output_json = parsed_output.model_dump(mode="json")
            except Exception as exc:  # noqa: BLE001 - failures must be logged uniformly.
                last_error = str(exc)
                self._finish_log(db, tool_call, "failed", None, last_error, started)
                if attempt == attempts_allowed:
                    return ToolResult(
                        tool_name=name,
                        status="failed",
                        error_message=last_error,
                        attempts=attempt,
                        tool_call_id=tool_call.id if tool_call else None,
                    )
                continue

            self._finish_log(db, tool_call, "succeeded", output_json, None, started)
            return ToolResult(
                tool_name=name,
                status="succeeded",
                output=output_json,
                attempts=attempt,
                tool_call_id=tool_call.id if tool_call else None,
            )

        raise ToolExecutionError(f"Tool failed without producing a result: {name}")

    def _validate_input(
        self,
        definition: ToolDefinition,
        raw_input: dict[str, Any] | BaseModel,
    ) -> BaseModel:
        if isinstance(raw_input, definition.input_model):
            return raw_input
        try:
            return definition.input_model.model_validate(raw_input)
        except ValidationError as exc:
            raise ToolExecutionError(f"Invalid input for {definition.name}: {exc}") from exc

    def _validate_output(
        self,
        definition: ToolDefinition,
        raw_output: BaseModel | dict[str, Any],
    ) -> BaseModel:
        if isinstance(raw_output, definition.output_model):
            return raw_output
        try:
            return definition.output_model.model_validate(raw_output)
        except ValidationError as exc:
            raise ToolExecutionError(f"Invalid output for {definition.name}: {exc}") from exc

    def _create_log(
        self,
        db: Session | None,
        definition: ToolDefinition,
        context: ToolContext,
        raw_input: dict[str, Any] | BaseModel,
        attempt: int,
    ) -> AgentToolCall | None:
        if db is None:
            return None

        input_json = (
            raw_input.model_dump(mode="json")
            if isinstance(raw_input, BaseModel)
            else raw_input
        )
        tool_call = AgentToolCall(
            task_id=context.task_id,
            tool_name=definition.name,
            status="running",
            input_json=input_json,
            attempt=attempt,
            has_side_effect=definition.has_side_effect,
            started_at=datetime.now(UTC),
        )
        db.add(tool_call)
        db.commit()
        db.refresh(tool_call)
        return tool_call

    def _finish_log(
        self,
        db: Session | None,
        tool_call: AgentToolCall | None,
        status: str,
        output_json: dict[str, Any] | None,
        error_message: str | None,
        started: float,
    ) -> None:
        if db is None or tool_call is None:
            return

        tool_call.status = status
        tool_call.output_json = output_json
        tool_call.error_message = error_message
        tool_call.finished_at = datetime.now(UTC)
        tool_call.duration_ms = int((perf_counter() - started) * 1000)
        db.add(tool_call)
        db.commit()
