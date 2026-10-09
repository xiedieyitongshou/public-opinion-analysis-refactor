"""Replay one recorded classification task in a DB backup, with zero network requests."""

import argparse
import cProfile
import io
import json
import pstats
import sqlite3
from pathlib import Path
from time import perf_counter

import httpx
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.session import engine
from app.models import AgentTask
from app.schemas.analysis import ClassifyEventsInput
from app.services.analysis_interfaces import classify_events
from app.services.runtime_logging import configure_logging


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--task-id", type=int, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--limit", type=int, default=0)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    backup = args.output / "replay.db"
    if not backup.exists():
        source_uri = f"file:{Path(engine.url.database).as_posix()}?mode=ro"
        with sqlite3.connect(source_uri, uri=True) as src:
            with sqlite3.connect(backup) as dst:
                src.backup(dst)
    assert backup.resolve() != Path(engine.url.database).resolve()
    settings.semantic_cache_path = str(args.output / "semantic-cache.db")
    settings.runtime_log_dir = str(args.output / "logs")
    configure_logging("profile")

    def forbidden(*args, **kwargs):
        raise RuntimeError("network_disabled_for_replay")

    httpx.Client.send = forbidden
    bind = create_engine(f"sqlite:///{backup}")
    with Session(bind) as db:
        data = ClassifyEventsInput.model_validate(db.get(AgentTask, args.task_id).input_json)
        data.official_search_budget = 0
        if args.limit:
            data.event_ids = data.event_ids[:args.limit]
        profiler = cProfile.Profile()
        started = perf_counter()
        profiler.enable()
        result = classify_events(db, data)
        profiler.disable()
        summary = {"seconds": round(perf_counter() - started, 3), "events": len(data.event_ids),
                   "profile": data.matching_profile, "network": "disabled"}
        output = args.output / f"task-{args.task_id}-{args.limit}"
        output.with_suffix(".json").write_text(result.model_dump_json(), encoding="utf-8")
        stream = io.StringIO()
        pstats.Stats(profiler, stream=stream).sort_stats("cumulative").print_stats(35)
        output.with_suffix(".txt").write_text(
            json.dumps(summary) + "\n" + stream.getvalue(), encoding="utf-8",
        )
        print(json.dumps(summary), flush=True)
    bind.dispose()


if __name__ == "__main__":
    main()
