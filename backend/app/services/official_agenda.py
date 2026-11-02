"""Independent official news records; never enter community semantic resolution."""

import hashlib
import re
from datetime import timedelta
from urllib.parse import urlsplit

from sqlalchemy import select

from app.models import Event, Item
from app.schemas.analysis import BuildOfficialAgendaOutput
from app.schemas.official_support import (
    OfficialReference,
    OfficialSupportDetail,
    OfficialSupportResult,
)
from app.services.hotspot_freshness import official_publication_exclusion
from app.services.match_documents import as_datetime

PIPELINE_VERSION = "official-independent-v1"


def is_official_agenda_event(event):
    return (event.event_detail_json or {}).get("event_domain") == "official_agenda" or bool(
        event.items and all(i.source.source_type == "official_news" for i in event.items)
    )


def build_official_agenda(db, data):
    """Admit published feed articles using exact article/title identity, without LLMs."""
    output = BuildOfficialAgendaOutput(status="skipped")
    touched = set()
    for item in db.scalars(select(Item).where(Item.id.in_(data.item_ids)).order_by(Item.id)):
        parsed = urlsplit(item.url or "")
        if (
            item.source.source_type != "official_news"
            or item.source_status not in {"use", "fallback"}
            or item.source_origin != "official_rss"
            or not item.title.strip()
            or parsed.scheme not in {"http", "https"}
            or not parsed.netloc
            or official_publication_exclusion(item.published_at, data.window_end)
        ):
            output.excluded_count += 1
            continue
        if item.event is not None and not is_official_agenda_event(item.event):
            # Preserve previously reviewed ownership; historical mixed data needs an
            # explicit isolated rebuild rather than silently moving an accepted item.
            output.errors.append(f"official item {item.id}: legacy mixed event requires rebuild")
            continue
        title_key = re.sub(r"\W+", "", item.title).casefold()
        date_key = as_datetime(item.published_at).date().isoformat()
        digest = hashlib.sha256(f"{date_key}:{title_key}".encode()).hexdigest()
        fingerprint = f"official-agenda:{digest}"
        event = item.event or db.scalar(select(Event).where(Event.event_fingerprint == fingerprint))
        observed = as_datetime(item.fetched_at)
        if event is None:
            event = Event(
                event_id="official-" + digest[:24],
                event_fingerprint=fingerprint,
                title=item.title,
                event_type="official_agenda",
                lifecycle_status="active",
                first_seen_at=observed,
                last_seen_at=observed,
                keywords_json=[],
                source_citations_json=[],
                event_detail_json={},
            )
            db.add(event)
            db.flush()
            output.created_count += 1
        else:
            output.reused_count += 1
        item.event_id = event.id
        event.last_seen_at = max(as_datetime(event.last_seen_at) or observed, observed)
        event.event_detail_json = {
            **(event.event_detail_json or {}),
            "event_domain": "official_agenda",
            "official_pipeline_version": PIPELINE_VERSION,
            "official_time_basis": "published_at",
            "official_list_kind": "rss_news_list",
        }
        citations = {str(c.get("item_id")): c for c in event.source_citations_json or []}
        citations[str(item.id)] = item.source_citation_json
        event.source_citations_json = list(citations.values())
        touched.add(event.event_id)
    db.commit()
    output.event_ids = sorted(touched)
    output.status = "partial" if output.errors else "succeeded" if touched else "skipped"
    return output


def agenda_support(event, window_end, window_start=None):
    """An official article proves its own provenance; no semantic self-comparison."""
    references = [
        OfficialReference(
            item_id=str(item.id),
            title=item.title,
            url=item.url,
            source_name=item.source.name,
            source_status=item.source_status,
            published_at=as_datetime(item.published_at).isoformat(),
            match_reason="official agenda article identity; not a community merge",
            match_features={"official_article_identity": True},
        )
        for item in event.items
        if item.source.source_type == "official_news"
        and item.source_status in {"use", "fallback"}
        and item.url
        and official_publication_exclusion(item.published_at, window_end, window_start) is None
    ]
    sources = sorted({r.source_name for r in references})
    return OfficialSupportResult(
        event_id=event.event_id,
        official_support_status=(
            "supported"
            if any(r.source_status == "use" for r in references)
            else "weak_supported"
            if references
            else "not_found"
        ),
        official_references=references,
        official_support_detail=OfficialSupportDetail(
            official_source_count=len(sources),
            source_coverage_count=len(sources),
            official_item_count=len(references),
            unique_story_count=bool(references),
            authority_sources=sources,
            matched_item_ids=[r.item_id for r in references],
            match_quality="strong" if references else "none",
            attention_window={
                "start": (window_start or window_end - timedelta(hours=24)).isoformat(),
                "end": window_end.isoformat(),
                "basis": "published_at",
            },
        ),
    )
