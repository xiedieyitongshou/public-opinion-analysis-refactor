"""Request-level accounting. Tool replays and cache reads never enter this module."""

from __future__ import annotations

from collections import Counter, defaultdict
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from time import monotonic
from urllib.parse import urljoin

import httpx
from sqlalchemy import func, select

from app.models import AgentToolCall
from app.models.briefing import QuotaSnapshot, RequestLog


class RequestBudgetExceeded(ValueError):
    pass


@dataclass
class RequestScope:
    run_id: str
    limits: dict[str, int]
    timeout_seconds: float
    records: list[dict] = field(default_factory=list)
    counts: Counter = field(default_factory=Counter)
    started: float = field(default_factory=monotonic)
    sink: object = None

    def reserve(self, source):
        if monotonic() - self.started >= self.timeout_seconds:
            raise RequestBudgetExceeded("job_request_deadline_exceeded")
        if self.counts[source] >= self.limits.get(source, 3):
            raise RequestBudgetExceeded(f"source_request_limit:{source}")
        self.counts[source] += 1

    def record(self, value):
        value["run_id"] = self.run_id
        if self.sink is not None:
            self.sink(value)
        else:
            self.records.append(value)

    def flush(self, db):
        for value in self.records:
            db.add(RequestLog(**value))
        db.commit()
        self.records.clear()


_scope: ContextVar[RequestScope | None] = ContextVar("external_request_scope", default=None)


@contextmanager
def request_scope(run_id, limits, timeout_seconds, *, sink=None):
    value = RequestScope(run_id, limits, timeout_seconds, sink=sink)
    token = _scope.set(value)
    try:
        yield value
    finally:
        _scope.reset(token)


def measured_call(source, operation, unit, call):
    scope = _scope.get()
    if scope is None:
        return call()
    scope.reserve(source)
    started, now = monotonic(), datetime.now(UTC)
    record = {
        "source_id": source,
        "operation": operation,
        "unit": unit,
        "started_at": now,
        "status": "succeeded",
        "response_code": None,
        "error": None,
    }
    try:
        result = call()
        code = getattr(result, "status_code", getattr(result, "returncode", None))
        record["response_code"] = code
        if code is not None and (
            (unit == "http_request" and code >= 400) or (unit == "cli_invocation" and code != 0)
        ):
            record.update(status="failed", error=f"response_{code}")
        return result
    except Exception as exc:
        record.update(status="failed", error=type(exc).__name__)
        if isinstance(exc, FileNotFoundError):
            record["unit"] = "cli_unavailable"
        raise
    finally:
        record["duration_ms"] = max(0, round((monotonic() - started) * 1000))
        scope.record(record)


def http_request(client, source, method, url, **kwargs):
    """Count every HTTP exchange, including each redirect and each caller retry."""
    redirects = kwargs.pop("follow_redirects", False)
    for _ in range(4):
        response = measured_call(
            source,
            method.upper(),
            "http_request",
            lambda method=method, url=url: getattr(client, method)(
                url, follow_redirects=False, **kwargs
            ),
        )
        if not redirects or not response.is_redirect:
            return response
        url = urljoin(str(response.url), response.headers["location"])
        kwargs.pop("params", None)
        if response.status_code == 303 or (response.status_code in {301, 302} and method == "post"):
            method = "get"
            kwargs.pop("json", None)
            kwargs.pop("data", None)
    raise httpx.TooManyRedirects("Maximum monitored redirects exceeded")


def usage_summary(db, *, hours=24):
    start = datetime.now(UTC) - timedelta(hours=hours)
    rows = db.scalars(select(RequestLog).where(RequestLog.started_at > start)).all()
    grouped = defaultdict(list)
    for row in rows:
        grouped[(row.source_id, row.unit)].append(row)
    groups = []
    for (source, unit), values in sorted(grouped.items()):
        successes = sum(row.status == "succeeded" for row in values)
        groups.append(
            {
                "source_id": source,
                "unit": unit,
                "count": len(values),
                "succeeded": successes,
                "success_rate": successes / len(values),
                "mean_duration_ms": round(sum(row.duration_ms for row in values) / len(values)),
                "errors": dict(Counter(row.error for row in values if row.error)),
                "cost": sum(row.cost for row in values)
                if all(row.cost is not None for row in values)
                else None,
            }
        )
    quotas = {}
    for row in db.scalars(select(QuotaSnapshot).order_by(QuotaSnapshot.observed_at)):
        quotas[row.source_id] = {
            "status": row.status,
            "observed_at": row.observed_at,
            "provenance": row.provenance,
            "values": row.values_json,
        }
    return {
        "window_start": start,
        "window_end": datetime.now(UTC),
        "requests": groups,
        "tool_call_count": db.scalar(
            select(func.count(AgentToolCall.id)).where(AgentToolCall.started_at > start)
        ),
        "quota": quotas,
        "notes": [
            "HTTP 按实际请求计数（含重定向和重试）；CLI 单独计调用次数，其内部 HTTP 数未知。",
            "传输成功不保证内容可用；内容质量请查看来源健康。未获取的余额与费用为未知。",
        ],
    }
