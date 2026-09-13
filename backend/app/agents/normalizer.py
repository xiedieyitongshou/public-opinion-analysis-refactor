"""Deterministic Normalizer Agent for Day 36."""

from __future__ import annotations

import hashlib
import html
import re
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from pydantic import ValidationError

from app.schemas import NormalizedItem, NormalizeRawItemsInput, NormalizeRawItemsOutput

HTML_TAG_RE = re.compile(r"<[^>]+>")
URL_RE = re.compile(r"https?://\S+|www\.\S+", re.IGNORECASE)
WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
REPEATED_NEWLINES_RE = re.compile(r"\n{3,}")
REPEATED_PUNCT_RE = re.compile(r"([!?！？。,.，、])\1+")
HASHTAG_RE = re.compile(r"#([^#\s]+)#?")
QUESTION_SHELL_RE = re.compile(r"^(如何看待|怎么看待|怎样看待|如何评价|怎么评价|为什么)\s*")
TEMPLATE_PHRASES = (
    "展开全文",
    "责任编辑",
    "免责声明",
    "来源：",
    "原文链接",
)


class NormalizerAgent:
    """Normalize mapped source items into the Day 36 `NormalizedItem` contract."""

    def normalize(self, input_data: NormalizeRawItemsInput) -> NormalizeRawItemsOutput:
        if not input_data.items:
            return NormalizeRawItemsOutput(status="skipped")

        normalized_items: list[NormalizedItem] = []
        errors: list[str] = []

        for index, raw_item in enumerate(input_data.items):
            try:
                normalized_items.append(
                    NormalizedItem.model_validate(
                        normalize_item(raw_item, defaults=input_data.source_defaults)
                    )
                )
            except (TypeError, ValueError, ValidationError) as exc:
                errors.append(f"item[{index}]: {exc}")

        status = "succeeded"
        if errors and normalized_items:
            status = "partial"
        elif errors:
            status = "failed"

        return NormalizeRawItemsOutput(
            status=status,
            normalized_count=len(normalized_items),
            items=normalized_items,
            quality_flags=_collect_quality_flags(normalized_items),
            error_count=len(errors),
            errors=errors,
        )


