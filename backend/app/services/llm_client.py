"""Bounded DeepSeek JSON calls. No I/O until an explicitly selected LLM operation."""

import hashlib
import json
import logging
import sqlite3
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from threading import BoundedSemaphore, RLock
from urllib.parse import urlsplit
from uuid import uuid4

import httpx
from pydantic import ValidationError

from app.core.config import settings
from app.schemas.llm import PROMPT_VERSION

_mode = ContextVar("event_reasoning_mode", default=None)
_client = ContextVar("event_llm_client", default=None)
_extraction_mode = ContextVar("event_extraction_mode", default=None)
_weibo_mode = ContextVar("weibo_cli_relevance_mode", default=None)


_cache_only = ContextVar("llm_cache_only", default=False)


class LLMFailure(ValueError):
    """Sanitized, observable failure; never triggers the legacy reasoning backend."""


class LLMBudgetExceeded(LLMFailure):
    """Stop the current phase without creating a semantic review or automatic retry."""


@contextmanager
def cache_only():
    token = _cache_only.set(True)
    try:
        yield
    finally:
        _cache_only.reset(token)


def reasoning_mode():
    return _mode.get() or settings.event_reasoning_mode


def extraction_mode():
    selected = _extraction_mode.get() or settings.event_extraction_mode
    return reasoning_mode() if selected == "inherit" else selected


def weibo_relevance_mode():
    selected = _weibo_mode.get() or settings.weibo_cli_relevance_mode
    return reasoning_mode() if selected == "inherit" else selected


@contextmanager
def reasoning_scope(mode, *, extraction_mode=None, weibo_cli_relevance_mode=None):
    if mode not in {"rules", "llm"}:
        raise ValueError("unknown event reasoning mode")
    mode_token = _mode.set(mode)
    extraction_token = _extraction_mode.set(extraction_mode)
    weibo_token = _weibo_mode.set(weibo_cli_relevance_mode)
    needs_llm = (
        mode == "llm"
        or (extraction_mode or settings.event_extraction_mode) == "llm"
        or (weibo_cli_relevance_mode or settings.weibo_cli_relevance_mode) == "llm"
    )
    client_token = _client.set(DeepSeekJSONClient() if needs_llm else None)
    try:
        yield
    finally:
        _extraction_mode.reset(extraction_token)
        _weibo_mode.reset(weibo_token)
        _client.reset(client_token)
        _mode.reset(mode_token)


def get_llm_client():
    return _client.get() or DeepSeekJSONClient()


def require_llm_configuration():
    if "llm" in {reasoning_mode(), extraction_mode(), weibo_relevance_mode()}:
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


@lru_cache(maxsize=4)
def request_slots(limit):
    # Shared by clients in this worker; waiting requests have not reserved API quota.
    return BoundedSemaphore(limit)


