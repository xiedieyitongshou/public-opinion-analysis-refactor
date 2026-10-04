"""Publication-based admission for the rolling 24-hour official attention window."""

from datetime import datetime, timedelta
from typing import Any

from app.services.match_documents import as_datetime

WINDOW_HOURS = 24


def official_publication_exclusion(published_at: Any, window_end: datetime) -> str | None:
    """Return why a report is background only; fetching it again never refreshes it."""
    published = as_datetime(published_at)
    if published is None:
        return "official_publication_unknown"
    if published > window_end:
        return "official_publication_in_future"
    if published <= window_end - timedelta(hours=WINDOW_HOURS):
        return "official_publication_expired"
    return None
