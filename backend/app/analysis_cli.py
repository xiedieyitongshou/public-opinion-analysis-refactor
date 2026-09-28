"""Run one tool-driven analysis round: python -m app.analysis_cli --help."""

import argparse
import json
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from uuid import uuid4

from sqlalchemy import create_engine
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session

from app.agents.analysis import HotspotAnalysisAgent
from app.core.config import settings
from app.db.init_db import init_db
from app.schemas.analysis import HotspotAnalysisInput
from app.schemas.collectors import FetchSourceItemsInput, FetchSourceItemsOutput
from app.schemas.platform_trend import PlatformTrendConfig
from app.tools import ToolRegistry, default_tool_registry


def main() -> int:
    parser = argparse.ArgumentParser(description="Collect, resolve events and analyze one round")
    parser.add_argument("--database", help="SQLAlchemy database URL")
    parser.add_argument("--run-id", help="Reuse an ID to replay its recorded collection")
    parser.add_argument("--sources", nargs="+")
    parser.add_argument("--limit", type=int, default=20)
    parser.add_argument("--window-end", help="ISO 8601 timestamp including timezone")
    parser.add_argument("--interval-minutes", type=int, help="Actual intended sampling interval")
    parser.add_argument(
        "--replay", type=Path, help="FetchSourceItemsOutput JSON; requires --database"
    )
    args = parser.parse_args()
    registry = default_tool_registry
    replay = None
    if args.replay:
        if not args.database:
            parser.error("--replay requires an explicit isolated --database")
        replay = FetchSourceItemsOutput.model_validate_json(args.replay.read_text(encoding="utf-8"))
        if replay.validate_only or replay.dry_run or not replay.run_id:
            parser.error("Replay needs an analysis-mode round with run_id and observation metadata")
        if args.run_id and args.run_id != replay.run_id:
            parser.error("--run-id must match the replay round")
        registry = ToolRegistry()
        for tool in default_tool_registry.list_tools():
            if tool.name == "fetch_source_items":
                tool = replace(tool, handler=lambda data, context: replay.model_copy(deep=True))
            registry.register(tool)
    configs = {}
    if args.interval_minutes:
        configs = {
            platform: PlatformTrendConfig(expected_interval_minutes=args.interval_minutes)
            for platform in ("zhihu", "weibo")
        }
    request = HotspotAnalysisInput(
        run_id=args.run_id or (replay.run_id if replay else str(uuid4())),
        window_end=datetime.fromisoformat(args.window_end) if args.window_end else None,
        trend_configs=configs,
        collection=FetchSourceItemsInput(
            source_ids=replay.requested_source_ids if replay else args.sources,
            limit=args.limit,
            validate_only=False,
            dry_run=False,
            promote_to_event_pool=False,
        ),
    )
    url = args.database or settings.database_url
    parsed = make_url(url)
    if parsed.get_backend_name() == "sqlite" and parsed.database not in {None, "", ":memory:"}:
        Path(parsed.database).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url)
    init_db(engine)
    with Session(engine) as db:
        result = HotspotAnalysisAgent(registry).run(db, request)
        print(json.dumps(result.model_dump(mode="json"), ensure_ascii=False, indent=2))
    engine.dispose()
    return 1 if result.status == "failed" else 0


if __name__ == "__main__":
    raise SystemExit(main())
