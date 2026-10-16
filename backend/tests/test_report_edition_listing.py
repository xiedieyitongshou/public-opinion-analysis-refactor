"""Edition listing must paginate rounds rather than individual review revisions."""
import unittest
from datetime import date

from sqlalchemy import create_engine, select, func
from sqlalchemy.orm import Session

from app.api.briefing import reports, report_history
from app.db.session import Base
from app.models import DailyReport
from app.models.briefing import ReportRevision


class ReportEditionListingTest(unittest.TestCase):
    def test_many_revisions_preserve_older_rounds_and_history(self):
        engine = create_engine("sqlite://")
        Base.metadata.create_all(engine)
        with Session(engine) as db:
            def add(run, status="draft"):
                row = DailyReport(report_date=date(2026, 10, 16), title="test",
                                  status=status, briefing_json={"event_count": 1})
                db.add(row)
                db.flush()
                if run:
                    db.add(ReportRevision(report_id=row.id, run_id=run,
                                          fingerprint=str(row.id)))
                db.commit()
                return row.id

            legacy = add(None)
            older = add("older-round")
            sent = add("current-round", "sent")
            for _ in range(35):
                latest = add("current-round")
            self.assertEqual([r["id"] for r in reports(db, limit=2)], [latest, older])
            self.assertEqual([r["id"] for r in reports(db, limit=30)], [latest, older, legacy])
            history = report_history(db, latest)
            self.assertEqual(len(history), 35)
            self.assertTrue(all(r["run_id"] == "current-round" for r in history))
            self.assertEqual(next(r for r in history if r["id"] == sent)["status"], "sent")
            self.assertEqual(report_history(db, legacy), [])
            self.assertEqual(db.scalar(select(func.count(DailyReport.id))), 38)
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
