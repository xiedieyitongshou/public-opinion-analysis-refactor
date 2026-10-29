"""Bounded DeepSeek JSON calls. No I/O until an explicitly selected LLM operation."""

import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.llm import PROMPT_VERSION

_mode = ContextVar("event_reasoning_mode", default=None)
_client = ContextVar("event_llm_client", default=None)


class LLMFailure(ValueError):
    """Sanitized, observable failure; never triggers the legacy reasoning backend."""


def reasoning_mode():
    return _mode.get() or settings.event_reasoning_mode


@contextmanager
def reasoning_scope(mode):
    if mode not in {"rules", "llm"}:
        raise ValueError("unknown event reasoning mode")
    mode_token = _mode.set(mode)
    client_token = _client.set(DeepSeekJSONClient() if mode == "llm" else None)
    try:
        yield
    finally:
        _client.reset(client_token)
        _mode.reset(mode_token)


def get_llm_client():
    return _client.get() or DeepSeekJSONClient()


def require_llm_configuration():
    if reasoning_mode() == "llm":
        get_llm_client().validate_configuration()


def validate_quotes(evidence, documents, *, required_ids=()):
    by_id = {row["source_id"]: row for row in documents}
    cited = set()
    for quote in evidence:
        doc = by_id.get(quote.source_id)
        if doc is None or quote.quote not in f"{doc['title']}\n{doc['body']}":
            raise LLMFailure("llm_unverifiable_evidence")
        cited.add(quote.source_id)
    if not set(required_ids).issubset(cited):
        raise LLMFailure("llm_missing_two_sided_evidence")


def document(source_id, title, body="", **metadata):
    limit = settings.llm_document_max_chars
    title, body = str(title or ""), str(body or "")
    return {
        "source_id": str(source_id),
        "title": title[:600],
        "body": body[:limit],
        "truncated": len(title) > 600 or len(body) > limit,
        **metadata,
    }


