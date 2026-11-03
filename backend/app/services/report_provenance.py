"""Describe saved collection times without mistaking a replay for a new crawl."""

from datetime import datetime

from sqlalchemy import select

from app.models.briefing import AnalysisRun
from app.services.briefing import as_utc


def _time(value):
    if not isinstance(value, str | datetime) or not value:
        return None
    try:
        return as_utc(value)
    except (ValueError, TypeError):
        return None


def collection_provenance(db, report, scope):
    brief = report.briefing_json or {}
    start, end = _time(brief.get("window_start")), _time(brief.get("window_end"))
    # Summary runs retain only the latest result for each source. Read the actual
    # constituent captures instead, including on historical review revisions.
    keys = set(scope.run_ids)
    if len(keys) > 1:
        keys.discard(scope.run_id)
    times, sources, recorded = [], {}, set()
    missing_time = False

    def add_source(value):
        key = value.get("source_id") or value.get("source_name")
        if key:
            sources[key] = {"source_id": value.get("source_id"),
                            "source_name": value.get("source_name") or key}

    for key, collection in db.execute(select(
        AnalysisRun.run_id, AnalysisRun.collection_json,
    ).where(AnalysisRun.run_id.in_(keys))):
        for result in (collection or {}).get("results", []):
            items = result.get("normalized_items") or []
            if not items and not result.get("normalized_count"):
                continue
            observed = _time(result.get("observed_at"))
            stamps = [observed] if observed else [
                stamp for item in items if (stamp := _time(item.get("fetched_at")))
            ]
            eligible = [stamp for stamp in stamps
                        if (start is None or stamp > start) and (end is None or stamp <= end)]
            if stamps and not eligible:
                continue  # The saved capture falls outside this report's window.
            times.extend(eligible)
            missing_time |= not bool(eligible)
            recorded.add(key)
            add_source(result)
            if any((item.get("raw_payload") or {}).get("cli_enrichment", {}).get("samples")
                   for item in items if isinstance(item.get("raw_payload"), dict)
                   and isinstance(item["raw_payload"].get("cli_enrichment"), dict)):
                add_source({"source_id": "weibo_cli", "source_name": "微博 CLI"})

    origin = "collection_records" if recorded else "unrecorded"
    if not recorded:
        # Legacy reports can have frozen citations even after collection records
        # were removed. These are partial evidence, never a guessed 24-hour crawl.
        citations = report.source_citations_json or []
        if not citations:
            citations = [citation for section in brief.get("sections", [])
                         for card in section.get("event_cards", [])
                         for citation in card.get("source_citations", [])]
        for citation in citations:
            add_source(citation)
            if stamp := _time(citation.get("fetched_at")):
                times.append(stamp)
        if times or sources:
            origin = "report_citations"
    return {
        "first_observed_at": min(times) if times else None,
        "last_observed_at": max(times) if times else None,
        "round_count": len(recorded),
        "expected_round_count": len(keys),
        "complete": bool(recorded) and recorded == keys and not missing_time,
        "origin": origin,
        "sources": list(sources.values()),
    }