class DeepSeekJSONClient:
    def __init__(self, *, http_client=None, cache_path=None, run_id=None):
        self.http_client = http_client
        self.calls = 0
        self._call_lock = RLock()
        self._slots = request_slots(settings.llm_max_concurrent_requests)
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
        from app.services.llm_budget import RunTokenBudget
        from app.services.request_usage import current_request_scope

        scope = current_request_scope()
        self.run_id = run_id or (scope.run_id if scope else "isolated:" + uuid4().hex)
        self.budget = RunTokenBudget(self.cache_path, self.run_id)

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

    def cache_identity(self, task, prompt, payload, output_type):
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
        return key, audit

    def read_cached(self, task, prompt, payload, output_type, validator):
        self.validate_configuration()
        key, audit = self.cache_identity(task, prompt, payload, output_type)
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
        return None

    def store_cached(self, task, prompt, payload, result):
        key, _ = self.cache_identity(task, prompt, payload, type(result))
        try:
            with self._cache() as db:
                db.execute(
                    "INSERT OR REPLACE INTO llm_results VALUES (?,?,?)",
                    (key, result.model_dump_json(), datetime.now(UTC).isoformat()),
                )
                db.execute(
                    "DELETE FROM llm_results WHERE saved < ?",
                    ((datetime.now(UTC) - timedelta(hours=settings.llm_cache_hours)).isoformat(),),
                )
        except (sqlite3.Error, OSError):
            logging.getLogger(__name__).warning("llm_cache_write_unavailable")

    def complete(self, task, prompt, payload, output_type, validator):
        self.validate_configuration()
        cached = self.read_cached(task, prompt, payload, output_type, validator)
        if cached is not None:
            return cached
        if _cache_only.get():
            raise LLMFailure("llm_cache_miss")
        _, audit = self.cache_identity(task, prompt, payload, output_type)
        material = json.dumps(payload, ensure_ascii=False, sort_keys=True, default=str)
        schema = output_type.model_json_schema()
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
                    "content": prompt + "\nJSON Schema:\n" + json.dumps(schema, ensure_ascii=False),
                },
                {"role": "user", "content": material},
            ],
        }

        usage, sent, final_status = {}, False, "failed"

        def send():
            nonlocal usage, sent
            sent = True
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
                response_usage = data.get("usage")
                if isinstance(response_usage, dict):
                    usage = {
                        name: response_usage.get(name)
                        for name in (
                            "prompt_tokens",
                            "completion_tokens",
                            "total_tokens",
                            "prompt_cache_hit_tokens",
                            "prompt_cache_miss_tokens",
                        )
                    }
                choice = data["choices"][0]
                if choice.get("finish_reason") != "stop":
                    raise LLMFailure("llm_incomplete_response")
                body = choice["message"].get("content")
                if not isinstance(body, str) or not body.strip():
                    raise LLMFailure("llm_empty_response")
                parsed = output_type.model_validate_json(body)
                validator(parsed)
                return parsed, usage
            finally:
                if self.http_client is None:
                    client.close()

        # UTF-8 bytes are a conservative token bound, not a billing estimate.
        # Reserve output capacity too; in-flight requests share the same budget.
        estimate = (
            sum(len(message["content"].encode("utf-8")) for message in request["messages"])
            + request["max_tokens"]
            + 256
        )
        key = None
        from app.services.llm_budget import LLMBudgetLimit

        try:
            with self._slots:
                key = self.budget.reserve(
                    task,
                    estimate,
                    token_limit=settings.llm_token_budget_per_run,
                    call_limit=settings.llm_max_calls_per_run,
                )
                with self._call_lock:
                    if self.calls >= settings.llm_max_calls_per_run:
                        raise LLMBudgetExceeded("llm_call_budget_exceeded")
                    self.calls += 1
                parsed, usage = measured_call("deepseek", task, "llm_request", send)
                final_status = "succeeded"
        except LLMBudgetLimit as exc:
            raise LLMBudgetExceeded(str(exc)) from exc
        except RequestBudgetExceeded as exc:
            raise LLMBudgetExceeded("llm_request_budget_exceeded") from exc
        except LLMFailure:
            raise
        except httpx.TimeoutException as exc:
            raise LLMFailure("llm_timeout") from exc
        except httpx.HTTPError as exc:
            raise LLMFailure("llm_network_error") from exc
        except (ValueError, KeyError, IndexError, TypeError, AttributeError) as exc:
            raise LLMFailure("llm_invalid_response") from exc
        finally:
            if key is not None:
                self.budget.finish(key, usage, sent=sent, status=final_status)
                logging.getLogger(__name__).info(
                    "llm_usage run=%s task=%s status=%s usage=%s",
                    self.run_id,
                    task,
                    final_status,
                    usage,
                )
        self.store_cached(task, prompt, payload, parsed)
        logging.getLogger(__name__).info(
            "llm_call task=%s model=%s usage=%s", task, settings.deepseek_model, usage
        )
        return parsed, {**audit, "cache_hit": False, "usage": usage}