class DeepSeekJSONClient:
    def __init__(self, *, http_client=None, cache_path=None):
        self.http_client = http_client
        self.calls = 0
        if cache_path is None:
            from sqlalchemy.engine import make_url

            db = make_url(settings.database_url)
            directory = (
                Path(db.database).parent
                if db.drivername == "sqlite" and db.database
                else Path("data")
            )
            cache_path = settings.llm_cache_path or str(directory / "llm-cache.db")
        self.cache_path = Path(cache_path)

    def validate_configuration(self):
        if settings.llm_provider != "deepseek":
            raise LLMFailure("llm_provider_not_supported")
        if not settings.deepseek_api_key or not settings.deepseek_api_key.strip():
            raise LLMFailure("llm_api_key_missing")
        if not settings.deepseek_model.strip():
            raise LLMFailure("llm_model_missing")
        url = urlsplit(settings.deepseek_base_url)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise LLMFailure("llm_base_url_requires_https_without_credentials")

    @contextmanager
    def _cache(self):
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.cache_path, timeout=3)
        try:
            with connection:
                connection.execute(
                    "CREATE TABLE IF NOT EXISTS llm_results "
                    "(key TEXT PRIMARY KEY, value TEXT, saved TEXT)"
                )
                yield connection
        finally:
            connection.close()

    def complete(self, task, prompt, payload, output_type, validator):
        self.validate_configuration()
        schema = output_type.model_json_schema()
        material = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        if len(material) > settings.llm_input_max_chars:
            raise LLMFailure("llm_input_budget_exceeded")
        identity = json.dumps(
            {
                "task": task,
                "model": settings.deepseek_model,
                "base_url": settings.deepseek_base_url,
                "thinking": {"type": "disabled"},
                "prompt_version": PROMPT_VERSION,
                "prompt": prompt,
                "schema": schema,
                "payload": payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            default=str,
        )
        key = hashlib.sha256(identity.encode()).hexdigest()
        audit = {
            "mode": "llm",
            "model": settings.deepseek_model,
            "prompt_version": PROMPT_VERSION,
            "input_hash": key,
            "task": task,
        }
        try:
            with self._cache() as db:
                row = db.execute(
                    "SELECT value,saved FROM llm_results WHERE key=?", (key,)
                ).fetchone()
            if row and datetime.fromisoformat(row[1]) > datetime.now(UTC) - timedelta(
                hours=settings.llm_cache_hours
            ):
                parsed = output_type.model_validate_json(row[0])
                validator(parsed)
                return parsed, {**audit, "cache_hit": True}
        except (sqlite3.Error, OSError, ValidationError):
            logging.getLogger(__name__).warning("llm_cache_read_unavailable")
        if self.calls >= settings.llm_max_calls_per_run:
            raise LLMFailure("llm_call_budget_exceeded")
        self.calls += 1
        from app.services.request_usage import (
            RequestBudgetExceeded,
            measured_call,
            register_default_limit,
        )

        register_default_limit("deepseek", settings.llm_max_calls_per_run)
        request = {
            "model": settings.deepseek_model,
            "stream": False,
            # Structured tasks reserve the bounded output for validated JSON.
            "thinking": {"type": "disabled"},
            "response_format": {"type": "json_object"},
            "max_tokens": settings.llm_max_output_tokens,
            "messages": [
                {
                    "role": "system",
                    "content": prompt
                    + "\nJSON Schema:\n"
                    + json.dumps(schema, ensure_ascii=False),
                },
                {"role": "user", "content": material},
            ],
        }

        def send():
            client = self.http_client or httpx.Client(
                timeout=settings.llm_timeout_seconds,
                follow_redirects=False,
            )
            try:
                with client.stream(
                    "POST",
                    settings.deepseek_base_url.rstrip("/") + "/chat/completions",
                    headers={"Authorization": "Bearer " + settings.deepseek_api_key},
                    json=request,
                    timeout=settings.llm_timeout_seconds,
                ) as response:
                    if response.status_code != 200:
                        raise LLMFailure(f"llm_http_{response.status_code}")
                    content = bytearray()
                    for chunk in response.iter_bytes():
                        content.extend(chunk)
                        if len(content) > 256 * 1024:
                            raise LLMFailure("llm_response_too_large")
                    data = json.loads(content)
                choice = data["choices"][0]
                if choice.get("finish_reason") != "stop":
                    raise LLMFailure("llm_incomplete_response")
                body = choice["message"].get("content")
                if not isinstance(body, str) or not body.strip():
                    raise LLMFailure("llm_empty_response")
                parsed = output_type.model_validate_json(body)
                validator(parsed)
                response_usage = data.get("usage")
                response_usage = (
                    response_usage if isinstance(response_usage, dict) else {}
                )
                usage = {
                    name: response_usage.get(name)
                    for name in (
                        "prompt_tokens",
                        "completion_tokens",
                        "total_tokens",
                    )
                }
                return parsed, usage
            finally:
                if self.http_client is None:
                    client.close()

        try:
            parsed, usage = measured_call("deepseek", task, "llm_request", send)
        except RequestBudgetExceeded as exc:
            raise LLMFailure("llm_request_budget_exceeded") from exc
        except LLMFailure:
            raise
        except httpx.TimeoutException as exc:
            raise LLMFailure("llm_timeout") from exc
        except httpx.HTTPError as exc:
            raise LLMFailure("llm_network_error") from exc
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            # Never put provider response bodies or authentication data in task errors.
            raise LLMFailure("llm_invalid_response") from exc
        try:
            with self._cache() as db:
                db.execute(
                    "INSERT OR REPLACE INTO llm_results VALUES (?,?,?)",
                    (
                        key,
                        parsed.model_dump_json(),
                        datetime.now(UTC).isoformat(),
                    ),
                )
                db.execute(
                    "DELETE FROM llm_results WHERE saved < ?",
                    (
                        (
                            datetime.now(UTC)
                            - timedelta(hours=settings.llm_cache_hours)
                        ).isoformat(),
                    ),
                )
        except (sqlite3.Error, OSError):
            logging.getLogger(__name__).warning("llm_cache_write_unavailable")
        logging.getLogger(__name__).info(
            "llm_call task=%s model=%s usage=%s", task, settings.deepseek_model, usage
        )
        return parsed, {**audit, "cache_hit": False, "usage": usage}
