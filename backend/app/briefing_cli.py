"""Local MVP setup, bounded observation and export; never approves or emails a report."""

import argparse
import json
import secrets
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models import DailyReport
from app.models.briefing import AnalysisRun
from app.schemas.display import DailyBriefing
from app.services.briefing_jobs import JobCoordinator, queue_analysis
from app.services.briefing_render import render_report, render_text
from app.services.request_usage import usage_summary


def initialize():
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite":
        raise ValueError("Week 8 MVP currently uses SQLite")
    if url.database and url.database != ":memory:":
        Path(url.database).parent.mkdir(parents=True, exist_ok=True)
    init_db()
    if not settings.admin_token:
        target = Path(__file__).resolve().parents[1] / ".env"
        text = target.read_text(encoding="utf-8-sig") if target.exists() else ""
        lines = [line for line in text.splitlines() if not line.strip().startswith("ADMIN_TOKEN=")]
        token = secrets.token_urlsafe(32)
        target.write_text("\n".join([*lines, f'ADMIN_TOKEN="{token}"', ""]), encoding="utf-8")
        print("Admin token written to local .env (not printed). Restart the server after setup.")


def run_one(coordinator):
    with SessionLocal() as db:
        row = queue_analysis(db)
        run_id = row.run_id
    print(json.dumps({"run_id": run_id, "status": "queued"}), flush=True)
    deadline = time.monotonic() + settings.job_timeout_seconds + 180
    while time.monotonic() < deadline:
        coordinator.tick()
        with SessionLocal() as db:
            row = db.get(AnalysisRun, run_id)
            if row.status not in {"queued", "running"}:
                result = {
                    "run_id": run_id,
                    "status": row.status,
                    "report_id": row.report_id,
                    "window_end": row.window_end.isoformat() if row.window_end else None,
                    "error": row.error,
                }
                print(json.dumps(result, ensure_ascii=True), flush=True)
                return result
        time.sleep(2)
    raise TimeoutError("Worker did not complete; inspect /api/jobs before starting another run")


def export_report(report_id, directory):
    directory.mkdir(parents=True, exist_ok=True)
    with SessionLocal() as db:
        report = db.get(DailyReport, report_id)
        if report is None:
            raise ValueError("Unknown report ID")
        briefing = DailyBriefing.model_validate(report.briefing_json)
        (directory / "briefing.json").write_text(
            briefing.model_dump_json(indent=2), encoding="utf-8"
        )
        (directory / "briefing.html").write_text(
            render_report(briefing, email=True, status=report.status), encoding="utf-8"
        )
        (directory / "briefing.txt").write_text(render_text(briefing), encoding="utf-8")
    print(f"Exported version #{report_id} to {directory.resolve()}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("init")
    sub.add_parser("run")
    observe = sub.add_parser("observe")
    observe.add_argument("--hours", type=float, default=24)
    observe.add_argument("--record", type=Path, default=Path("data/briefing-mvp/observation.json"))
    observe.add_argument(
        "--resume", action="store_true", help="Resume this record's original window",
    )
    export = sub.add_parser("export")
    export.add_argument("report_id", type=int)
    export.add_argument("--directory", type=Path, default=Path("data/briefing-mvp/export"))
    sub.add_parser("status")
    args = parser.parse_args()
    if args.command == "init":
        initialize()
        return
    init_db()
    if args.command == "export":
        export_report(args.report_id, args.directory)
        return
    if args.command == "status":
        with SessionLocal() as db:
            runs = db.scalars(select(AnalysisRun).order_by(AnalysisRun.started_at.desc()).limit(12))
            print(
                json.dumps(
                    {
                        "runs": [
                            {"run_id": r.run_id, "status": r.status, "report_id": r.report_id}
                            for r in runs
                        ],
                        "usage": usage_summary(db),
                    },
                    default=str,
                    ensure_ascii=True,
                    indent=2,
                )
            )
        return
    coordinator = JobCoordinator()
    try:
        if args.command == "run":
            run_one(coordinator)
        else:
            if not args.resume and args.record.exists():
                parser.error("Record already exists; use --resume or choose a new --record path")
            previous = json.loads(args.record.read_text(encoding="utf-8")) if args.resume else None
            if previous:
                args.hours = previous["hours_requested"]
            if not 0 < args.hours <= 48:
                parser.error("--hours must be in (0, 48]")
            started = time.monotonic()
            record = previous or {
                "started_at": datetime.now(UTC).isoformat(),
                "hours_requested": args.hours,
                "status": "running",
                "runs": [],
            }
            duration = args.hours * 3600
            interval = settings.sampling_interval_minutes * 60
            elapsed = 0
            if previous:
                original_start = datetime.fromisoformat(record["started_at"])
                spent = (datetime.now(UTC) - original_start).total_seconds()
                if record["status"] == "completed" or spent >= duration:
                    raise ValueError("Observation window ended; inspect it before starting anew")
                started -= max(0, spent)
                elapsed = (
                    min(duration, (int(spent // interval) + 1) * interval) if record["runs"] else 0
                )
                # Reconcile results recorded before a process exit or a draft correction.
                with SessionLocal() as db:
                    for entry in record["runs"]:
                        saved = db.get(AnalysisRun, entry["run_id"])
                        if saved:
                            entry.update(status=saved.status, report_id=saved.report_id,
                                         error=saved.error)
                record["status"] = "running"
                record["resumed_at"] = datetime.now(UTC).isoformat()
                record.pop("error", None)
                record.pop("finished_at", None)
            args.record.parent.mkdir(parents=True, exist_ok=True)
            args.record.write_text(json.dumps(record, indent=2), encoding="utf-8")
            # Includes the boundary round: a 24h observation has samples at 0,3,...,24h.
            while elapsed <= duration:
                record["next_due_at"] = (
                    datetime.fromisoformat(record["started_at"]) + timedelta(seconds=elapsed)
                ).isoformat()
                args.record.write_text(json.dumps(record, indent=2), encoding="utf-8")
                while time.monotonic() < started + elapsed:
                    coordinator.tick()
                    time.sleep(max(0.01, min(5, started + elapsed - time.monotonic())))
                record["runs"].append(run_one(coordinator))
                args.record.write_text(json.dumps(record, indent=2), encoding="utf-8")
                if elapsed == duration:
                    break
                # After sleep/downtime, skip missed slots instead of inventing catch-up history.
                next_slot = (int((time.monotonic() - started) // interval) + 1) * interval
                elapsed = min(duration, max(elapsed + interval, next_slot))
            record.update(status="completed", finished_at=datetime.now(UTC).isoformat())
            record.pop("next_due_at", None)
            args.record.write_text(json.dumps(record, indent=2), encoding="utf-8")
    except (Exception, KeyboardInterrupt) as exc:
        if (args.command == "observe" and "record" in locals()
                and record.get("status") != "completed"):
            record.update(
                status="interrupted" if isinstance(exc, KeyboardInterrupt) else "failed",
                error=type(exc).__name__, finished_at=datetime.now(UTC).isoformat(),
            )
            args.record.write_text(json.dumps(record, indent=2), encoding="utf-8")
        raise
    finally:
        coordinator.stop()


if __name__ == "__main__":
    main()
