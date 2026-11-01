"""Durable conservative token reservations, shared by resume attempts and request threads."""

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4


class LLMBudgetLimit(ValueError):
    pass


class RunTokenBudget:
    def __init__(self, path, run_id):
        self.path, self.run_id = Path(path), run_id

    @contextmanager
    def connect(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(self.path, timeout=10)
        try:
            with connection as db:
                db.execute("""CREATE TABLE IF NOT EXISTS llm_budget_events (
                    id TEXT PRIMARY KEY, run_id TEXT NOT NULL, task TEXT NOT NULL,
                    reserved INTEGER NOT NULL, charged INTEGER NOT NULL,
                    status TEXT NOT NULL, usage_json TEXT, created TEXT NOT NULL)""")
                db.execute(
                    "CREATE INDEX IF NOT EXISTS ix_llm_budget_run ON llm_budget_events(run_id)"
                )
                yield db
        finally:
            connection.close()

    def reserve(self, task, estimate, *, token_limit, call_limit):
        with self.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            spent, calls = db.execute(
                "SELECT coalesce(sum(charged),0),count(*) FROM llm_budget_events "
                "WHERE run_id=? AND status!='not_sent'",
                (self.run_id,),
            ).fetchone()
            if calls >= call_limit:
                raise LLMBudgetLimit("llm_call_budget_exceeded")
            if spent + estimate > token_limit:
                raise LLMBudgetLimit("llm_token_budget_exceeded")
            key = uuid4().hex
            db.execute(
                "INSERT INTO llm_budget_events VALUES (?,?,?,?,?,?,?,?)",
                (
                    key,
                    self.run_id,
                    task,
                    estimate,
                    estimate,
                    "reserved",
                    None,
                    datetime.now(UTC).isoformat(),
                ),
            )
            return key

    def finish(self, key, usage, *, sent, status):
        # Missing usage after a sent request retains the conservative reservation.
        total = usage.get("total_tokens") if usage else None
        known = isinstance(total, int) and not isinstance(total, bool) and total >= 0
        with self.connect() as db:
            db.execute(
                "UPDATE llm_budget_events SET charged=CASE WHEN ? THEN ? "
                "WHEN ? THEN charged ELSE 0 END,status=?,usage_json=? WHERE id=?",
                (
                    known,
                    total if known else 0,
                    sent,
                    status if sent else "not_sent",
                    json.dumps(usage or {}),
                    key,
                ),
            )

    def summary(self):
        with self.connect() as db:
            rows = db.execute(
                "SELECT task,charged,status,usage_json FROM llm_budget_events WHERE run_id=?",
                (self.run_id,),
            ).fetchall()
        tasks = {}
        for task, charged, status, raw in rows:
            if status == "not_sent":
                continue
            value = tasks.setdefault(
                task, {"requests": 0, "charged_tokens": 0, "reported_tokens": 0, "unknown_usage": 0}
            )
            usage = json.loads(raw or "{}")
            value["requests"] += 1
            value["charged_tokens"] += charged
            total = usage.get("total_tokens")
            if isinstance(total, int) and not isinstance(total, bool) and total >= 0:
                value["reported_tokens"] += total
            else:
                value["unknown_usage"] += 1
        return {
            "run_id": self.run_id,
            "tasks": tasks,
            "charged_tokens": sum(v["charged_tokens"] for v in tasks.values()),
            "reported_tokens": sum(v["reported_tokens"] for v in tasks.values()),
            "requests": sum(v["requests"] for v in tasks.values()),
        }
