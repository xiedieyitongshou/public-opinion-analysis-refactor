"""Content-stable material and reversible evidence aliases; never rewrite source text."""

import re
from copy import deepcopy
from datetime import datetime
from urllib.parse import urlsplit, urlunsplit
from zoneinfo import ZoneInfo

RELATIVE_TIME = re.compile(
    r"今天|昨天|昨日|今日|明天|前天|本周|上周|这周|今年|去年|本月|上月|刚刚|小时前|分钟前"
)


def canonical_documents(documents, *, fetched_at=None):
    """Remove observation-only time and alias IDs; preserve publication and body changes."""
    docs, aliases = [], {}
    for index, original in enumerate(documents):
        key = f"document:{index}"
        aliases[key] = original["source_id"]
        doc = {k: deepcopy(v) for k, v in original.items() if k != "fetched_at"}
        doc["source_id"] = key
        if fetched_at and RELATIVE_TIME.search(doc.get("title", "") + doc.get("body", "")):
            stamp = (
                fetched_at
                if isinstance(fetched_at, datetime)
                else datetime.fromisoformat(str(fetched_at).replace("Z", "+00:00"))
            )
            doc["reference_date"] = stamp.astimezone(ZoneInfo("Asia/Shanghai")).date().isoformat()
        docs.append(doc)
    return docs, aliases


def remap_result(result, aliases):
    """Rebind only typed identifier fields, leaving quotes, values and reasons intact."""

    def visit(value):
        if isinstance(value, dict):
            return {
                key: aliases.get(item, item)
                if key in {"source_id", "candidate_id"} and isinstance(item, str)
                else visit(item)
                for key, item in value.items()
            }
        if isinstance(value, list):
            return [visit(item) for item in value]
        return value

    return type(result).model_validate(visit(result.model_dump(mode="json")))


def canonical_pair(incoming, candidate):
    # "incoming" and "candidate" refer to the two sides, not database identities.
    left = {**incoming, "source_id": "incoming"}
    right = {**candidate, "source_id": "candidate"}
    return {"incoming": left, "candidate": right}


def source_url_match(source_refs, candidate_urls):
    """Only a concrete source URL identifies a topic; path/query case is significant."""

    def key(raw):
        try:
            parsed = urlsplit((raw or "").strip())
        except ValueError:
            return None
        if parsed.scheme.lower() not in {"http", "https"} or not parsed.hostname:
            return None
        path = parsed.path.rstrip("/")
        if not path or (path in {"/search", "/weibo"} and not parsed.query):
            return None
        return urlunsplit((parsed.scheme.lower(), parsed.netloc.lower(), path, parsed.query, ""))

    left = {key(ref.url) for ref in source_refs} - {None}
    right = {key(url) for url in candidate_urls} - {None}
    return bool(left & right)
