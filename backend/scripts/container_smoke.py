"""Offline container smoke: local models + scheduled report to a local SMTP sink only."""

import json
from datetime import timedelta

from app.core.config import settings
from app.db.init_db import init_db
from app.db.session import SessionLocal
from app.models.briefing import AnalysisRun, DailyBriefingJob
from app.schemas.display import DailyBriefing
from app.services.briefing import as_utc, save_briefing
from app.services.daily_schedule import tick_daily
from app.services.runtime_logging import configure_logging
from app.services.semantic_models import get_semantic_models


def main():
    # Guard against accidentally using real SMTP if this script is copied to production.
    assert settings.smtp_host == "mailpit"
    assert settings.email_recipients == ["reader@example.test"]
    logger = configure_logging("smoke")
    init_db()
    models = get_semantic_models()
    assert len(models.encode(["热点日报容器测试"])[0]) == 512
    scores = models.rerank([("热点日报容器测试", "热点日报容器测试")])
    assert scores[0] > 0.5
    # The real analysis fixture is injected by the local verification command, never fetched here.
    from pathlib import Path

    content = json.loads(Path("/app/data/smoke-briefing.json").read_text(encoding="utf-8"))
    briefing = DailyBriefing.model_validate(content)
    # Preserve a previously validated snapshot verbatim; drive the scheduler at its own cutoff.
    now = as_utc(briefing.window_end)
    settings.email_schedule_enabled = True
    settings.email_send_time = "00:00"
    with SessionLocal() as db:
        run_id = briefing.run_id
        if db.get(AnalysisRun, run_id) is None:
            db.add(AnalysisRun(run_id=run_id, status="succeeded", started_at=now,
                               finished_at=now, window_end=now, input_json={}))
            db.commit()
        report = save_briefing(db, briefing)
        row = db.get(AnalysisRun, run_id)
        row.report_id = report.id
        day = report.report_date.isoformat()
        if db.get(DailyBriefingJob, day) is None:
            db.add(DailyBriefingJob(local_date=day, run_id=run_id, status="queued"))
        db.commit()
        first = tick_daily(db, now=now + timedelta(seconds=1))
        second = tick_daily(db, now=now + timedelta(seconds=2))
        assert first.status == second.status == "sent", first.error
        logger.info("container_smoke_passed report=%s", report.id)
        print(json.dumps({"models": "ok", "daily_job": second.status,
                          "report_id": report.id, "rerank_score": scores[0]}))


if __name__ == "__main__":
    main()