def normalize_item(
    raw_item: dict[str, Any],
    *,
    defaults: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return one Day 36-normalized item dict.

    The input is expected to be source-mapped already. This function performs
    structural cleanup and does not make raw metrics cross-platform comparable.
    """

    item = {**(defaults or {}), **raw_item}
    flags = _dedupe([*(item.get("quality_flags") or [])])
    raw_payload = _as_dict(item.get("raw_payload"))
    raw_metrics = _as_dict(item.get("raw_metrics"))
    normalized = _as_dict(item.get("normalized"))

    original_title = item.get("title")
    title = _clean_title(original_title)
    if original_title is not None:
        raw_payload.setdefault("raw_title", original_title)
    if not title:
        flags.append("missing_title")

    url = _clean_url(_first_present(item, "canonical_url", "url", "link"))
    if not url:
        flags.append("missing_url")

    summary = _clean_body(_first_present(item, "summary", "description"))
    if not summary:
        flags.append("missing_summary")

    content_text = _clean_body(_first_present(item, "content_text", "content"))
    if not content_text:
        flags.append("missing_content_text")

    fetched_at, fetched_flag = _parse_datetime(item.get("fetched_at"))
    if fetched_at is None:
        fetched_at = datetime.now(UTC)
        flags.append(fetched_flag or "missing_fetched_at")

    published_at, published_flag = _parse_datetime(item.get("published_at"))
    if published_at is None:
        flags.append(published_flag or "missing_published_at")

    if not raw_metrics:
        flags.append("no_raw_metrics")

    flags = _dedupe(flags)
    content_hash = _build_content_hash(
        item=item,
        raw_payload=raw_payload,
        source_id=str(item.get("source_id") or ""),
        title=title,
        canonical_url=url,
        published_at=published_at,
        fetched_at=fetched_at,
    )
    event_text_for_match = _build_event_text_for_match(
        item=item,
        title=title,
        summary=summary,
        content_text=content_text,
        published_at=published_at,
        fetched_at=fetched_at,
    )
    event_text_for_embedding = _build_event_text_for_embedding(event_text_for_match)

    return {
        **item,
        "title": title,
        "url": url,
        "summary": summary,
        "content_text": content_text,
        "published_at": published_at,
        "fetched_at": fetched_at,
        "raw_metrics": raw_metrics,
        "raw_payload": raw_payload,
        "normalized": normalized,
        "quality_flags": flags,
        "content_hash": content_hash,
        "event_text_for_match": event_text_for_match,
        "event_text_for_embedding": event_text_for_embedding,
    }


def _clean_title(value: Any) -> str | None:
    text = _strip_html(value)
    if text is None:
        return None
    text = WHITESPACE_RE.sub(" ", text).strip()
    return text or None


def _clean_body(value: Any) -> str | None:
    text = _strip_html(value)
    if text is None:
        return None
    text = URL_RE.sub(" ", text)
    for phrase in TEMPLATE_PHRASES:
        text = text.replace(phrase, " ")
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = "\n".join(WHITESPACE_RE.sub(" ", line).strip() for line in text.split("\n"))
    text = REPEATED_NEWLINES_RE.sub("\n\n", text).strip()
    return text or None


def _strip_html(value: Any) -> str | None:
    if value is None:
        return None
    text = html.unescape(str(value))
    text = HTML_TAG_RE.sub(" ", text)
    return text.replace("\xa0", " ")


def _clean_url(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _parse_datetime(value: Any) -> tuple[datetime | None, str | None]:
    if value in (None, ""):
        return None, None
    if isinstance(value, datetime):
        return _ensure_aware(value), None
    if isinstance(value, int | float):
        try:
            return datetime.fromtimestamp(value, UTC), None
        except (OSError, OverflowError, ValueError):
            return None, "invalid_published_at"

    text = str(value).strip()
    if not text:
        return None, None
    try:
        return _ensure_aware(datetime.fromisoformat(text.replace("Z", "+00:00"))), None
    except ValueError:
        pass
    try:
        return _ensure_aware(parsedate_to_datetime(text)), None
    except (TypeError, ValueError, IndexError, OverflowError):
        return None, "invalid_published_at"


def _ensure_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.tzinfo.utcoffset(value) is None:
        return value.replace(tzinfo=UTC)
    return value


def _build_content_hash(
    *,
    item: dict[str, Any],
    raw_payload: dict[str, Any],
    source_id: str,
    title: str | None,
    canonical_url: str | None,
    published_at: datetime | None,
    fetched_at: datetime,
) -> str:
    external_id = _first_present(item, "external_id", "guid", "id", "status_id", "mid")
    if external_id is None:
        external_id = _first_present(raw_payload, "external_id", "guid", "id", "status_id", "mid")
    if external_id:
        identity = f"{source_id}|external|{external_id}"
    elif canonical_url:
        identity = f"{source_id}|url|{canonical_url}"
    elif title and published_at:
        identity = f"{source_id}|title_date|{title}|{published_at.date().isoformat()}"
    else:
        identity = f"{source_id}|title_fetched|{title or ''}|{fetched_at.isoformat()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _build_event_text_for_match(
    *,
    item: dict[str, Any],
    title: str | None,
    summary: str | None,
    content_text: str | None,
    published_at: datetime | None,
    fetched_at: datetime,
) -> str:
    topics = _extract_topics(item)
    content_snippet = (content_text or "")[:500]
    timestamp = published_at or fetched_at
    return _join_text_parts(
        [
            title,
            summary,
            " ".join(topics),
            content_snippet,
            timestamp.date().isoformat(),
        ]
    )


def _build_event_text_for_embedding(value: str) -> str:
    text = URL_RE.sub(" ", value)
    text = HASHTAG_RE.sub(r"\1", text)
    text = QUESTION_SHELL_RE.sub("", text)
    text = REPEATED_PUNCT_RE.sub(r"\1", text)
    for phrase in TEMPLATE_PHRASES:
        text = text.replace(phrase, " ")
    return WHITESPACE_RE.sub(" ", text).strip()


def _extract_topics(item: dict[str, Any]) -> list[str]:
    candidates: list[Any] = []
    for container in (item, _as_dict(item.get("normalized")), _as_dict(item.get("raw_payload"))):
        for key in ("topic", "topics", "topic_words", "hashtags", "query", "keyword", "keywords"):
            value = container.get(key)
            if isinstance(value, list | tuple | set):
                candidates.extend(value)
            elif value:
                candidates.append(value)
    return _dedupe([str(value).strip("# ") for value in candidates if str(value).strip("# ")])


def _join_text_parts(parts: list[Any]) -> str:
    return WHITESPACE_RE.sub(" ", " ".join(str(part).strip() for part in parts if part)).strip()


def _first_present(container: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        value = container.get(key)
        if value not in (None, ""):
            return value
    return None


def _as_dict(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _dedupe(values: list[str]) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value and value not in seen:
            seen.add(value)
            result.append(value)
    return result


def _collect_quality_flags(items: list[NormalizedItem]) -> list[str]:
    flags: list[str] = []
    for item in items:
        flags.extend(item.quality_flags)
    return _dedupe(flags)
